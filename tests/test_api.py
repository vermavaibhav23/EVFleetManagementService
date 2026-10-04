import asyncio
import os
from copy import deepcopy
from datetime import UTC
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.domain import LoadRequest, PlanRequest
from app.main import app
from app.services.control import request_job
from app.services.ledger import Ledger
from app.services.runner import Runner
from app.services.seed import seed

URI = os.getenv("FLEET_TEST_MONGO")
pytestmark = pytest.mark.skipif(not URI, reason="Requires isolated FLEET_TEST_MONGO")


def test_http_plan_review_approve_execute_and_reset():
    prior_uri, prior_db = settings.mongodb_uri, settings.mongodb_db
    name = "fleet_test_" + uuid4().hex
    settings.mongodb_uri = URI
    settings.mongodb_db = name
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/health/ready").status_code == 200
            loaded = client.post(
                "/api/v1/simulator/load", json={"vehicle_count": 1}
            ).json()
            run_id = loaded["run_id"]
            assert (
                client.post(
                    "/api/v1/simulator/load",
                    json={"scenario": "EDGE_CASE_DAY", "vehicle_count": 1},
                ).status_code
                == 422
            )
            response = client.post(
                "/api/v1/journeys/plan", json={"alternatives": False}
            )
            assert response.status_code == 200, response.text
            job_id = response.json()["job_id"]
            deadline = monotonic() + 25
            while monotonic() < deadline:
                job = client.get(f"/api/v1/jobs/{job_id}").json()
                if job["status"] not in ("QUEUED", "RUNNING"):
                    break
                sleep(0.2)
            assert job["status"] == "COMPLETED", job
            assert job["results"][0]["status"] == "OPTIMAL_MODEL", job
            plan_id = job["results"][0]["plan_id"]
            proposal = client.get("/api/v1/fleet").json()["plans"][plan_id]
            assert proposal["review"]["can_approve"]
            assert (
                proposal["review"]["delivery_summary"]
                == "All remaining deliveries on time."
            )
            assert proposal["review"]["slots"] == []
            response = client.post(
                f"/api/v1/journeys/{plan_id}/approve", json={"run_id": run_id}
            )
            assert response.status_code == 200, response.text
            assert client.post(
                f"/api/v1/journeys/{plan_id}/approve", json={"run_id": run_id}
            ).json()["idempotent"]
            view = client.get("/api/v1/day-view?day=2026-10-04").json()
            assert len(view["tariffs"]) == 4
            assert len(view["tariffs"]["SIM-C1"]) == 5
            assert (
                client.post(
                    "/api/v1/simulator/tick", json={"run_id": run_id, "seconds": 18000}
                ).status_code
                == 200
            )
            state = client.get("/api/v1/fleet").json()
            assert state["vehicles"]["SIM-001"]["state"] == "COMPLETED"
            new = client.post(
                "/api/v1/simulator/load", json={"vehicle_count": 1}
            ).json()
            assert new["run_id"] != run_id
            assert (
                client.post(
                    f"/api/v1/journeys/{plan_id}/approve", json={"run_id": run_id}
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/v1/telemetry",
                    json={
                        "run_id": run_id,
                        "vin": "SIM-001",
                        "sequence": 1,
                        "energy_kwh": 0,
                        "temperature_c": 30,
                        "health_fault": False,
                    },
                ).status_code
                == 409
            )
    finally:
        settings.mongodb_uri, settings.mongodb_db = prior_uri, prior_db

        async def cleanup():
            client = AsyncIOMotorClient(URI)
            await client.drop_database(name)
            client.close()

        asyncio.run(cleanup())


def test_reset_terminates_worker_and_discards_its_output():
    async def run():
        client = AsyncIOMotorClient(URI, tz_aware=True, tzinfo=UTC)
        name = "fleet_test_" + uuid4().hex
        store = Ledger(client[name])
        runner = Runner(client[name])
        try:
            doc = seed(LoadRequest())
            created = request_job(doc, None, PlanRequest(alternatives=False))
            job = doc["jobs"][created["job_id"]]
            from datetime import datetime, timedelta

            job.update(
                status="RUNNING",
                owner=runner.owner,
                lease_until=datetime.now(UTC) + timedelta(minutes=5),
            )
            snapshot = await store.replace_run(doc)
            task = asyncio.create_task(runner.execute(snapshot, deepcopy(job)))
            await asyncio.sleep(0.4)
            new = await store.replace_run(seed(LoadRequest(vehicle_count=1)))
            await asyncio.wait_for(task, 10)
            current = await store.read()
            assert (
                current["run_id"] == new["run_id"]
                and not current["plans"]
                and not current["jobs"]
            )
            assert runner.process is None
        finally:
            await runner.stop()
            await client.drop_database(name)
            client.close()

    asyncio.run(run())
