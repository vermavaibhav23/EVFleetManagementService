import itertools
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.domain import Approval, LoadRequest, dt
from app.services.control import approve, telemetry
from app.services.execution import advance, apply_event, interrupt
from app.services.ledger import bookings
from app.services.optimizer import optimize
from app.services.pricing import price_at, session
from app.services.seed import seed
from app.services.validation import validate


def fixture():
    doc = seed(LoadRequest(vehicle_count=1))
    now = datetime(2026, 10, 4, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    doc["clock"] = now.isoformat()
    doc["events"] = []
    doc["policy"].update(
        reserve_kwh=5,
        efficiency=1,
        taper=False,
        review_minutes=0,
        connection_minutes=0,
        release_minutes=0,
        waiting_allowance_minutes=0,
        solver_seconds=10,
        horizon_minutes=300,
    )
    depot = doc["depots"]["SIM-DEPOT"]
    depot.update(node="DEPOT", power_limit_kw=200)
    v = doc["vehicles"]["SIM-001"]
    v.update(node="EXP", capacity_kwh=100, energy_kwh=10, max_power_kw=60)
    exp = dict(
        charger_id="EXP",
        node="EXP",
        name="Expensive",
        depot_id="SIM-DEPOT",
        lat=v["lat"],
        lon=v["lon"],
        power_kw=60,
        port_count=1,
        connector="CCS2",
        status="AVAILABLE",
        price=20,
        tariffs=[],
    )
    cheap = {
        **exp,
        "charger_id": "CHEAP",
        "node": "CHEAP",
        "name": "Cheap",
        "power_kw": 30,
        "price": 10,
        "lat": 13.1,
    }
    doc["stations"] = {"EXP": exp, "CHEAP": cheap}
    v["deliveries"] = [
        dict(
            trip_id=n,
            node=n,
            sequence=i + 1,
            name=n,
            lat=13 + i * 0.1,
            lon=77.5,
            ready_at=now.isoformat(),
            accepts_at=now.isoformat(),
            deadline=(now + timedelta(minutes=100 if n == "B" else 240)).isoformat(),
            service_minutes=10 if n == "A" else 0,
            status="PLANNED",
        )
        for i, n in enumerate(["A", "B", "C"])
    ]
    nodes = ["EXP", "CHEAP", "A", "B", "C", "DEPOT"]
    doc["roads"] = {
        f"{a}>{b}": dict(energy=0 if a == b else 200, minutes=0 if a == b else 200)
        for a, b in itertools.product(nodes, repeat=2)
    }
    for a, b, e, t in [
        ("EXP", "A", 10, 10),
        ("A", "CHEAP", 8, 10),
        ("CHEAP", "B", 10, 10),
        ("A", "B", 18, 20),
        ("B", "C", 12, 10),
        ("C", "DEPOT", 8, 10),
    ]:
        doc["roads"][f"{a}>{b}"] = dict(energy=e, minutes=t)
    doc["external_bookings"] = [
        dict(
            charger_id="CHEAP",
            port=1,
            start=now,
            end=now + timedelta(minutes=60),
            power_kw=30,
        )
    ]
    return doc


def attach(doc, plan):
    doc["plans"][plan["plan_id"]] = plan
    approve(
        doc, plan["plan_id"], Approval(run_id=doc["run_id"], acknowledge_recovery=True)
    )


def test_710_fixture_and_full_journey_execution():
    doc = fixture()
    result = optimize(doc, "SIM-001")
    assert "plan" in result, result
    plan = result["plan"]
    assert plan["total_cost"] == pytest.approx(710, abs=0.02)
    charges = [o for o in plan["operations"] if o["kind"] == "CHARGE"]
    assert [o["grid_kwh"] for o in charges] == pytest.approx([28, 15], abs=0.002)
    assert [o["kind"] for o in plan["operations"]] == [
        "CHARGE",
        "DELIVERY",
        "CHARGE",
        "DELIVERY",
        "DELIVERY",
        "RETURN",
    ]
    assert not validate(doc, plan)
    attach(doc, plan)
    advance(doc, 29 * 60)
    assert (
        plan["status"] == "EXECUTING"
    )  # first charge never completes the whole journey
    advance(doc, 3 * 3600)
    assert doc["vehicles"]["SIM-001"]["state"] == "COMPLETED"
    assert doc["vehicles"]["SIM-001"]["energy_kwh"] == pytest.approx(5, abs=0.001)
    assert sum(o.get("actual_cost", 0) for o in plan["operations"]) == pytest.approx(
        710, abs=0.02
    )


def test_arithmetic_enumeration_independent_of_solver():
    feasible = []
    for expensive in range(44):
        cheap = 43 - expensive
        if expensive < 13:
            continue
        arrival = max(60, expensive + 30) + cheap * 2 + 10
        if arrival <= 100:
            feasible.append((expensive * 20 + cheap * 10, expensive, cheap))
    assert min(feasible) == (710, 28, 15)
    assert max(60, 13 + 30) + 30 * 2 + 10 == 130
    assert 43 * 20 == 860


def test_taper_tariff_integrates_actual_grid_draw():
    now = datetime(2026, 10, 4, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    station = dict(
        price=20, tariffs=[dict(id="first", start_minute=540, end_minute=545, price=10)]
    )
    seconds, grid, cost = session(now, 75, 95, 100, 60, 1, station)
    assert grid == pytest.approx(20)
    assert cost == pytest.approx(350)
    assert seconds == pytest.approx((5 + 10 / 0.6 + 5 / 0.3) * 60)


def test_milp_crosses_both_taper_boundaries_and_tariff_change():
    doc = fixture()
    v = doc["vehicles"]["SIM-001"]
    v["energy_kwh"] = 75
    v["deliveries"] = v["deliveries"][:1]
    doc["policy"].update(taper=True, efficiency=0.9)
    doc["roads"]["EXP>A"] = dict(energy=90, minutes=10)
    doc["roads"]["A>DEPOT"] = dict(energy=1, minutes=10)
    doc["stations"]["EXP"]["tariffs"] = [
        dict(id="early", start_minute=540, end_minute=545, price=10)
    ]
    result = optimize(doc, "SIM-001")
    assert "plan" in result, result
    charge = result["plan"]["operations"][0]
    assert charge["energy_end"] == pytest.approx(96, abs=0.001)
    assert charge["grid_kwh"] == pytest.approx(21 / 0.9, abs=0.001)
    assert charge["cost"] == pytest.approx(50 + (21 / 0.9 - 5) * 20, abs=0.02)
    assert (dt(charge["end"]) - dt(charge["start"])).total_seconds() == pytest.approx(
        (5 / 0.9 + 10 / (0.6 * 0.9) + 6 / (0.3 * 0.9)) * 60, abs=0.1
    )


def test_explicit_return_not_duplicated_and_recovery_across_midnight():
    doc = seed(LoadRequest(vehicle_count=1))
    v = doc["vehicles"]["SIM-001"]
    depot = doc["depots"][v["depot_id"]]
    terminal = {
        **deepcopy(v["deliveries"][-1]),
        "trip_id": "HOME",
        "sequence": 4,
        "name": "Depot",
        "is_return": True,
        "lat": depot["lat"],
        "lon": depot["lon"],
        "service_minutes": 0,
    }
    v["deliveries"].append(terminal)
    p = optimize(doc, v["vin"])["plan"]
    assert len(p["operations"]) == 4 and p["operations"][-1]["trip_id"] == "HOME"
    doc["clock"] = (dt(doc["clock"]) + timedelta(days=1)).isoformat()
    doc["stations"] = {}
    result = optimize(doc, v["vin"], recovery=True)
    assert "plan" in result, result
    assert result["plan"]["operations"][0]["lateness_minutes"] > 720
    assert result["plan"]["operations"][0]["deadline"] == v["deliveries"][0]["deadline"]


def test_tariff_precedence_wrap_dates_and_gaps():
    now = datetime(2026, 10, 4, 23, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    station = dict(
        price=30,
        tariffs=[
            dict(id="b", start_minute=1380, end_minute=60, price=12, priority=2),
            dict(id="a", start_minute=1380, end_minute=60, price=10, priority=2),
            dict(
                id="expired",
                start_minute=0,
                end_minute=0,
                price=1,
                priority=9,
                until="2026-10-03",
            ),
        ],
    )
    assert price_at(now, station) == 10
    assert price_at(now + timedelta(hours=3), station) == 30
    assert (
        price_at(
            now + timedelta(hours=3),
            station,
            [dict(id="depot", start_minute=0, end_minute=0, price=20)],
        )
        == 20
    )


def test_no_charge_and_tick_size_invariance():
    doc = seed(LoadRequest(vehicle_count=1))
    plan = optimize(doc, "SIM-001")["plan"]
    assert not any(o["kind"] == "CHARGE" for o in plan["operations"])
    attach(doc, plan)
    other = deepcopy(doc)
    advance(doc, 3 * 3600)
    for _ in range(180):
        advance(other, 60)
    assert doc["vehicles"]["SIM-001"]["state"] == "COMPLETED"
    assert other["vehicles"]["SIM-001"]["energy_kwh"] == pytest.approx(
        doc["vehicles"]["SIM-001"]["energy_kwh"]
    )
    assert [d["completed_at"] for d in other["vehicles"]["SIM-001"]["deliveries"]] == [
        d["completed_at"] for d in doc["vehicles"]["SIM-001"]["deliveries"]
    ]


def test_travel_trigger_does_not_slide_with_subsecond_ticks():
    doc = seed(LoadRequest(vehicle_count=1))
    plan = optimize(doc, "SIM-001")["plan"]
    attach(doc, plan)
    doc["events"] = [
        dict(
            event_id="moving",
            at=plan["operations"][0]["depart"],
            kind="ENERGY_LOSS",
            trigger="TRAVELLING",
            vin="SIM-001",
            value=0,
            status="PENDING",
        )
    ]
    other = deepcopy(doc)
    advance(doc, 601)
    advance(other, 600.5)
    assert other["events"][0]["status"] == "PENDING"
    advance(other, 0.5)
    assert other["events"][0]["applied_at"] == doc["events"][0]["applied_at"]
    assert (
        doc["vehicles"]["SIM-001"]["state"]
        == other["vehicles"]["SIM-001"]["state"]
        == "ASSISTANCE"
    )


def test_service_survives_interruption_and_replan():
    doc = seed(LoadRequest(vehicle_count=1))
    p = optimize(doc, "SIM-001")["plan"]
    attach(doc, p)
    first = p["operations"][0]
    advance(doc, (dt(first["start"]) - dt(doc["clock"])).total_seconds() + 30)
    v = doc["vehicles"]["SIM-001"]
    assert v["state"] == "SERVICING"
    until = v["service_until"]
    interrupt(doc, v["vin"], "Replan required")
    v["deliveries"][0]["service_minutes"] = 60
    v["service_until"] = dt(until) + timedelta(minutes=30)
    result = optimize(doc, v["vin"])
    assert "plan" in result, result
    assert dt(result["plan"]["operations"][0]["depart"]) >= dt(v["service_until"])


def test_sequence_not_departure_deadline_order():
    doc = seed(LoadRequest(vehicle_count=1))
    v = doc["vehicles"]["SIM-001"]
    v["deliveries"][1]["ready_at"] = v["deliveries"][0]["ready_at"]
    v["deliveries"][1]["deadline"] = v["deliveries"][0]["deadline"]
    v["deliveries"].reverse()
    result = optimize(doc, v["vin"])
    assert "plan" in result, result
    assert [
        o["trip_id"] for o in result["plan"]["operations"] if o["kind"] == "DELIVERY"
    ] == ["SIM-001-D1", "SIM-001-D2", "SIM-001-D3"]


def test_stale_snapshot_and_idempotent_approval():
    doc = seed(LoadRequest(vehicle_count=1))
    plan = optimize(doc, "SIM-001")["plan"]
    doc["plans"][plan["plan_id"]] = plan
    doc["vehicles"]["SIM-001"]["temperature_c"] += 1
    with pytest.raises(HTTPException):
        approve(doc, plan["plan_id"], Approval(run_id=doc["run_id"]))
    assert not bookings(doc)
    doc["vehicles"]["SIM-001"]["temperature_c"] -= 1
    approve(doc, plan["plan_id"], Approval(run_id=doc["run_id"]))
    assert approve(doc, plan["plan_id"], Approval(run_id=doc["run_id"]))["idempotent"]


def test_old_telemetry_and_deterministic_seed():
    a = seed(LoadRequest(scenario="EDGE_CASE_DAY"))
    b = seed(LoadRequest(scenario="EDGE_CASE_DAY"))
    assert a["vehicles"] == b["vehicles"] and a["events"] == b["events"]
    assert len({v["case"] for v in a["vehicles"].values()}) == 12
    assert all(
        v["lat"] == a["depots"]["SIM-DEPOT"]["lat"] for v in a["vehicles"].values()
    )
    with pytest.raises(HTTPException):
        telemetry(
            a,
            dict(
                run_id=b["run_id"],
                vin="SIM-001",
                sequence=1,
                energy_kwh=0,
                temperature_c=30,
                health_fault=False,
            ),
        )


def test_recovery_preserves_deadlines_and_requires_acknowledgement():
    doc = seed(LoadRequest(vehicle_count=1))
    v = doc["vehicles"]["SIM-001"]
    v["deliveries"][0]["deadline"] = doc["clock"]
    doc["stations"] = {}
    deadlines = [d["deadline"] for d in v["deliveries"]]
    assert optimize(doc, v["vin"])["status"] == "INFEASIBLE_MODEL"
    result = optimize(doc, v["vin"], recovery=True)
    assert "plan" in result, result
    p = result["plan"]
    doc["plans"][p["plan_id"]] = p
    assert p["operations"][0]["lateness_minutes"] > 0
    with pytest.raises(HTTPException):
        approve(doc, p["plan_id"], Approval(run_id=doc["run_id"]))
    approve(
        doc, p["plan_id"], Approval(run_id=doc["run_id"], acknowledge_recovery=True)
    )
    assert deadlines == [d["deadline"] for d in v["deliveries"]]


def test_independent_validator_catches_corruption():
    doc = seed(LoadRequest(vehicle_count=1))
    p = optimize(doc, "SIM-001")["plan"]
    p["operations"][0]["energy_arrival"] += 1
    assert "Arrival energy mismatch" in validate(doc, p)
    p["operations"].pop()
    assert "Incomplete journey" in validate(doc, p)


def test_delivery_first_and_after_last_delivery_charging():
    doc = fixture()
    v = doc["vehicles"]["SIM-001"]
    v["energy_kwh"] = 23
    v["deliveries"][1]["deadline"] = (
        dt(doc["clock"]) + timedelta(minutes=140)
    ).isoformat()
    result = optimize(doc, v["vin"])
    assert "plan" in result, result
    assert result["plan"]["operations"][0]["kind"] == "DELIVERY"
    assert result["plan"]["total_cost"] == pytest.approx(300, abs=0.02)
    doc = fixture()
    v = doc["vehicles"]["SIM-001"]
    v["energy_kwh"] = 50
    doc["roads"]["A>CHEAP"] = dict(energy=200, minutes=200)
    doc["roads"]["C>CHEAP"] = dict(energy=1, minutes=1)
    doc["roads"]["CHEAP>DEPOT"] = dict(energy=7, minutes=7)
    result = optimize(doc, v["vin"])
    assert "plan" in result, result
    assert [o["kind"] for o in result["plan"]["operations"]][-3:] == [
        "DELIVERY",
        "CHARGE",
        "RETURN",
    ]
    assert result["plan"]["total_cost"] == pytest.approx(30, abs=0.02)


def test_fastest_alternative_changes_energy_and_total_cost():
    doc = fixture()
    cheap = optimize(doc, "SIM-001")["plan"]
    fast = optimize(doc, "SIM-001", fastest=True)["plan"]
    assert fast["total_cost"] > cheap["total_cost"]
    assert dt(fast["operations"][-1]["end"]) < dt(cheap["operations"][-1]["end"])
    assert not validate(doc, fast)


def test_power_change_stops_drawing_and_retains_physical_release():
    doc = fixture()
    doc["policy"]["release_minutes"] = 1
    p = optimize(doc, "SIM-001")["plan"]
    attach(doc, p)
    advance(doc, 20 * 60)
    v = doc["vehicles"]["SIM-001"]
    energy = v["energy_kwh"]
    assert v["state"] == "CHARGING"
    apply_event(doc, dict(kind="DEPOT_POWER", depot_id="SIM-DEPOT", value=1))
    assert p["status"] == "INTERRUPTED"
    assert len(bookings(doc)) == 1 and bookings(doc)[0]["status"] == "RELEASING"
    assert v["state"] == "RELEASING"
    assert optimize(doc, v["vin"])["status"] == "ERROR"
    advance(doc, 120)
    assert v["energy_kwh"] == pytest.approx(energy) and not bookings(doc)


def test_cancel_while_queuing_does_not_invent_physical_occupancy():
    doc = fixture()
    doc["policy"]["release_minutes"] = 1
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
    p = optimize(doc, "SIM-001")["plan"]
    attach(doc, p)
    advance(doc, 60)
    assert doc["vehicles"]["SIM-001"]["state"] == "QUEUING"
    interrupt(doc, "SIM-001", "Cancel while waiting")
    assert not bookings(doc)
    assert doc["vehicles"]["SIM-001"].get("release_until") is None


def test_zero_energy_at_compatible_charger_and_stranded_away():
    doc = fixture()
    doc["vehicles"]["SIM-001"]["energy_kwh"] = 0
    p = optimize(doc, "SIM-001")["plan"]
    assert p["operations"][0]["energy_arrival"] == pytest.approx(0)
    doc["stations"]["EXP"]["connector"] = "OTHER"
    assert optimize(doc, "SIM-001")["status"] == "INFEASIBLE_MODEL"


def test_tight_gap_is_not_extended_over_next_booking():
    doc = fixture()
    now = dt(doc["clock"])
    doc["external_bookings"] += [
        dict(
            charger_id="EXP",
            port=1,
            start=now + timedelta(minutes=5),
            end=now + timedelta(minutes=200),
            power_kw=60,
        )
    ]
    assert optimize(doc, "SIM-001")["status"] == "INFEASIBLE_MODEL"


def test_constrained_vehicle_gets_unique_reachable_resource_first():
    from app.services.runner import priority

    doc = fixture()
    v = doc["vehicles"]["SIM-001"]
    doc["external_bookings"] = []
    doc["policy"]["reserve_kwh"] = 0
    v.update(capacity_kwh=20, energy_kwh=0)
    v["deliveries"] = v["deliveries"][:1]
    v["deliveries"][0].update(
        service_minutes=0,
        deadline=(dt(doc["clock"]) + timedelta(minutes=25)).isoformat(),
    )
    doc["stations"]["EXP"]["price"] = 1
    doc["stations"]["CHEAP"].update(power_kw=60, price=5)
    for key in doc["roads"]:
        doc["roads"][key] = dict(
            energy=0 if key.split(">")[0] == key.split(">")[1] else 200,
            minutes=0 if key.split(">")[0] == key.split(">")[1] else 200,
        )
    for a, b, e, t in [
        ("EXP", "A", 10, 10),
        ("EXP", "CHEAP", 2, 5),
        ("CHEAP", "A", 8, 5),
        ("A", "DEPOT", 2, 5),
    ]:
        doc["roads"][f"{a}>{b}"] = dict(energy=e, minutes=t)
    flex = deepcopy(v)
    flex.update(vin="FLEX", energy_kwh=5)
    doc["vehicles"]["FLEX"] = flex
    order = sorted(doc["vehicles"], key=lambda vin: priority(doc, vin))
    assert order == ["SIM-001", "FLEX"]
    first = optimize(doc, order[0])
    assert "plan" in first, first
    attach(doc, first["plan"])
    second = optimize(doc, order[1])
    assert "plan" in second, second
    assert [
        o["charger_id"] for o in second["plan"]["operations"] if o["kind"] == "CHARGE"
    ] == ["CHEAP"]
    assert not validate(doc, second["plan"])
