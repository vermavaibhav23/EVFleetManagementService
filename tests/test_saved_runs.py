"""Real PostgreSQL regression for scenario isolation; no Kafka service needed."""

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import dumps
from app.domain import LoadRequest, Telemetry
from app.services.ledger import Ledger
from app.services.seed import seed
from app.services.telemetry import TelemetryProcessor

pytestmark = pytest.mark.skipif(
    not os.getenv("FLEET_TEST_POSTGRES"), reason="Needs disposable PostgreSQL"
)


@pytest.mark.parametrize("legacy", [False, True, "v5"])
def test_saved_scenarios_switch_reset_and_delayed_telemetry(legacy):
    async def run():
        url = os.environ["FLEET_TEST_POSTGRES"]
        admin = await asyncpg.connect(url)
        schema = "saved_runs_" + uuid4().hex
        await admin.execute(f'CREATE SCHEMA "{schema}"')

        async def initialize(connection):
            await connection.set_type_codec(
                "jsonb", schema="pg_catalog", encoder=dumps, decoder=json.loads
            )

        pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=3,
            server_settings={"search_path": schema},
            init=initialize,
        )
        try:
            sql = Path("app/core/schema.sql").read_text()
            async with pool.acquire() as c:
                if legacy is True:
                    await c.execute(
                        Path("tests/fixtures/schema_v4.sql").read_text(
                            encoding="utf-8-sig"
                        )
                    )
                    await c.execute(
                        "INSERT INTO fleet_run VALUES(true,'legacy',7,$1)",
                        {"scenario": "EVERYDAY_CHOICES", "running": False},
                    )
                    await c.execute(
                        "INSERT INTO vehicles VALUES('SIM-001','legacy',$1)",
                        {"marker": "preserved"},
                    )
                if legacy == "v5":
                    await c.execute(
                        Path("tests/fixtures/schema_v5.sql").read_text(
                            encoding="utf-8-sig"
                        )
                    )
                    await c.execute(
                        "INSERT INTO fleet_run VALUES('legacy','EVERYDAY_CHOICES',true,7,$1)",
                        {"scenario": "EVERYDAY_CHOICES", "running": False},
                    )
                    await c.execute(
                        "INSERT INTO vehicles VALUES('SIM-001','legacy',$1)",
                        {"marker": "preserved"},
                    )
                await c.execute(sql)
                await c.execute(sql)  # Startup migration must be repeatable.
                if legacy:
                    assert (
                        await c.fetchval(
                            "SELECT revision FROM fleet_run WHERE run_id='legacy'"
                        )
                        == 7
                    )
                    assert (
                        await c.fetchval(
                            "SELECT data->>'marker' FROM vehicles WHERE run_id='legacy'"
                        )
                        == "preserved"
                    )
            store = Ledger(pool)
            a = await store.replace_run(
                seed(LoadRequest(scenario="EVERYDAY_CHOICES", vehicle_count=4))
            )
            old = await store.read(a["run_id"])
            edited = deepcopy(old)
            edited["test_marker"] = "preserve my progress"
            await store.commit(old, edited)
            async with pool.acquire() as c:
                pending_a = await c.fetchval(
                    "SELECT payload FROM outbox WHERE run_id=$1 LIMIT 1", a["run_id"]
                )
                await c.execute(
                    "INSERT INTO planning_jobs VALUES('waiting-job',$1,$2)",
                    a["run_id"],
                    {"status": "QUEUED", "vin": None, "results": []},
                )
            b = await store.replace_run(
                seed(LoadRequest(scenario="SHARED_CHARGERS", vehicle_count=4))
            )
            stale_b = await store.read(b["run_id"])
            # Independent browser requests must never rely on a global selection.
            from types import SimpleNamespace

            import httpx
            from fastapi import FastAPI

            from app.api.routes import ledger, router

            api = FastAPI()
            api.include_router(router)
            api.dependency_overrides[ledger] = lambda: store
            api.state.pipeline = SimpleNamespace(last_viewed={})
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api), base_url="http://test"
            ) as client:
                assert (await client.get("/fleet")).status_code == 422
                for saved in (a, b, a):
                    response = await client.get(
                        "/fleet", params={"run_id": saved["run_id"]}
                    )
                    assert response.status_code == 200
                    assert response.json()["run_id"] == saved["run_id"]
                response = await client.post(
                    "/simulator/start", json={"run_id": b["run_id"], "speed": 30}
                )
                assert response.status_code == 200
                assert (await store.read(b["run_id"]))["running"]
                assert not (await store.read(a["run_id"]))["running"]
                assert (
                    await client.get("/day-view", params={"run_id": b["run_id"]})
                ).status_code == 200
            stale_b = await store.read(b["run_id"])

            assert len(await store.saved_runs()) == 2
            async with pool.acquire() as c:
                assert (
                    await c.fetchval(
                        "SELECT data->>'status' FROM planning_jobs WHERE run_id=$1",
                        a["run_id"],
                    )
                    == "QUEUED"
                )
                b_bookings = await c.fetch(
                    "SELECT * FROM reservations WHERE run_id=$1 ORDER BY id",
                    b["run_id"],
                )
                assert (
                    await c.fetchval("SELECT count(*) FROM vehicles WHERE id='SIM-001'")
                    == 2
                )
                assert await c.fetchval("SELECT count(*) FROM fleet_run") == 2
            # Previously queued readings must finish in A even while B is selected.
            await TelemetryProcessor(pool).process(Telemetry(**pending_a))
            assert (await store.read(b["run_id"]))["run_id"] == b["run_id"]
            async with pool.acquire() as c:
                assert (
                    await c.fetch(
                        "SELECT * FROM reservations WHERE run_id=$1 ORDER BY id",
                        b["run_id"],
                    )
                    == b_bookings
                )
            await store.replace_run(
                seed(LoadRequest(scenario="DELIVERY_DELAYS", vehicle_count=4))
            )
            assert len(await store.saved_runs()) == 3
            await store.select_scenario("EVERYDAY_CHOICES")
            restored = await store.read(a["run_id"])
            assert restored["run_id"] == a["run_id"]
            assert restored["test_marker"] == "preserve my progress"
            assert not restored["running"]
            assert (
                restored["vehicles"][pending_a["vehicle_id"]]["sequence"]
                == pending_a["sequence"]
            )
            # Other scenarios can update independently; stale writes in the same run fail.
            await store.commit(stale_b, deepcopy(stale_b))
            with pytest.raises(HTTPException):
                await store.commit(stale_b, deepcopy(stale_b))
            with pytest.raises(HTTPException):
                await store.select_scenario("NOT_LOADED")
            assert (await store.read(a["run_id"]))["run_id"] == a["run_id"]
            async with pool.acquire() as c:
                columns = await c.fetch(
                    "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema()"
                )
                fields = {(v["table_name"], v["column_name"]) for v in columns}
                assert ("fleet_run", "active") not in fields
                assert ("outbox", "kind") not in fields
                assert ("outbox", "message_key") not in fields
                assert ("reservations", "data") not in fields
                metadata = await c.fetchval(
                    "SELECT data FROM fleet_run WHERE run_id=$1", a["run_id"]
                )
                assert (
                    not {"run_id", "scenario", "revision", "schema_version"}
                    & metadata.keys()
                )
            with pytest.raises(HTTPException):
                await store.read(None)
            reset = await store.replace_run(
                seed(LoadRequest(scenario="EVERYDAY_CHOICES", vehicle_count=5))
            )
            assert reset["run_id"] != a["run_id"]
            assert len(await store.saved_runs()) == 3
            async with pool.acquire() as c:
                assert (
                    await c.fetchval(
                        "SELECT count(*) FROM vehicles WHERE run_id=$1", a["run_id"]
                    )
                    == 0
                )
                assert (
                    await c.fetchval(
                        "SELECT count(*) FROM vehicles WHERE run_id=$1", b["run_id"]
                    )
                    == 4
                )
                assert (
                    await c.fetchval(
                        "SELECT count(*) FROM vehicles WHERE run_id=$1", reset["run_id"]
                    )
                    == 5
                )
            with pytest.raises(HTTPException, match="Old-run"):
                await TelemetryProcessor(pool).process(Telemetry(**pending_a))
            await store.select_scenario("SHARED_CHARGERS")
            assert (await store.read(b["run_id"]))["run_id"] == b["run_id"]
        finally:
            await pool.close()
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await admin.close()

    asyncio.run(run())
