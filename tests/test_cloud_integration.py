"""Opt-in real PostgreSQL/Kafka/MongoDB checks. No local services are launched.

Use disposable cloud test services via FLEET_TEST_POSTGRES, FLEET_TEST_KAFKA,
FLEET_TEST_MONGO. Only uniquely named test schema/topics/history are removed.
"""

import asyncio
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import httpx
import pytest
from aiokafka.admin import AIOKafkaAdminClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.core.dependencies import get_database
from app.main import app, lifespan
from app.services.ledger import Ledger

pytestmark = pytest.mark.skipif(
    not all(
        os.getenv(k)
        for k in ("FLEET_TEST_POSTGRES", "FLEET_TEST_KAFKA", "FLEET_TEST_MONGO")
    ),
    reason="Requires disposable cloud PostgreSQL, Kafka and MongoDB; never starts Docker",
)


def test_real_end_to_end_and_reset_fencing():
    async def run():
        suffix = uuid4().hex
        schema = "fleet_test_" + suffix
        original = settings.model_dump()
        database_url = os.environ["FLEET_TEST_POSTGRES"]
        connection = await asyncpg.connect(database_url)
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.close()
        parts = urlsplit(database_url)
        query = dict(parse_qsl(parts.query))
        query["search_path"] = schema
        settings.database_url = urlunsplit(parts._replace(query=urlencode(query)))
        settings.mongodb_uri = os.environ["FLEET_TEST_MONGO"]
        settings.mongodb_db = schema
        settings.kafka_bootstrap_servers = os.environ["FLEET_TEST_KAFKA"]
        settings.kafka_telemetry_topic = f"fleet-test-{suffix}-telemetry"
        settings.kafka_planning_topic = f"fleet-test-{suffix}-planning"
        settings.kafka_group_prefix = f"fleet-test-{suffix}"
        settings.run_workers = True

        async def until(client, predicate, timeout=90):
            async with asyncio.timeout(timeout):
                while True:
                    response = await client.get("/api/v1/fleet")
                    response.raise_for_status()
                    doc = response.json()
                    if predicate(doc):
                        return doc
                    await asyncio.sleep(0.25)

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
                        "/api/v1/simulator/load", json={"vehicle_count": 1}
                    )
                    assert loaded.status_code == 200, loaded.text
                    run_id = loaded.json()["run_id"]
                    doc = await until(
                        client,
                        lambda d: any(
                            p["review"]["can_approve"] for p in d["plans"].values()
                        ),
                    )
                    assert doc["vehicles"]["SIM-001"]["sequence"] > 0
                    assert doc["vehicles"]["SIM-001"]["telemetry_status"] == "LIVE"
                    plan = next(
                        p for p in doc["plans"].values() if p["review"]["can_approve"]
                    )
                    async with asyncio.timeout(20):
                        while True:
                            approved = await client.post(
                                f"/api/v1/journeys/{plan['plan_id']}/approve",
                                json={"run_id": run_id},
                            )
                            if approved.status_code == 200:
                                break
                            assert approved.status_code == 409, approved.text
                            await asyncio.sleep(0.3)
                    assert approved.json()["status"] == "APPROVED"
                    async with asyncio.timeout(20):
                        while True:
                            tick = await client.post(
                                "/api/v1/simulator/tick",
                                json={"run_id": run_id, "seconds": 18000},
                            )
                            if tick.status_code == 200:
                                break
                            assert tick.status_code == 409, tick.text
                            await asyncio.sleep(0.3)
                    complete = await until(
                        client,
                        lambda d: d["vehicles"]["SIM-001"]["state"] == "COMPLETED",
                    )
                    assert all(
                        d["status"] == "COMPLETED"
                        for d in complete["vehicles"]["SIM-001"]["deliveries"]
                    )
                    async with asyncio.timeout(20):
                        while (
                            await pipeline.history.telemetry_history.count_documents(
                                {"run_id": run_id}
                            )
                            < 2
                        ):
                            await asyncio.sleep(0.3)
                    # Two writers cannot commit different changes from one revision.
                    store = Ledger(get_database())
                    old = await store.read()
                    from copy import deepcopy

                    await store.commit(old, deepcopy(old))
                    from fastapi import HTTPException

                    with pytest.raises(HTTPException):
                        await store.commit(old, deepcopy(old))
                    reset = await client.post(
                        "/api/v1/simulator/load", json={"vehicle_count": 1}
                    )
                    assert reset.json()["run_id"] != run_id
                    stale = await client.post(
                        "/api/v1/simulator/tick", json={"run_id": run_id}
                    )
                    assert stale.status_code == 409
        finally:
            # Identifiers are generated above, never derived from production names.
            admin = AIOKafkaAdminClient(**settings.kafka_config)
            try:
                await admin.start()
                await admin.delete_topics(
                    [settings.kafka_telemetry_topic, settings.kafka_planning_topic]
                )
            finally:
                await admin.close()
                mongo = AsyncIOMotorClient(settings.mongodb_uri)
                await mongo.drop_database(schema)
                mongo.close()
                connection = await asyncpg.connect(database_url)
                await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
                await connection.close()
                for key, value in original.items():
                    setattr(settings, key, value)

    asyncio.run(run())
