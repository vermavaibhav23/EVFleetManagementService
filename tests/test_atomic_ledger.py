"""Real standalone MongoDB tests, including separate OS processes.

Run with FLEET_TEST_MONGO=mongodb://127.0.0.1:27028. Each test uses a unique DB.
"""

import asyncio
import multiprocessing
import os
from copy import deepcopy
from datetime import UTC
from uuid import uuid4

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from app.domain import Approval, LoadRequest
from app.services.control import approve
from app.services.ledger import Ledger, bookings
from app.services.optimizer import optimize
from app.services.seed import seed
from tests.test_v2 import fixture

URI = os.getenv("FLEET_TEST_MONGO")
pytestmark = pytest.mark.skipif(
    not URI, reason="FLEET_TEST_MONGO must point to an isolated test MongoDB"
)


def process_approval(uri, database, plan_id, barrier, output, crash=False):
    async def run():
        client = AsyncIOMotorClient(uri, tz_aware=True, tzinfo=UTC)
        store = Ledger(client[database])
        old = await store.read()
        new = deepcopy(old)
        approve(new, plan_id, Approval(run_id=old["run_id"]))
        if barrier:
            barrier.wait(timeout=30)
        try:
            await store.commit(old, new)
            if crash:
                os._exit(0)  # lost HTTP response after durable commit
            output.put("committed")
        except HTTPException:
            output.put("conflict")
        finally:
            client.close()

    asyncio.run(run())


def test_cross_process_chain_conflict_is_atomic():
    async def run():
        client = AsyncIOMotorClient(URI, tz_aware=True, tzinfo=UTC)
        name = "fleet_test_" + uuid4().hex
        store = Ledger(client[name])
        try:
            doc = fixture()
            doc["vehicles"]["OTHER"] = deepcopy(doc["vehicles"]["SIM-001"])
            doc["vehicles"]["OTHER"]["vin"] = "OTHER"
            p1 = optimize(doc, "SIM-001")["plan"]
            p2 = optimize(doc, "OTHER")["plan"]
            doc["plans"] = {p["plan_id"]: p for p in (p1, p2)}
            await store.replace_run(doc)
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            output = context.Queue()
            processes = [
                context.Process(
                    target=process_approval,
                    args=(URI, name, p["plan_id"], barrier, output),
                )
                for p in (p1, p2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                await asyncio.to_thread(process.join, 40)
                assert process.exitcode == 0
            assert sorted([output.get(timeout=5), output.get(timeout=5)]) == [
                "committed",
                "conflict",
            ]
            stored = await store.read()
            rows = bookings(stored)
            assert len(rows) == 2 and len({r["vin"] for r in rows}) == 1
            assert sum(p["status"] == "APPROVED" for p in stored["plans"].values()) == 1
        finally:
            await client.drop_database(name)
            client.close()

    asyncio.run(run())


def test_process_crash_after_commit_retry_and_reset_fence():
    async def run():
        client = AsyncIOMotorClient(URI, tz_aware=True, tzinfo=UTC)
        name = "fleet_test_" + uuid4().hex
        store = Ledger(client[name])
        try:
            doc = fixture()
            p = optimize(doc, "SIM-001")["plan"]
            doc["plans"][p["plan_id"]] = p
            await store.replace_run(doc)
            context = multiprocessing.get_context("spawn")
            process = context.Process(
                target=process_approval,
                args=(URI, name, p["plan_id"], None, None, True),
            )
            process.start()
            await asyncio.to_thread(process.join, 30)
            assert process.exitcode == 0
            response = await store.mutate(
                lambda d: approve(d, p["plan_id"], Approval(run_id=doc["run_id"])),
                doc["run_id"],
            )
            assert response["idempotent"] and len(bookings(await store.read())) == 2
            old = await store.read()
            await store.replace_run(seed(LoadRequest(vehicle_count=1)))
            with pytest.raises(HTTPException):
                await store.commit(old, deepcopy(old))
            assert not bookings(await store.read())
        finally:
            await client.drop_database(name)
            client.close()

    asyncio.run(run())


def test_unrelated_records_survive_reset_and_restart():
    async def run():
        client = AsyncIOMotorClient(URI, tz_aware=True, tzinfo=UTC)
        name = "fleet_test_" + uuid4().hex
        db = client[name]
        store = Ledger(db)
        try:
            await db.vehicles.insert_one({"vin": "EXTERNAL", "name": "Do not delete"})
            doc = seed(LoadRequest(vehicle_count=1))
            await store.replace_run(doc)
            await store.mutate(
                lambda d: d.update(running=False, clock="2026-10-04T08:45:00+05:30")
            )
            restarted = Ledger(db)
            assert (await restarted.read())["clock"] == "2026-10-04T08:45:00+05:30"
            await restarted.replace_run(seed(LoadRequest(vehicle_count=1)))
            assert await db.vehicles.count_documents({"vin": "EXTERNAL"}) == 1
        finally:
            await client.drop_database(name)
            client.close()

    asyncio.run(run())
