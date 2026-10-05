from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.domain import Approval, dt
from app.services.charger_review import charger_review
from app.services.control import approve
from app.services.execution import advance
from app.services.optimizer import fingerprint
from app.services.plan_review import review
from app.services.runner import compare_options
from app.services.telemetry import apply_reading
from app.services.validation import validate
from tests.test_telemetry_pipeline import reading
from tests.test_tradeoffs import tradeoff_fixture


def approach_fixture():
    doc = tradeoff_fixture()
    v = doc["vehicles"]["SIM-001"]
    v.update(node="START", energy_kwh=4)
    doc["stations"]["EXP"]["power_kw"] = 60
    for node in ("EXP", "A", "B", "DEPOT"):
        doc["roads"][f"START>{node}"] = dict(energy=200, minutes=200)
    doc["roads"]["START>EXP"] = dict(energy=2.45, minutes=17)
    for delivery in v["deliveries"]:
        delivery["deadline"] = dt(doc["clock"]) + timedelta(hours=3)
    return doc


def test_initial_exception_restores_reserve_instead_of_returning_empty():
    doc = approach_fixture()
    results = compare_options(doc, "SIM-001")
    plans = [r["plan"] for r in results if "plan" in r]
    assert len(plans) == 1
    plan = plans[0]
    assert plan["initial_reserve_exception"]
    assert plan["reserve_kwh"] == 3
    assert plan["operations"][0]["energy_arrival"] == pytest.approx(1.55)
    assert all(o["energy_arrival"] >= 3 - 0.0002 for o in plan["operations"][1:])
    assert plan["operations"][-1]["energy_end"] == pytest.approx(3, abs=0.0002)
    assert not validate(doc, plan)
    explanation = review(doc, plan)
    assert explanation["initial_reserve_exception"]
    assert not explanation["affected_customers"]
    assert "restore 3 kWh" in explanation["tradeoff"]
    doc["plans"][plan["plan_id"]] = plan
    with pytest.raises(HTTPException) as error:
        approve(doc, plan["plan_id"], Approval(run_id=doc["run_id"]))
    assert error.value.status_code == 422
    approve(
        doc, plan["plan_id"], Approval(run_id=doc["run_id"], acknowledge_recovery=True)
    )
    advance(doc, 6 * 3600)
    assert doc["vehicles"]["SIM-001"]["state"] == "COMPLETED"
    assert doc["vehicles"]["SIM-001"]["energy_kwh"] >= 3 - 0.0002


def test_initial_exception_is_not_a_whole_journey_waiver():
    doc = approach_fixture()
    plan = next(r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r)
    changed = deepcopy(plan)
    changed["initial_reserve_exception"] = False
    assert "Arrival reserve violated" in validate(doc, changed)
    changed = deepcopy(plan)
    changed["reserve_kwh"] = 0
    assert "Invalid initial reserve exception" in validate(doc, changed)
    changed = deepcopy(plan)
    changed["recovery"] = False
    assert "Invalid initial reserve exception" in validate(doc, changed)
    doc["roads"]["START>EXP"]["energy"] = 4.5
    assert not any("plan" in r for r in compare_options(doc, "SIM-001"))


def test_live_reading_honors_exception_only_on_first_charger_approach():
    doc = approach_fixture()
    plan = next(r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r)
    doc["plans"][plan["plan_id"]] = plan
    approve(
        doc, plan["plan_id"], Approval(run_id=doc["run_id"], acknowledge_recovery=True)
    )
    assert apply_reading(doc, reading(doc))["accepted"]
    assert doc["vehicles"]["SIM-001"]["plan_id"] == plan["plan_id"]
    stranded = deepcopy(doc)
    apply_reading(stranded, reading(stranded, sequence=102, energy_kwh=2))
    assert not stranded["vehicles"]["SIM-001"].get("plan_id")
    v = doc["vehicles"]["SIM-001"]
    v.update(operation_index=1, node="EXP")
    apply_reading(doc, reading(doc, sequence=102, energy_kwh=5))
    assert not v.get("plan_id")  # 5 - 3 kWh to A is below the restored 3-kWh floor.


def test_charger_inventory_never_disappears_and_does_not_invent_rejections():
    doc = approach_fixture()
    base = doc["stations"]["EXP"]
    for cid, changes in {
        "FAILED": dict(status="FAILED"),
        "PLUG": dict(connector="OTHER"),
        "POWER": dict(power_kw=0),
        "FAR": dict(lat=14, lon=78),
    }.items():
        doc["stations"][cid] = {**base, "charger_id": cid, "name": cid, **changes}
    doc["policy"]["max_stations"] = 1
    before = fingerprint(doc, "SIM-001")
    rows = charger_review(doc, "SIM-001")
    assert {r["charger_id"] for r in rows} == set(doc["stations"])
    indexed = {r["charger_id"]: r for r in rows}
    assert all(
        indexed[c]["state"] == "UNAVAILABLE" for c in ("FAILED", "PLUG", "POWER")
    )
    assert indexed["FAR"]["state"] == "NOT_SEARCHED"
    assert indexed["EXP"]["state"] == "NOT_SELECTED"
    assert "initial reserve exception" in indexed["EXP"]["notes"][0]
    doc["vehicles"]["SIM-001"]["charger_review"] = rows
    assert fingerprint(doc, "SIM-001") == before


def test_charger_inventory_survives_rejection_staleness_and_job_pruning():
    doc = approach_fixture()
    plan = next(r["plan"] for r in compare_options(doc, "SIM-001") if "plan" in r)
    doc["plans"][plan["plan_id"]] = plan
    plan["review"] = review(doc, plan)
    assert charger_review(doc, "SIM-001")[0]["state"] == "SELECTED"
    for status in ("REJECTED", "SUPERSEDED", "CANCELLED"):
        plan["status"] = status
        plan["review"] = review(doc, plan)
        doc["jobs"] = {}
        rows = charger_review(doc, "SIM-001")
        assert len(rows) == 1 and rows[0]["state"] == "NOT_SELECTED"
    plan["status"] = "PROPOSED"
    plan["fingerprint"] = "old-rules"
    plan["review"] = review(doc, plan)
    assert not plan["review"]["can_approve"]
    assert charger_review(doc, "SIM-001")[0]["state"] == "NOT_SELECTED"


def test_booked_charger_shows_actual_windows_without_claiming_permanent_rejection():
    doc = approach_fixture()
    doc["external_bookings"] = [
        dict(
            charger_id="EXP",
            port=1,
            power_kw=60,
            start=doc["clock"],
            end=dt(doc["clock"]) + timedelta(minutes=10),
        )
    ]
    row = charger_review(doc, "SIM-001")[0]
    assert len(row["bookings"]) == 1
    assert "later slots may still be usable" in row["notes"][-1]
