from app.domain import Approval, LoadRequest
from app.services.control import approve
from app.services.execution import advance
from app.services.optimizer import optimize
from app.services.runner import priority
from app.services.seed import seed
from app.services.validation import validate


def plan_and_approve(doc):
    results = []
    for vin in sorted(doc["vehicles"], key=lambda v: priority(doc, v)):
        result = optimize(doc, vin)
        results.append(result)
        if "plan" in result:
            p = result["plan"]
            assert not validate(doc, p)
            doc["plans"][p["plan_id"]] = p
            approve(doc, p["plan_id"], Approval(run_id=doc["run_id"]))
    return results


def test_normal_day_every_journey_returns_home():
    doc = seed(LoadRequest(vehicle_count=12))
    doc["policy"]["solver_seconds"] = 5
    results = plan_and_approve(doc)
    assert all("plan" in r for r in results), [
        (r["status"], r.get("reason")) for r in results
    ]
    advance(doc, (doc["policy"]["horizon_minutes"] + 1) * 60)
    assert all(v["state"] == "COMPLETED" for v in doc["vehicles"].values())
    for plan in doc["plans"].values():
        assert plan["status"] == "COMPLETED"
        assert all(o["status"] == "COMPLETED" for o in plan["operations"])
        assert (
            abs(
                sum(o.get("actual_cost", 0) for o in plan["operations"])
                - plan["total_cost"]
            )
            < 0.01
        )


def test_edge_events_leave_safe_states_without_automatic_recovery_approval():
    doc = seed(LoadRequest(scenario="EDGE_CASE_DAY"))
    doc["policy"]["solver_seconds"] = 5
    deadlines = {
        vin: [d["deadline"] for d in v["deliveries"]]
        for vin, v in doc["vehicles"].items()
    }
    results = plan_and_approve(doc)
    assert any(r["status"] == "INFEASIBLE_MODEL" for r in results)
    advance(doc, 180 * 60)
    assert all(e["status"] == "APPLIED" for e in doc["events"])
    assert doc["vehicles"]["SIM-011"]["energy_kwh"] == 0
    assert doc["vehicles"]["SIM-011"]["state"] == "ASSISTANCE"
    assert all(v["energy_kwh"] >= 0 for v in doc["vehicles"].values())
    assert all(not p.get("acknowledged_recovery") for p in doc["plans"].values())
    assert deadlines == {
        vin: [d["deadline"] for d in v["deliveries"]]
        for vin, v in doc["vehicles"].items()
    }
    assert doc["vehicles"]["SIM-001"]["lat"] == doc["depots"]["SIM-DEPOT"]["lat"]
