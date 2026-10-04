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
    doc = seed(LoadRequest(scenario=group, vehicle_count=4))
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
    from app.services.geometry import distance

    doc = seed(LoadRequest(scenario="EVERYDAY_CHOICES"))
    assert distance(doc["stations"]["SIM-C3"], doc["stations"]["SIM-C4"]) > 10
    assert distance(doc["depots"]["SIM-DEPOT"], doc["depots"]["HOME-A"]) > 12
    fast = optimize(doc, "SIM-002")
    assert fast["plan"]["operations"][0]["charger_id"] == "SIM-C3"
    slow = deepcopy(doc)
    slow["stations"]["SIM-C3"]["status"] = "FAILED"
    assert "plan" not in optimize(slow, "SIM-002")
    low = optimize(doc, "SIM-003")["plan"]
    assert low["operations"][0]["kind"] == "CHARGE"
    assert low["operations"][0]["energy_arrival"] == pytest.approx(1)
    control = doc["plans"][doc["vehicles"]["SIM-004"]["plan_id"]]
    assert control["seeded_journey"] and control["status"] == "EXECUTING"
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


@pytest.mark.parametrize("group", GROUPS)
def test_selected_fleet_size_preserves_core_cases_and_adds_distinct_routes(group):
    small = seed(LoadRequest(scenario=group, vehicle_count=4))
    large = seed(LoadRequest(scenario=group, vehicle_count=12))
    assert len(large["vehicles"]) == 12
    assert len({(v["lat"], v["lon"]) for v in large["vehicles"].values()}) == 12
    assert len(large["scenario_actions"]) == len(small["scenario_actions"])
    for vin, core in small["vehicles"].items():
        actual = large["vehicles"][vin]
        assert (actual["case"], actual["state"]) == (core["case"], core["state"])
        assert actual["lat"] == pytest.approx(core["lat"])
        assert actual["lon"] == pytest.approx(core["lon"])
        assert actual["energy_kwh"] == pytest.approx(core["energy_kwh"])
    extra = list(large["vehicles"].values())[4:]
    assert {v["state"] for v in extra} == {"TRAVELLING", "SERVICING", "READY"}
    for v in extra:
        assert v["case"] == "ADDITIONAL_VEHICLE"
        assert not v["health_fault"] and not v["incident"]
        plan = large["plans"][v["plan_id"]]
        assert plan["seeded_journey"] and plan["approved_at"]
        assert plan["status"] in ("APPROVED", "EXECUTING")
        assert plan["total_cost"] == 0
        assert all(o["kind"] != "CHARGE" for o in plan["operations"])
    advance(large, 6 * 3600)
    for v in extra:
        assert v["state"] == "COMPLETED"
        assert v["energy_kwh"] >= large["policy"]["reserve_kwh"]
        assert all(d["status"] == "COMPLETED" for d in v["deliveries"])
        assert all(dt(d["arrived_at"]) <= dt(d["deadline"]) for d in v["deliveries"])


def test_fleet_size_limits_and_large_fleet_reproducibility():
    from pydantic import ValidationError

    for count in (1, 3, 101):
        with pytest.raises(ValidationError):
            LoadRequest(scenario="EVERYDAY_CHOICES", vehicle_count=count)
    a = seed(LoadRequest(scenario="EVERYDAY_CHOICES", vehicle_count=100, seed=77))
    b = seed(LoadRequest(scenario="EVERYDAY_CHOICES", vehicle_count=100, seed=77))
    assert len(a["vehicles"]) == 100
    # Approval IDs are unique per run; the physical starting snapshot is reproducible.
    assert {
        vin: {k: value for k, value in v.items() if k != "plan_id"}
        for vin, v in a["vehicles"].items()
    } == {
        vin: {k: value for k, value in v.items() if k != "plan_id"}
        for vin, v in b["vehicles"].items()
    }
    assert len({(v["lat"], v["lon"]) for v in a["vehicles"].values()}) == 100
    advance(a, 6 * 3600)
    for v in list(a["vehicles"].values())[4:]:
        assert v["state"] == "COMPLETED"
        assert v["energy_kwh"] >= a["policy"]["reserve_kwh"]


@pytest.mark.parametrize("group", GROUPS)
def test_every_station_has_full_day_varying_tariffs(group):
    from datetime import timedelta

    from app.services.pricing import intervals, price_at, session

    doc = seed(LoadRequest(scenario=group, vehicle_count=4))
    start = dt(doc["start_time"]).replace(hour=0, minute=0, second=0)
    assert doc["tariff_profile_version"] == 1
    for station in doc["stations"].values():
        bands = intervals(start, start + timedelta(days=1), station)
        assert len(bands) == 5
        assert bands[0][0] == start and bands[-1][1] == start + timedelta(days=1)
        assert all(a[1] == b[0] for a, b in zip(bands, bands[1:]))
        assert len({r[2] for r in bands}) == 5
        assert price_at(start.replace(hour=9, minute=59), station) == station["price"]
        assert price_at(start.replace(hour=10), station) == round(
            station["price"] * 1.15, 2
        )
        _, grid, cost = session(
            start.replace(hour=9, minute=50), 0, 20, 100, 60, 1, station
        )
        assert grid == pytest.approx(20)
        assert cost == pytest.approx(
            10 * station["price"] + 10 * round(station["price"] * 1.15, 2)
        )
