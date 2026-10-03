"""Leased simulated clock and cancellable, process-isolated solver jobs."""

import asyncio
import multiprocessing
import queue
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException

from app.domain import dt
from app.services.execution import advance
from app.services.ledger import Ledger
from app.services.optimizer import fingerprint, leg, optimize
from app.services.validation import validate


def priority(doc, vin):
    v = doc["vehicles"][vin]
    reserve = doc["policy"]["reserve_kwh"]
    reachable = sum(
        s["status"] == "AVAILABLE"
        and s["connector"] == v["connector"]
        and (
            leg(doc, v, v, s)[0] < 1e-9
            or leg(doc, v, v, s)[0] + reserve <= v["energy_kwh"]
        )
        for s in doc["stations"].values()
    )
    deadlines = [dt(d["deadline"]) for d in v["deliveries"] if d["status"] == "PLANNED"]
    return (
        reachable,
        min(deadlines, default=dt(doc["clock"]) + timedelta(days=1)),
        v["energy_kwh"],
        vin,
    )


def solve_job(snapshot, job, output):
    try:
        working = deepcopy(snapshot)
        results = []
        vins = (
            [job["vin"]]
            if job["vin"]
            else sorted(
                [
                    vin
                    for vin, v in working["vehicles"].items()
                    if not v.get("plan_id") and v["state"] != "COMPLETED"
                ],
                key=lambda vin: priority(working, vin),
            )
        )
        req = job["request"]
        for vin in vins:
            result = optimize(working, vin, req["recovery"], req["reserve_exception"])
            result["vin"] = vin
            results.append(result)
            if "plan" in result:
                plan = deepcopy(result["plan"])
                if job["vin"] and req["alternatives"] and plan["total_cost"] > 0:
                    alt = optimize(
                        working,
                        vin,
                        req["recovery"],
                        req["reserve_exception"],
                        fastest=True,
                    )
                    if "plan" in alt and (
                        abs(alt["plan"]["total_cost"] - plan["total_cost"]) > 0.01
                        or abs(
                            (
                                dt(alt["plan"]["operations"][-1]["end"])
                                - dt(plan["operations"][-1]["end"])
                            ).total_seconds()
                        )
                        > 1
                    ):
                        alt["vin"] = vin
                        results.append(alt)
                # Synthetic reservations coordinate subsequent vehicles in the batch.
                plan["status"] = "APPROVED"
                working["plans"][plan["plan_id"]] = plan
        output.put(
            {
                "results": results,
                "coordination": "constrained-first sequential allocation; no fleet-wide optimality claim",
            }
        )
    except Exception as exc:  # noqa: BLE001 - process boundary reports solver errors
        output.put({"error": f"{type(exc).__name__}: {exc}"})


class Runner:
    def __init__(self, db):
        self.ledger = Ledger(db)
        self.owner = str(uuid4())
        self.task = None
        self.worker = None
        self.process = None

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        for task in (self.task, self.worker):
            if task:
                task.cancel()
        if self.process and self.process.is_alive():
            self.process.terminate()
        for task in (self.task, self.worker):
            if task:
                with suppress(asyncio.CancelledError):
                    await task

    async def loop(self):
        while True:
            await asyncio.sleep(1)
            try:
                old = await self.ledger.read()
                doc = deepcopy(old)
                wall = datetime.now(UTC)
                lease = doc.get("lease")
                if lease and lease["owner"] != self.owner and dt(lease["until"]) > wall:
                    continue
                doc["lease"] = {
                    "owner": self.owner,
                    "until": wall + timedelta(seconds=10),
                }
                if doc["running"]:
                    advance(doc, doc["speed"])
                # A worker crash is retried from persisted current facts, never from a half-written chain.
                for job in doc["jobs"].values():
                    if job["status"] == "RUNNING" and (
                        job.get("owner") != self.owner
                        or self.worker is None
                        or self.worker.done()
                    ):
                        job["status"] = "QUEUED"
                job = next(
                    (j for j in doc["jobs"].values() if j["status"] == "QUEUED"), None
                )
                if job and (not self.worker or self.worker.done()):
                    job.update(
                        status="RUNNING",
                        owner=self.owner,
                        lease_until=wall
                        + timedelta(
                            seconds=doc["policy"]["solver_seconds"]
                            * max(2, len(doc["vehicles"]))
                            + 90
                        ),
                    )
                else:
                    job = None
                committed = await self.ledger.commit(old, doc)
                if job:
                    self.worker = asyncio.create_task(self.execute(committed, job))
            except HTTPException:
                continue
            except Exception:
                import logging

                logging.exception(
                    "Simulation runner failed; persisted state remains authoritative"
                )

    async def execute(self, snapshot, job):
        context = multiprocessing.get_context("spawn")
        output = context.Queue()
        process = context.Process(
            target=solve_job, args=(snapshot, job, output), daemon=True
        )
        self.process = process
        process.start()
        result = None
        try:
            while result is None:
                await asyncio.sleep(0.2)
                current = await self.ledger.read()
                active = current["jobs"].get(job["job_id"])
                if (
                    current["run_id"] != snapshot["run_id"]
                    or not active
                    or active["status"] != "RUNNING"
                    or active.get("owner") != self.owner
                ):
                    return
                if datetime.now(UTC) > dt(active["lease_until"]):
                    result = {"error": "Solver job exceeded wall-time limit"}
                    break
                try:
                    result = output.get_nowait()
                except queue.Empty:
                    if not process.is_alive():
                        result = {"error": "Solver worker exited without a result"}
            for _ in range(5):
                old = await self.ledger.read()
                if old["run_id"] != snapshot["run_id"]:
                    return
                doc = deepcopy(old)
                stored = doc["jobs"].get(job["job_id"])
                if (
                    not stored
                    or stored["status"] != "RUNNING"
                    or stored.get("owner") != self.owner
                ):
                    return
                stored.update(
                    status="ERROR" if "error" in result else "COMPLETED",
                    completed_at=datetime.now(UTC),
                    results=[],
                )
                if "error" in result:
                    stored["error"] = result["error"]
                for row in result.get("results", []):
                    plan = row.get("plan")
                    summary = {k: v for k, v in row.items() if k != "plan"}
                    if plan:
                        errors = validate(doc, plan)
                        if (
                            fingerprint(doc, plan["vin"]) != plan["fingerprint"]
                            or errors
                        ):
                            summary.update(
                                status="STALE",
                                reason="State changed during solving; request a fresh plan",
                                errors=errors,
                            )
                        else:
                            for existing in doc["plans"].values():
                                if (
                                    existing["vin"] == plan["vin"]
                                    and existing["status"] == "PROPOSED"
                                    and existing.get("job_id") != job["job_id"]
                                ):
                                    existing["status"] = "SUPERSEDED"
                            plan["job_id"] = job["job_id"]
                            doc["plans"][plan["plan_id"]] = plan
                            summary["plan_id"] = plan["plan_id"]
                    stored["results"].append(summary)
                try:
                    await self.ledger.commit(old, doc)
                    return
                except HTTPException:
                    await asyncio.sleep(0.1)
        finally:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 2)
            output.close()
            self.process = None
