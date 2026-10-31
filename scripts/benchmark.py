"""Solver-assisted station-first baseline versus full candidate search.

Both use identical constraints. Baseline tries stations nearest-first and chooses
the first feasible single-station journey. It is not a pure greedy scheduler.
"""

import json
import sys
from copy import deepcopy
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain import LoadRequest
from app.services.optimizer import candidate_stations, optimize
from app.services.seed import seed
from app.services.validation import validate


def measure(doc, vin, baseline=False):
    started = monotonic()
    result = {"status": "INFEASIBLE_MODEL"}
    if baseline:
        for station in candidate_stations(doc, doc["vehicles"][vin]):
            limited = deepcopy(doc)
            limited["stations"] = {station["charger_id"]: station}
            limited["external_bookings"] = [
                r
                for r in doc.get("external_bookings", [])
                if r["charger_id"] in limited["stations"]
            ]
            # Preserve every committed reservation; the benchmark uses only the everyday snapshot.
            limited["plans"] = {}
            result = optimize(limited, vin)
            if "plan" in result:
                assert not validate(limited, result["plan"])
                break
    else:
        result = optimize(doc, vin)
        if "plan" in result:
            assert not validate(doc, result["plan"])
    plan = result.get("plan")
    return {
        "status": result["status"],
        "cost_inr": round(plan["total_cost"], 2) if plan else None,
        "late_deliveries": sum(
            o.get("lateness_minutes", 0) > 0.001 for o in plan["operations"]
        )
        if plan
        else None,
        "seconds": round(monotonic() - started, 3),
    }


if __name__ == "__main__":
    doc = seed(LoadRequest(scenario="EVERYDAY_CHOICES", vehicle_count=4))
    # Compare the same uncommitted snapshot for both methods.
    doc["plans"] = {}
    rows = []
    for vin in ("SIM-001", "SIM-002", "SIM-003"):
        rows.append(
            {
                "vehicle": vin,
                "optimized": measure(doc, vin),
                "station_first": measure(doc, vin, True),
            }
        )
    print(
        json.dumps(
            {
                "method": "nearest-first single-station restriction with MILP; identical hard constraints",
                "results": rows,
            },
            indent=2,
        )
    )
