from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.domain import Approval, dt
from app.services.control import approve
from app.services.execution import advance
from app.services.plan_review import review
from app.services.runner import compare_options
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
