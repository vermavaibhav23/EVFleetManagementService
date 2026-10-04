from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.domain import Approval, LoadRequest, dt
from app.services.control import approve
from app.services.execution import advance
from app.services.ledger import bookings
from app.services.optimizer import optimize
from app.services.scenario_groups import GROUPS, action_block, trigger_action
from app.services.seed import seed
from app.services.validation import validate


def attach(doc, vin):
    result = optimize(doc, vin)
    assert "plan" in result, result
    p = result["plan"]
    assert not validate(doc, p)
    doc["plans"][p["plan_id"]] = p
    approve(doc, p["plan_id"], Approval(run_id=doc["run_id"]))
    return p


@pytest.mark.parametrize("group", GROUPS)
def test_four_distinct_vehicles_and_consistent_staged_states(group):
    doc = seed(LoadRequest(scenario=group, vehicle_count=12))
    assert len(doc["vehicles"]) == 4
    assert len({(v["lat"], v["lon"]) for v in doc["vehicles"].values()}) == 4
    assert not doc["running"]
    assert dt(doc["clock"]) == dt(doc["start_time"])
    assert (
        doc["events"] == []
    )  # incidents are explicit actions, never a simultaneous timer burst
    for a in doc["scenario_actions"]:
        assert action_block(doc, a) is None
    for p in doc["plans"].values():
        assert p["seeded_journey"]
        assert p["status"] in ("APPROVED", "EXECUTING", "COMPLETED")
    if group == "SHARED_CHARGERS":
        assert doc["vehicles"]["SIM-002"]["state"] == "CHARGING"
    if group == "DELIVERY_DELAYS":
        assert doc["vehicles"]["SIM-001"]["state"] == "SERVICING"
        assert doc["vehicles"]["SIM-002"]["state"] == "TRAVELLING"


def test_split_charge_is_solver_selected_and_executes_with_real_efficiency():
    doc = seed(LoadRequest(scenario="EVERYDAY_CHOICES"))
    p = attach(doc, "SIM-001")
    assert [o["kind"] for o in p["operations"]] == [
        "CHARGE",
        "DELIVERY",
        "CHARGE",
        "DELIVERY",
        "RETURN",
    ]
    charges = [o for o in p["operations"] if o["kind"] == "CHARGE"]
    assert [o["charger_id"] for o in charges] == ["SIM-C1", "SIM-C2"]
    assert charges[0]["energy_end"] - charges[0]["energy_arrival"] == pytest.approx(
        8, abs=1e-4
    )
    assert charges[1]["energy_end"] - charges[1]["energy_arrival"] == pytest.approx(
        20, abs=1e-4
    )
    assert p["total_cost"] == pytest.approx(360 / 0.92, abs=0.01)
    assert p["total_cost"] < 560 / 0.92
    advance(doc, 6 * 3600)
    assert p["status"] == "COMPLETED"
    assert doc["vehicles"]["SIM-001"]["energy_kwh"] == pytest.approx(3, abs=1e-4)
    assert sum(o.get("actual_cost", 0) for o in p["operations"]) == pytest.approx(
        p["total_cost"], abs=0.01
    )


def test_everyday_tradeoffs_are_real_constraints():
    doc = seed(LoadRequest(scenario="EVERYDAY_CHOICES"))
    fast = optimize(doc, "SIM-002")
    assert fast["plan"]["operations"][0]["charger_id"] == "SIM-C3"
    slow = deepcopy(doc)
    slow["stations"]["SIM-C3"]["status"] = "FAILED"
    assert "plan" not in optimize(slow, "SIM-002")
    low = optimize(doc, "SIM-003")["plan"]
    assert low["operations"][0]["kind"] == "CHARGE"
    assert low["operations"][0]["energy_arrival"] == pytest.approx(1)
    control = optimize(doc, "SIM-004")["plan"]
    assert control["total_cost"] == 0
    assert all(o["kind"] != "CHARGE" for o in control["operations"])


def test_charger_failure_preserves_energy_release_and_independent_vehicle():
    doc = seed(LoadRequest(scenario="SHARED_CHARGERS"))
    v = doc["vehicles"]["SIM-002"]
    energy = v["energy_kwh"]
    control = deepcopy(doc["vehicles"]["SIM-004"])
    trigger_action(doc, "fail-east")
    assert v["energy_kwh"] == energy
    assert v["state"] == "RELEASING"
    assert doc["vehicles"]["SIM-004"] == control
    assert any(b["vin"] == v["vin"] for b in bookings(doc))
    with pytest.raises(HTTPException):
        trigger_action(doc, "fail-east")
    advance(doc, 61)
    assert v["state"] == "WAITING_REVIEW"
    assert v["energy_kwh"] == energy
    assert not any(b["vin"] == v["vin"] for b in bookings(doc))


def test_queue_and_power_change_do_not_double_book_or_hit_other_sites():
    doc = seed(LoadRequest(scenario="SHARED_CHARGERS"))
    north = attach(doc, "SIM-001")
    assert not validate({**doc, "plans": {}}, north)
    west = attach(doc, "SIM-003")
    assert any(o.get("charger_id") == "SIM-C4" for o in west["operations"])
    control = deepcopy(doc["vehicles"]["SIM-004"])
    east = deepcopy(doc["vehicles"]["SIM-002"])
    trigger_action(doc, "power-west")
    assert west["status"] == "INTERRUPTED"
    assert doc["vehicles"]["SIM-004"] == control
    assert doc["vehicles"]["SIM-002"] == east
    replacement = optimize(doc, "SIM-003")
    assert "plan" in replacement, replacement
    assert not validate(doc, replacement["plan"])


def test_delay_and_consumption_keep_deadlines_and_actual_position():
    doc = seed(LoadRequest(scenario="DELIVERY_DELAYS"))
    v = doc["vehicles"]["SIM-001"]
    old = dt(v["service_until"])
    deadlines = [d["deadline"] for d in v["deliveries"]]
    trigger_action(doc, "unload")
    assert (dt(v["service_until"]) - old).total_seconds() == 1800
    assert [d["deadline"] for d in v["deliveries"]] == deadlines
    moving = doc["vehicles"]["SIM-002"]
    position = (moving["lat"], moving["lon"])
    trigger_action(doc, "consumption")
    assert (moving["lat"], moving["lon"]) == position
    assert moving["consumption_kwh_km"] == 0.5
    assert moving["state"] == "WAITING_REVIEW"
    assert "plan" not in optimize(doc, "SIM-003")
    assert "plan" in optimize(doc, "SIM-003", recovery=True)


def test_stranding_health_and_deadline_are_distinct_and_guarded():
    doc = seed(LoadRequest(scenario="ASSISTANCE_CASES"))
    assert "plan" not in optimize(doc, "SIM-001", recovery=True, reserve_exception=True)
    v = doc["vehicles"]["SIM-002"]
    position = (v["lat"], v["lon"])
    trigger_action(doc, "strand")
    assert (v["lat"], v["lon"]) == position and v["energy_kwh"] == 0
    assert v["state"] == "ASSISTANCE"
    trigger_action(doc, "health")
    assert "plan" not in optimize(doc, "SIM-003", recovery=True, reserve_exception=True)
    assert "plan" not in optimize(doc, "SIM-004")
    assert "plan" in optimize(doc, "SIM-004", recovery=True)
    fresh = seed(LoadRequest(scenario="ASSISTANCE_CASES"))
    fresh["vehicles"]["SIM-002"]["state"] = "PARKED"
    with pytest.raises(HTTPException):
        trigger_action(fresh, "strand")
