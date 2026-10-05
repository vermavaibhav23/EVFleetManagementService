"""Kafka-dispatched, leased and cancellable process-isolated solver jobs."""

import asyncio
import multiprocessing
import queue
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException

from app.domain import dt
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


def same_journey(left, right):
    """Ignore solver noise, but preserve different routes, slots and charge amounts."""
    if abs(left["total_cost"] - right["total_cost"]) > 0.01:
        return False
    if len(left["operations"]) != len(right["operations"]):
        return False
    for a, b in zip(left["operations"], right["operations"]):
        if any(a.get(k) != b.get(k) for k in ("kind", "charger_id", "port", "trip_id")):
            return False
        if any(
            abs((dt(a[k]) - dt(b[k])).total_seconds()) > 1
            for k in ("depart", "arrival", "start", "end")
        ):
            return False
        if any(abs(a[k] - b[k]) > 0.01 for k in ("energy_arrival", "energy_end")):
            return False
    return True


def deadline_tradeoff(safe, urgent, reserve):
    """Only offer emergency reserve when it rescues an actual missed deadline.

    Both plans have already passed physical feasibility validation. Taking more
    battery risk for the same delivery outcome is not a useful manager choice.
    """
    safe_deliveries = {
        o["trip_id"]: o for o in safe["operations"] if o["kind"] == "DELIVERY"
    }
    urgent_deliveries = {
        o["trip_id"]: o for o in urgent["operations"] if o["kind"] == "DELIVERY"
    }
    if safe_deliveries.keys() != urgent_deliveries.keys():
        return []
    saved = []
    for trip_id, a in safe_deliveries.items():
        b = urgent_deliveries[trip_id]
        before, after = a.get("lateness_minutes", 0), b.get("lateness_minutes", 0)
        if after > before + 1 / 60:
            return []
        if before > 1 / 60 and after <= 0.001:
            saved.append(a["name"])
    # Exclude charging in place at the starting point: an already-low battery
    # is not a new risk created by choosing this journey.
    if not saved or not any(
        o["energy_arrival"] < reserve - 0.01
        for i, o in enumerate(urgent["operations"])
        if not (
            i == 0 and o["kind"] == "CHARGE" and dt(o["arrival"]) == dt(o["depart"])
        )
    ):
        return []
    return saved


def compare_options(doc, vin, alternatives=True):
    """Compare reserve-preserving and deadline-priority journeys on one snapshot.

    Reduced reserve is searched only when the normal constraints cannot both
    be satisfied. It never relaxes physical energy, health, ports or power.
    """
    results = []

    def add(result, goal):
        result.update(vin=vin, comparison_goal=goal)
        plan = result.get("plan")
        if plan:
            plan["comparison_goal"] = goal

            if any(same_journey(r["plan"], plan) for r in results if "plan" in r):
                return
        results.append(result)

    normal = optimize(doc, vin)
    if "plan" in normal:
        add(normal, "PROTECT_RESERVE")
        if alternatives and normal["plan"]["total_cost"] > 0:
            earlier = optimize(doc, vin, fastest=True)
            if "plan" not in earlier:
                add(earlier, "EARLIER_RETURN")
            elif (
                dt(normal["plan"]["operations"][-1]["end"])
                - dt(earlier["plan"]["operations"][-1]["end"])
            ).total_seconds() >= 60:
                add(earlier, "EARLIER_RETURN")
    else:
        # Keep the unavailable strict option visible with its failure reason.
        add(normal, "ON_TIME_WITH_RESERVE")
        if normal["status"] == "ERROR":
            return results
        safe = optimize(doc, vin, recovery=True)
        add(safe, "PROTECT_RESERVE")
        urgent = optimize(doc, vin, recovery=True, reserve_exception=True)
        if "plan" not in urgent:
            add(urgent, "PROTECT_DEADLINES")
        elif "plan" in safe:
            saved = deadline_tradeoff(
                safe["plan"], urgent["plan"], doc["policy"]["reserve_kwh"]
            )
            if saved:
                urgent["plan"]["deadlines_saved"] = saved
                add(urgent, "PROTECT_DEADLINES")
        else:
            # If reserve protection is impossible, a physically feasible escape
            # remains reviewable, but it is not a two-way deadline trade-off.
            add(urgent, "REDUCED_RESERVE_ALTERNATIVE")
    return results


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
            if req.get("compare_tradeoffs"):
                options = compare_options(working, vin, req["alternatives"])
                results.extend(options)
                selected = next((r["plan"] for r in options if "plan" in r), None)
                if selected:
                    plan = deepcopy(selected)
                    plan["status"] = "APPROVED"
                    working["plans"][plan["plan_id"]] = plan
                continue
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
                    if (
                        "plan" in alt
                        and not same_journey(plan, alt["plan"])
                        and (
                            dt(plan["operations"][-1]["end"])
                            - dt(alt["plan"]["operations"][-1]["end"])
                        ).total_seconds()
                        >= 60
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
        self.process = None

    async def stop(self):
        if self.process and self.process.is_alive():
            self.process.terminate()
            await asyncio.to_thread(self.process.join, 2)

    async def handle(self, message):
        """Kafka delivery wakes a job; PostgreSQL fences duplicate ownership."""
        old = await self.ledger.read()
        if old["run_id"] != message["run_id"]:
            return
        doc = deepcopy(old)
        job = doc["jobs"].get(message["job_id"])
        if not job or job["status"] in ("COMPLETED", "ERROR", "CANCELLED"):
            return
        wall = datetime.now(UTC)
        if job["status"] == "RUNNING" and dt(job["lease_until"]) > wall:
            # Do not acknowledge an unfinished job owned by another worker.
            raise HTTPException(409, "Planning job is already leased")
        from app.core.config import settings
        from app.services.simulator import fresh

        vins = (
            [job["vin"]]
            if job["vin"]
            else [
                vin
                for vin, v in doc["vehicles"].items()
                if not v.get("plan_id") and v["state"] != "COMPLETED"
            ]
        )
        if any(
            not fresh(doc, doc["vehicles"][vin], settings.telemetry_max_age_seconds)
            for vin in vins
        ):
            raise HTTPException(409, "Waiting for fresh vehicle readings")
        job.update(
            status="RUNNING",
            owner=self.owner,
            lease_until=wall
            + timedelta(
                seconds=doc["policy"]["solver_seconds"] * max(4, 3 * len(vins)) + 90
            ),
        )
        snapshot = await self.ledger.commit(old, doc)
        await self.execute(snapshot, job)
        current = await self.ledger.read()
        active = current["jobs"].get(job["job_id"])
        if (
            current["run_id"] == snapshot["run_id"]
            and active
            and active["status"] == "RUNNING"
        ):
            raise HTTPException(
                409, "Result not committed; keep the planning request pending"
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
                stale_vins = {
                    row["vin"] for row in stored["results"] if row["status"] == "STALE"
                }
                usable_vins = {
                    row["vin"] for row in stored["results"] if row.get("plan_id")
                }
                for vin in stale_vins - usable_vins:
                    # A solve invalidated only by elapsed time still needs a fresh
                    # paused search, even if the parked vehicle has not moved.
                    doc["vehicles"][vin].pop("auto_plan_marker", None)
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
