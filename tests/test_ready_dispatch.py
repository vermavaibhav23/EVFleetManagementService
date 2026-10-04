"""Readiness dispatch, deterministic execution and committed slot boundaries."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest

from app.domain import LoadRequest, dt
from app.services.execution import advance, interrupt
from app.services.optimizer import optimize
from app.services.seed import seed
from app.services.validation import validate
from tests.test_v2 import attach, fixture


def direct():
    doc = seed(LoadRequest(vehicle_count=1))
    plan = optimize(doc, "SIM-001")["plan"]
    attach(doc, plan)
    return doc, plan


def test_old_review_policy_no_longer_adds_a_dispatch_hold():
    doc = seed(LoadRequest(vehicle_count=1))
    assert "review_minutes" not in doc["policy"]
    doc["policy"]["review_minutes"] = 99  # existing persisted run
    plan = optimize(doc, "SIM-001")["plan"]
    assert dt(plan["operations"][0]["depart"]) == dt(doc["clock"])
    for previous, current in zip(plan["operations"], plan["operations"][1:]):
        assert dt(current["depart"]) == dt(previous["end"])


def test_saved_departure_does_not_hold_vehicle_and_restart_keeps_progress():
    doc, plan = direct()
    now = dt(doc["clock"])
    # Mimic an already approved legacy journey with a discretionary 10-minute hold.
    for op in plan["operations"]:
        for key in ("depart", "arrival", "start", "end"):
            op[key] = dt(op[key]) + timedelta(minutes=10)
    advance(doc, 30)
    assert doc["vehicles"]["SIM-001"]["state"] == "TRAVELLING"
    assert dt(plan["operations"][0]["depart"]) == now
    restarted = json.loads(json.dumps(doc, default=str))
    advance(doc, 3 * 3600)
    for _ in range(180):
        advance(restarted, 60)
    assert plan["status"] == "COMPLETED"
    assert doc["vehicles"] == restarted["vehicles"]


def test_route_readiness_and_customer_acceptance_are_still_real_waits():
    doc = seed(LoadRequest(vehicle_count=1))
    v = doc["vehicles"]["SIM-001"]
    now = dt(doc["clock"])
    first = v["deliveries"][0]
    first.update(
        ready_at=now + timedelta(minutes=5), accepts_at=now + timedelta(minutes=60)
    )
    plan = optimize(doc, v["vin"])["plan"]
    attach(doc, plan)
    position = (v["lat"], v["lon"])
    advance(doc, 299)
    assert (v["lat"], v["lon"]) == position and v["state"] == "READY"
    advance(doc, 2)
    assert v["state"] == "TRAVELLING"
    op = plan["operations"][0]
    advance(doc, (dt(op["arrival"]) - dt(doc["clock"])).total_seconds() + 1)
    assert v["state"] == "WAITING_WINDOW"
    assert dt(op["start"]) == dt(first["accepts_at"])
    advance(doc, (dt(op["end"]) - dt(doc["clock"])).total_seconds() + 1)
    assert v["state"] == "TRAVELLING"
    assert dt(plan["operations"][1]["depart"]) == dt(op["end"])


@pytest.fixture(scope="module")
def booked():
    doc = fixture()
    now = dt(doc["clock"])
    doc["external_bookings"].append(
        dict(
            charger_id="EXP",
            port=1,
            start=now,
            end=now + timedelta(minutes=10),
            power_kw=60,
        )
    )
    plan = optimize(doc, "SIM-001")["plan"]
    attach(doc, plan)
    return doc, plan["plan_id"]


def test_milp_leaves_when_ready_and_waits_at_reserved_charger(booked):
    doc, pid = deepcopy(booked)
    plan = doc["plans"][pid]
    cursor = dt(doc["clock"])
    slots = [
        (o["start"], o["end"]) for o in plan["operations"] if o["kind"] == "CHARGE"
    ]
    for op in plan["operations"]:
        assert abs((dt(op["depart"]) - cursor).total_seconds()) < 0.05
        cursor = dt(op["end"])
    v = doc["vehicles"]["SIM-001"]
    initial = v["energy_kwh"]
    advance(doc, 60)
    assert v["state"] == "QUEUING" and v["energy_kwh"] == initial
    advance(doc, 10 * 60)
    assert v["state"] == "CHARGING" and v["energy_kwh"] > initial
    advance(doc, 3 * 3600)
    assert plan["status"] == "COMPLETED"
    assert [
        (o["start"], o["end"]) for o in plan["operations"] if o["kind"] == "CHARGE"
    ] == slots
    assert sum(o.get("actual_cost", 0) for o in plan["operations"]) == pytest.approx(
        plan["total_cost"], abs=0.01
    )


def test_missed_slot_is_not_silently_moved_or_used_early(booked):
    doc, pid = deepcopy(booked)
    plan = doc["plans"][pid]
    charge = plan["operations"][0]
    slot = (charge["start"], charge["end"])
    doc["clock"] = dt(charge["start"]) + timedelta(minutes=1)
    v = doc["vehicles"]["SIM-001"]
    initial = v["energy_kwh"]
    advance(doc, 1)
    assert plan["status"] == "INTERRUPTED"
    assert "slot" in v["incident"]
    assert v["energy_kwh"] == initial
    assert (charge["start"], charge["end"]) == slot


def test_replanned_vehicle_finishes_active_service_before_moving():
    doc, plan = direct()
    op = plan["operations"][0]
    advance(doc, (dt(op["start"]) - dt(doc["clock"])).total_seconds() + 1)
    v = doc["vehicles"]["SIM-001"]
    until = dt(v["service_until"])
    interrupt(doc, v["vin"], "Review remaining route")
    replacement = optimize(doc, v["vin"])["plan"]
    attach(doc, replacement)
    position = (v["lat"], v["lon"])
    advance(doc, (until - dt(doc["clock"])).total_seconds() - 1)
    assert v["state"] == "SERVICING" and (v["lat"], v["lon"]) == position
    advance(doc, 2)
    assert v["state"] == "TRAVELLING"
    assert dt(replacement["operations"][0]["depart"]) == until


def test_validator_rejects_discretionary_dispatch_wait():
    doc = seed(LoadRequest(vehicle_count=1))
    plan = optimize(doc, "SIM-001")["plan"]
    for op in plan["operations"]:
        for key in ("depart", "arrival", "start", "end"):
            op[key] = dt(op[key]) + timedelta(minutes=1)
    assert "Leg must begin when vehicle and route are ready" in validate(doc, plan)


def test_milp_uses_exact_readiness_maximum_in_each_customer_gap():
    doc = fixture()
    now = dt(doc["clock"])
    deliveries = doc["vehicles"]["SIM-001"]["deliveries"]
    deliveries[0]["ready_at"] = now + timedelta(minutes=5)
    deliveries[1]["ready_at"] = now + timedelta(minutes=80)
    deliveries[1]["deadline"] = now + timedelta(minutes=200)
    plan = optimize(doc, "SIM-001")["plan"]
    assert not validate(doc, plan)
    cursor, index = now, 0
    for op in plan["operations"]:
        ready = (
            max(cursor, dt(deliveries[index]["ready_at"]))
            if index < len(deliveries)
            else cursor
        )
        assert abs((dt(op["depart"]) - ready).total_seconds()) < 0.05
        cursor = dt(op["end"])
        if op["kind"] != "CHARGE":
            index += 1
