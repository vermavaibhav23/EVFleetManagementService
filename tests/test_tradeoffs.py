from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.domain import Approval, dt
from app.services.control import approve
from app.services.execution import advance
from app.services.plan_review import review
from app.services.runner import compare_options, deadline_tradeoff, same_journey
from app.services.validation import validate
from tests.test_v2 import fixture


def tradeoff_fixture():
    doc = fixture()
    doc["policy"]["reserve_kwh"] = 3
    v = doc["vehicles"]["SIM-001"]
    v["energy_kwh"] = 8
    v["deliveries"] = v["deliveries"][:2]
    for i, delivery in enumerate(v["deliveries"]):
        delivery.update(
            service_minutes=0,
            deadline=(dt(doc["clock"]) + timedelta(minutes=10 + i * 5)).isoformat(),
        )
    doc["stations"] = {"EXP": doc["stations"]["EXP"]}
    doc["stations"]["EXP"]["power_kw"] = 6
    doc["external_bookings"] = []
    for row in doc["roads"].values():
        if row["energy"]:
            row.update(energy=200, minutes=200)
    for a, b, energy in (("EXP", "A", 3), ("A", "B", 2), ("B", "DEPOT", 2)):
        doc["roads"][f"{a}>{b}"] = dict(energy=energy, minutes=5)
    return doc


def test_comparison_offers_real_reserve_delay_tradeoff_and_requires_acknowledgement():
    doc = tradeoff_fixture()
    results = compare_options(doc, "SIM-001")
    assert results[0]["comparison_goal"] == "ON_TIME_WITH_RESERVE"
    assert "plan" not in results[0]
    plans = [r["plan"] for r in results if "plan" in r]
    assert len(plans) == 2
    safe, urgent = plans
    safe_review, urgent_review = review(doc, safe), review(doc, urgent)
    assert safe["reserve_kwh"] == 3 and urgent["reserve_kwh"] == 0
    assert {d["name"] for d in safe_review["affected_customers"]} == {"A", "B"}
    assert "A +15.0 min" in safe_review["tradeoff"]
    assert "B +15.0 min" in safe_review["tradeoff"]
    assert "emergency battery reserve" in urgent_review["tradeoff"]
    assert not urgent_review["affected_customers"]
    assert urgent["deadlines_saved"] == ["A", "B"]
    assert urgent_review["minimum_battery_pct"] == 1
    for plan in plans:
        fresh = deepcopy(doc)
        assert not validate(fresh, plan)
        fresh["plans"][plan["plan_id"]] = deepcopy(plan)
        with pytest.raises(HTTPException) as error:
            approve(fresh, plan["plan_id"], Approval(run_id=fresh["run_id"]))
        assert error.value.status_code == 422
        approve(
            fresh,
            plan["plan_id"],
            Approval(run_id=fresh["run_id"], acknowledge_recovery=True),
        )
        advance(fresh, 6 * 3600)
        assert fresh["vehicles"]["SIM-001"]["state"] == "COMPLETED"
        assert fresh["vehicles"]["SIM-001"]["energy_kwh"] >= plan["reserve_kwh"]


def test_comparison_does_not_invent_a_second_route_or_relax_physical_energy():
    doc = tradeoff_fixture()
    doc["vehicles"]["SIM-001"]["energy_kwh"] = 20
    results = compare_options(doc, "SIM-001")
    assert len(results) == 1 and results[0]["plan"]["total_cost"] == 0
    doc["vehicles"]["SIM-001"]["energy_kwh"] = 0
    doc["stations"] = {}
    results = compare_options(doc, "SIM-001")
    assert not any("plan" in r for r in results)
    assert {r["comparison_goal"] for r in results} == {
        "ON_TIME_WITH_RESERVE",
        "PROTECT_RESERVE",
        "PROTECT_DEADLINES",
    }


def test_no_emergency_option_when_deadlines_are_already_missed():
    doc = tradeoff_fixture()
    for d in doc["vehicles"]["SIM-001"]["deliveries"]:
        d["deadline"] = (dt(doc["clock"]) - timedelta(minutes=5)).isoformat()
    plans = [r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r]
    assert len(plans) == 1
    assert plans[0]["comparison_goal"] == "PROTECT_RESERVE"


def test_reaching_first_customer_is_not_enough_if_vehicle_would_be_stranded():
    doc = tradeoff_fixture()
    doc["vehicles"]["SIM-001"]["energy_kwh"] = 3
    doc["stations"] = {}
    # First delivery is reachable on time, but the next customer/depot is not.
    assert not any("plan" in r for r in compare_options(doc, "SIM-001"))


def test_only_physical_escape_is_an_alternative_not_a_fake_two_way_tradeoff():
    doc = tradeoff_fixture()
    doc["stations"] = {}
    # Eight kWh covers the seven-kWh route, but cannot retain three kWh reserve.
    plans = [r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r]
    assert len(plans) == 1
    assert plans[0]["comparison_goal"] == "REDUCED_RESERVE_ALTERNATIVE"
    assert not plans[0].get("deadlines_saved")
    assert not validate(doc, plans[0])


def test_solver_noise_is_not_an_earlier_return_alternative(monkeypatch):
    doc = tradeoff_fixture()
    from app.services.optimizer import optimize

    normal = optimize(doc, "SIM-001", recovery=True)
    duplicate = deepcopy(normal)
    duplicate["plan"]["total_cost"] += 0.00017
    for o in duplicate["plan"]["operations"]:
        for key in ("depart", "arrival", "start", "end"):
            o[key] = dt(o[key]) + timedelta(seconds=0.0135)
        o["energy_end"] += 0.000019
    assert same_journey(normal["plan"], duplicate["plan"])
    monkeypatch.setattr(
        "app.services.runner.optimize",
        lambda *args, **kwargs: deepcopy(
            duplicate if kwargs.get("fastest") else normal
        ),
    )
    plans = [r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r]
    assert len(plans) == 1
    # A genuine two-minute improvement is an ordinary alternative, not an emergency choice.
    duplicate = deepcopy(normal)
    duplicate["plan"]["operations"][-1]["end"] -= timedelta(minutes=2)
    plans = [r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r]
    assert len(plans) == 2
    assert plans[1]["comparison_goal"] == "EARLIER_RETURN"
    assert not same_journey(plans[0], plans[1])


def test_deadline_tradeoff_requires_real_risk_and_no_other_customer_worsening():
    doc = tradeoff_fixture()
    plans = [r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r]
    safe, urgent = plans
    no_risk = deepcopy(urgent)
    for o in no_risk["operations"]:
        o["energy_arrival"] = max(3, o["energy_arrival"])
    assert not deadline_tradeoff(safe, no_risk, 3)
    worsened = deepcopy(urgent)
    next(o for o in worsened["operations"] if o.get("trip_id") == "B")[
        "lateness_minutes"
    ] = 100
    assert not deadline_tradeoff(safe, worsened, 3)
