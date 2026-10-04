"""Pure regressions: no database/broker or Docker process is started."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.domain import Approval, LoadRequest, PlanRequest, Telemetry, dt
from app.services.control import approve, request_job
from app.services.execution import advance
from app.services.optimizer import fingerprint, optimize
from app.services.pipeline import Pipeline
from app.services.seed import seed
from app.services.simulator import caught_up, fresh
from app.services.telemetry import apply_reading


def reading(doc, **changes):
    v = doc["vehicles"]["SIM-001"]
    body = dict(
        event_id="reading-101",
        run_id=doc["run_id"],
        vehicle_id=v["vin"],
        sequence=101,
        observed_at=doc["clock"],
        lat=v["lat"],
        lon=v["lon"],
        energy_kwh=v["energy_kwh"],
        activity=v["state"],
    )
    return Telemetry(**(body | changes))


def snapshot():
    return seed(LoadRequest(vehicle_count=1))


def test_duplicate_and_late_reading_cannot_restore_old_battery():
    doc = snapshot()
    assert apply_reading(doc, reading(doc, energy_kwh=19))["accepted"]
    assert not apply_reading(doc, reading(doc, sequence=100, energy_kwh=20))["accepted"]
    assert not apply_reading(doc, reading(doc, energy_kwh=20))["accepted"]
    assert doc["vehicles"]["SIM-001"]["energy_kwh"] == 19


def test_replaced_instructions_and_old_run_are_fenced():
    doc = snapshot()
    doc["vehicles"]["SIM-001"]["control_version"] = 2
    assert not apply_reading(doc, reading(doc, control_version=1))["accepted"]
    with pytest.raises(HTTPException, match="Old-run"):
        apply_reading(doc, reading(doc, run_id="old"))


def test_invalid_gps_and_excess_battery_rejected():
    doc = snapshot()
    with pytest.raises(ValidationError):
        reading(doc, lat=100)
    with pytest.raises(HTTPException) as error:
        apply_reading(doc, reading(doc, energy_kwh=100000))
    assert error.value.status_code == 422


def test_newer_sequence_with_older_observed_time_cannot_overwrite():
    doc = snapshot()
    apply_reading(doc, reading(doc))
    result = apply_reading(
        doc,
        reading(
            doc, sequence=102, observed_at=dt(doc["clock"]) - timedelta(seconds=10)
        ),
    )
    assert not result["accepted"]


def test_freshness_uses_both_simulation_time_and_received_wall_time():
    doc = snapshot()
    apply_reading(doc, reading(doc))
    v = doc["vehicles"]["SIM-001"]
    assert fresh(doc, v, 120)
    v["received_at"] = (datetime.now(UTC) - timedelta(seconds=121)).isoformat()
    assert not fresh(doc, v, 120)
    v["received_at"] = datetime.now(UTC).isoformat()
    doc["clock"] = (dt(doc["clock"]) + timedelta(seconds=121)).isoformat()
    assert not fresh(doc, v, 120)


def test_heartbeat_does_not_invalidate_reviewed_options():
    doc = snapshot()
    before = fingerprint(doc, "SIM-001")
    apply_reading(doc, reading(doc))
    assert fingerprint(doc, "SIM-001") == before


def test_simulator_reading_updates_progress_without_interrupting_normal_travel():
    doc = snapshot()
    plan = optimize(doc, "SIM-001")["plan"]
    doc["plans"][plan["plan_id"]] = plan
    approve(doc, plan["plan_id"], Approval(run_id=doc["run_id"]))
    simulator = deepcopy(doc)
    advance(simulator, 60)
    v = simulator["vehicles"]["SIM-001"]
    v["sequence"] = 101
    checkpoint = dict(
        vehicle=v,
        clock=simulator["clock"],
        plans=simulator["plans"],
        events=simulator["events"],
        history=[],
    )
    event = reading(simulator)
    # Device execution has not silently changed the service state.
    assert doc["vehicles"]["SIM-001"]["sequence"] == 0
    assert not caught_up(doc, simulator)
    assert apply_reading(doc, event, checkpoint)["accepted"]
    assert caught_up(doc, simulator)
    assert doc["vehicles"]["SIM-001"]["plan_id"] == plan["plan_id"]
    assert doc["vehicles"]["SIM-001"]["energy_kwh"] == v["energy_kwh"]
    assert doc["plans"][plan["plan_id"]]["status"] == "EXECUTING"


def test_external_completed_label_cannot_complete_deliveries():
    doc = snapshot()
    apply_reading(doc, reading(doc, activity="COMPLETED"))
    assert doc["vehicles"]["SIM-001"]["state"] != "COMPLETED"
    assert all(
        d["status"] == "PLANNED" for d in doc["vehicles"]["SIM-001"]["deliveries"]
    )


def test_v3_planning_requires_fresh_observation_and_deduplicates_pending():
    doc = snapshot()
    doc["schema_version"] = 3
    request = PlanRequest(run_id=doc["run_id"], compare_tradeoffs=True)
    with pytest.raises(HTTPException, match="outdated"):
        request_job(doc, "SIM-001", request)
    apply_reading(doc, reading(doc))
    first = request_job(doc, "SIM-001", request)
    second = request_job(doc, "SIM-001", request)
    assert first["job_id"] == second["job_id"] and len(doc["jobs"]) == 1


def test_history_has_one_document_per_event_and_independent_upsert():
    async def run():
        history = SimpleNamespace(
            telemetry_history=SimpleNamespace(
                create_index=AsyncMock(), update_one=AsyncMock()
            )
        )
        pipeline = object.__new__(Pipeline)
        pipeline.history = history
        doc = snapshot()
        for sequence in (101, 102, 102):
            await pipeline.save_history(
                reading(doc, sequence=sequence, event_id=f"reading-{sequence}")
            )
        calls = history.telemetry_history.update_one.call_args_list
        assert calls[0].args[0] != calls[1].args[0]
        assert calls[1].args[0] == calls[2].args[0]
        assert "$setOnInsert" in calls[0].args[1]
        assert calls[0].kwargs["upsert"]

    asyncio.run(run())


def test_auto_planner_does_not_repeat_failed_search_on_every_heartbeat():
    async def run():
        doc = snapshot()
        apply_reading(doc, reading(doc))
        doc["vehicles"]["SIM-001"]["auto_plan_marker"] = fingerprint(doc, "SIM-001")
        pipeline = object.__new__(Pipeline)
        pipeline.store = SimpleNamespace(mutate=AsyncMock())
        await pipeline.auto_plan(doc)
        pipeline.store.mutate.assert_not_called()
        doc["vehicles"]["SIM-001"]["energy_kwh"] -= 1
        await pipeline.auto_plan(doc)
        pipeline.store.mutate.assert_awaited_once()

    asyncio.run(run())


def test_auto_planner_waits_for_running_job_and_fresh_data():
    async def run():
        doc = snapshot()
        pipeline = object.__new__(Pipeline)
        pipeline.store = SimpleNamespace(mutate=AsyncMock())
        await pipeline.auto_plan(doc)
        pipeline.store.mutate.assert_not_called()
        apply_reading(doc, reading(doc))
        doc["jobs"]["job"] = {"status": "RUNNING"}
        await pipeline.auto_plan(doc)
        pipeline.store.mutate.assert_not_called()

    asyncio.run(run())
