from datetime import timedelta

from app.domain import dt
from app.services.charger_review import charger_review
from app.services.optimizer import fingerprint
from app.services.plan_review import review
from app.services.runner import compare_options
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


def test_below_reserve_charger_approach_is_not_offered():
    doc = approach_fixture()
    assert not any("plan" in r for r in compare_options(doc, "SIM-001"))


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
    assert "cannot be approved" in indexed["EXP"]["notes"][0]
    doc["vehicles"]["SIM-001"]["charger_review"] = rows
    assert fingerprint(doc, "SIM-001") == before


def test_charger_inventory_survives_rejection_staleness_and_job_pruning():
    doc = approach_fixture()
    doc["vehicles"]["SIM-001"]["energy_kwh"] = 8
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
    doc["stations"]["EXP"]["status"] = "FAILED"
    failed = charger_review(doc, "SIM-001")[0]
    assert failed["state"] == "UNAVAILABLE"
    assert len(failed["notes"]) == 1
    assert "remains unavailable" in failed["notes"][0]
    assert "may still be usable" not in failed["notes"][0]
