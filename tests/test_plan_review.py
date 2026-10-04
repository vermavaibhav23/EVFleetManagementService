from copy import deepcopy
from datetime import timedelta

import pytest

from app.domain import Approval, PlanRequest, dt
from app.services.control import approve_or_refresh, reject, request_job
from app.services.ledger import bookings
from app.services.optimizer import optimize
from app.services.plan_review import review
from app.services.validation import validate
from tests.test_v2 import fixture


@pytest.fixture(scope="module")
def example():
    doc = fixture()
    plan = optimize(doc, "SIM-001")["plan"]
    doc["plans"][plan["plan_id"]] = plan
    return doc, plan["plan_id"]


def test_review_explains_each_target_and_full_slot_chain(example):
    doc, pid = deepcopy(example)
    plan = doc["plans"][pid]
    result = review(doc, plan)
    assert result["can_approve"]
    assert len(result["slots"]) == 2
    first, second = result["slots"]
    assert first["arrival_pct"] == 10
    assert first["target_pct"] == 38
    assert first["battery_added_kwh"] == 28
    assert "18.00 kWh" in first["reason"]
    assert "20.00 kWh" in first["reason"]
    assert "15.00 kWh" in first["carry_reason"]
    assert second["target_pct"] == 35
    assert "30.00 kWh" in second["reason"]
    for slot, op in zip(
        result["slots"], [o for o in plan["operations"] if o["kind"] == "CHARGE"]
    ):
        assert (slot["charger_id"], slot["port"], slot["start"], slot["end"]) == (
            op["charger_id"],
            op["port"],
            op["start"],
            op["end"],
        )
    assert not bookings(doc)  # looking at proposed slots must never reserve them


def test_taken_slot_requeues_search_without_any_partial_booking(example):
    doc, pid = deepcopy(example)
    plan = doc["plans"][pid]
    occupied = next(o for o in plan["operations"] if o["kind"] == "CHARGE")
    doc["external_bookings"].append(deepcopy(occupied))
    assert not review(doc, plan)["can_approve"]
    result = approve_or_refresh(doc, pid, Approval(run_id=doc["run_id"]))
    assert result["status"] == "REPLAN_QUEUED"
    assert "already booked" in result["message"]
    assert doc["jobs"][result["job_id"]]["vin"] == plan["vin"]
    assert not bookings(doc)
    assert plan["status"] == "SUPERSEDED"
    assert not doc["running"]
    fresh = optimize(doc, plan["vin"])
    if "plan" in fresh:
        assert not validate(doc, fresh["plan"])
    else:
        assert fresh["status"] in ("INFEASIBLE_MODEL", "LIMIT_NO_INCUMBENT")


def test_approval_checks_site_power_and_expired_departure(example):
    doc, pid = deepcopy(example)
    doc["depots"]["SIM-DEPOT"]["power_limit_kw"] = 70
    first = next(o for o in doc["plans"][pid]["operations"] if o["kind"] == "CHARGE")
    # A different port/station uses the same site's supply during the requested slot.
    doc["external_bookings"].append(
        {**first, "charger_id": "CHEAP", "port": 1, "power_kw": 30}
    )
    result = approve_or_refresh(doc, pid, Approval(run_id=doc["run_id"]))
    assert result["status"] == "REPLAN_QUEUED"
    assert "power" in result["reason"]
    assert not bookings(doc)
    doc, pid = deepcopy(example)
    doc["clock"] = dt(doc["plans"][pid]["valid_until"]) + timedelta(minutes=1)
    assert review(doc, doc["plans"][pid])["state"] == "OUTDATED"
    assert (
        approve_or_refresh(doc, pid, Approval(run_id=doc["run_id"]))["status"]
        == "REPLAN_QUEUED"
    )


def test_rejected_option_is_preserved_and_cannot_be_booked(example):
    doc, pid = deepcopy(example)
    request = Approval(run_id=doc["run_id"])
    reject(doc, pid, request)
    assert review(doc, doc["plans"][pid])["state"] == "REJECTED"
    assert "No charging slots" in review(doc, doc["plans"][pid])["reason"]
    assert not bookings(doc)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        approve_or_refresh(doc, pid, request)


def test_elapsed_review_identifies_missed_arrival_deadlines_without_booking(example):
    doc, pid = deepcopy(example)
    plan = doc["plans"][pid]
    vehicle = doc["vehicles"][plan["vin"]]
    pending = [d for d in vehicle["deliveries"] if not d.get("is_return")]
    doc["clock"] = max(dt(d["deadline"]) for d in pending) + timedelta(minutes=1)
    pending[0]["arrived_at"] = pending[0]["deadline"]
    result = review(doc, plan)
    assert result["state"] == "OUTDATED"
    assert not result["can_approve"]
    assert "On-time delivery is no longer possible" in result["tradeoff"]
    for delivery in pending[1:]:
        assert delivery["name"] in result["reason"]
    named = result["reason"].split("passed for ")[1].split(". On-time")[0].split(", ")
    assert pending[0]["name"] not in named
    assert not bookings(doc)


def test_approved_journey_does_not_expire_and_duplicate_approval_is_safe(example):
    doc, pid = deepcopy(example)
    request = Approval(run_id=doc["run_id"])
    assert approve_or_refresh(doc, pid, request)["status"] == "APPROVED"
    doc["clock"] = dt(doc["plans"][pid]["valid_until"]) + timedelta(minutes=1)
    assert review(doc, doc["plans"][pid])["state"] == "APPROVED"
    assert approve_or_refresh(doc, pid, request)["idempotent"]
    assert len(bookings(doc)) == 2


def test_recalculation_pauses_retires_old_options_and_reuses_pending_job(example):
    doc, pid = deepcopy(example)
    doc["running"] = True
    request = PlanRequest(
        run_id=doc["run_id"], pause_for_review=True, compare_tradeoffs=True
    )
    first = request_job(doc, "SIM-001", request)
    assert not doc["running"]
    assert not review(doc, doc["plans"][pid])["can_approve"]
    assert not bookings(doc)
    second = request_job(doc, "SIM-001", request)
    assert first["job_id"] == second["job_id"]
    assert len(doc["jobs"]) == 1
    # A failed/no-solution refresh must never resurrect the old approval button.
    doc["jobs"][first["job_id"]].update(status="COMPLETED", results=[])
    assert review(doc, doc["plans"][pid])["state"] == "SUPERSEDED"


def test_recalculate_preserves_committed_slots_and_rejects_old_run(example):
    from fastapi import HTTPException

    doc, pid = deepcopy(example)
    approve_or_refresh(doc, pid, Approval(run_id=doc["run_id"]))
    reserved = deepcopy(bookings(doc))
    with pytest.raises(HTTPException, match="old simulation run"):
        request_job(doc, "SIM-001", PlanRequest(run_id="old", pause_for_review=True))
    assert not doc["jobs"]
    request_job(
        doc, "SIM-001", PlanRequest(run_id=doc["run_id"], pause_for_review=True)
    )
    assert bookings(doc) == reserved
    assert doc["plans"][pid]["status"] == "APPROVED"
