from datetime import timedelta

from app.domain import dt
from app.services.optimizer import optimize
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


def test_no_route_when_deadlines_and_reserve_cannot_both_be_met():
    doc = tradeoff_fixture()
    assert not any("plan" in r for r in compare_options(doc, "SIM-001"))


def test_one_recommendation_preserves_reserve_through_return():
    doc = tradeoff_fixture()
    doc["vehicles"]["SIM-001"]["energy_kwh"] = 20
    results = compare_options(doc, "SIM-001")
    assert len(results) == 1
    plan = results[0]["plan"]
    assert not validate(doc, plan)
    assert all(o["energy_arrival"] >= 3 for o in plan["operations"])


def test_explicit_reserve_and_deadline_bypass_requests_are_rejected():
    doc = tradeoff_fixture()
    for options in (
        {"recovery": True},
        {"reserve_exception": True},
        {"approach_reserve_exception": True},
    ):
        assert "plan" not in optimize(doc, "SIM-001", **options)


def test_validator_rejects_tampered_reserve_even_with_acknowledgement():
    doc = fixture()
    plan = optimize(doc, "SIM-001")["plan"]
    plan.update(reserve_kwh=0, recovery=True)
    assert any("Interview policy" in error for error in validate(doc, plan))
