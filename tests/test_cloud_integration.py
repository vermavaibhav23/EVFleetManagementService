"""Opt-in SQL/Kafka test; unique SQL schema and dedicated precreated test topic."""

import asyncio
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import httpx
import pytest

from app.core.config import settings
from app.core.dependencies import get_database
from app.main import app, lifespan

pytestmark = pytest.mark.skipif(
    not all(
        os.getenv(k)
        for k in ("FLEET_TEST_POSTGRES", "FLEET_TEST_KAFKA", "FLEET_TEST_TOPIC")
    ),
    reason="Requires disposable PostgreSQL and a dedicated Kafka test topic",
)


def test_real_telemetry_sql_history_planning_approval_and_reset():
    async def run():
        original = settings.model_dump()
        schema = "fleet_test_" + uuid4().hex
        url = os.environ["FLEET_TEST_POSTGRES"]
        connection = await asyncpg.connect(url)
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        query["search_path"] = schema
        settings.database_url = urlunsplit(parts._replace(query=urlencode(query)))
        settings.kafka_bootstrap_servers = os.environ["FLEET_TEST_KAFKA"]
        settings.kafka_telemetry_topic = os.environ["FLEET_TEST_TOPIC"]
        settings.kafka_group_prefix = schema
        settings.kafka_auto_create_topics = False
        settings.run_workers = True
        try:
            async with lifespan(app):
                pipeline = app.state.pipeline
                await pipeline.http.aclose()
                pipeline.http = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    loaded = await client.post(
                        "/api/v1/simulator/load",
                        json={"scenario": "EVERYDAY_CHOICES", "vehicle_count": 4},
                    )
                    loaded.raise_for_status()
                    run_id = loaded.json()["run_id"]
                    async with asyncio.timeout(120):
                        while True:
                            response = await client.get("/api/v1/fleet", params={"run_id": run_id})
                            response.raise_for_status()
                            doc = response.json()
                            plans = [
                                p
                                for p in doc["plans"].values()
                                if p["vin"] == "SIM-001" and p["review"]["can_approve"]
                            ]
                            if plans:
                                break
                            await asyncio.sleep(0.5)
                    plan = plans[0]
                    approved = await client.post(
                        f"/api/v1/journeys/{plan['plan_id']}/approve",
                        json={"run_id": run_id, "version": plan["version"]},
                    )
                    approved.raise_for_status()
                    assert approved.json()["status"] == "APPROVED"
                    async with get_database().acquire() as db:
                        assert (
                            await db.fetchval("SELECT count(*) FROM telemetry_history")
                            > 0
                        )
                        assert (
                            await db.fetchval(
                                "SELECT count(*) FROM reservations WHERE vehicle_id='SIM-001'"
                            )
                            == 2
                        )
                        assert not await db.fetchval(
                            "SELECT count(*) FROM outbox WHERE payload IS NULL"
                        )
                    response = await client.post(
                        "/api/v1/simulator/load",
                        json={"scenario": "EVERYDAY_CHOICES", "vehicle_count": 4},
                    )
                    response.raise_for_status()
                    assert response.json()["run_id"] != run_id
                    old = await client.post(
                        f"/api/v1/journeys/{plan['plan_id']}/approve",
                        json={"run_id": run_id},
                    )
                    assert old.status_code in (404, 409)
        finally:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await connection.close()
            for key, value in original.items():
                setattr(settings, key, value)

    asyncio.run(run())
