"""Complete, factual charger inventory for a vehicle's current review.

Not selected is not the same as infeasible. Never invent a cost/deadline rejection
for a station whose best complete journey has not been solved separately.
"""

from app.domain import dt
from app.services.ledger import bookings
from app.services.optimizer import candidate_stations, leg


def charger_review(doc, vin):
    v = doc["vehicles"][vin]
    candidates = {s["charger_id"] for s in candidate_stations(doc, v)}
    plans = [
        p
        for p in doc["plans"].values()
        if p["vin"] == vin
        and (
            p.get("review", {}).get("can_approve")
            or (
                p["plan_id"] == v.get("plan_id")
                and p["status"] in ("APPROVED", "EXECUTING")
            )
        )
    ]
    selected = {
        o["charger_id"]
        for p in plans
        for o in p["operations"]
        if o["kind"] == "CHARGE" and o["status"] not in ("COMPLETED", "CANCELLED")
    }
    reservations = bookings(doc) + doc.get("external_bookings", [])
    rows = []
    for s in sorted(
        doc["stations"].values(), key=lambda s: (s["name"], s["charger_id"])
    ):
        cid = s["charger_id"]
        power = min(
            s["power_kw"],
            v["max_power_kw"],
            doc["depots"][s["depot_id"]]["power_limit_kw"],
        )
        energy, _ = leg(doc, v, v, s)
        notes = []
        if s["status"] != "AVAILABLE":
            state, reason = (
                "UNAVAILABLE",
                f"Charger is {s['status'].lower().replace('_', ' ')}. Cannot book it now.",
            )
        elif s["connector"] != v["connector"]:
            state, reason = (
                "UNAVAILABLE",
                f"Connector {s['connector']} does not match this vehicle's {v['connector']}.",
            )
        elif power <= 0 or s["port_count"] <= 0:
            state, reason = (
                "UNAVAILABLE",
                "No usable charging power or port is available.",
            )
        elif cid in selected:
            state, reason = (
                "SELECTED",
                "Used in a current journey option or the remaining approved journey.",
            )
        elif v["state"] == "COMPLETED" or (plans and not selected):
            state, reason = "NOT_NEEDED", "Current journeys need no further charging."
        elif cid not in candidates:
            state, reason = (
                "NOT_SEARCHED",
                f"Outside the nearest {doc['policy']['max_stations']} compatible available chargers searched by this bounded model. Not proven unusable.",
            )
        else:
            state, reason = (
                "NOT_SELECTED",
                "Not used in the current journeys. This alone does not prove the charger is unusable or more expensive.",
            )
            if not plans:
                reason = "No current usable journey selects this charger. Recalculate to check complete routes and slots."
        if state != "UNAVAILABLE" and energy > v["energy_kwh"] + 0.001:
            notes.append(
                f"Cannot reach directly now: needs {energy:.2f} kWh; vehicle has {v['energy_kwh']:.2f} kWh. It may be reachable after charging elsewhere."
            )
        elif (
            state != "UNAVAILABLE"
            and energy > 0
            and v["energy_kwh"] - energy < doc["policy"]["reserve_kwh"] - 0.001
        ):
            notes.append(
                f"Direct arrival would leave {v['energy_kwh'] - energy:.2f} kWh, below the normal reserve. An initial reserve exception needs approval."
            )
        occupied = [
            dict(port=r["port"], start=r["start"], end=r["end"])
            for r in reservations
            if r["charger_id"] == cid and dt(r["end"]) > dt(doc["clock"])
        ]
        if occupied:
            notes.append(
                "Recorded booking or unplugging intervals are shown below; this charger remains unavailable."
                if state == "UNAVAILABLE"
                else "Booked intervals are listed below; other ports or later slots may still be usable."
            )
        rows.append(
            dict(
                charger_id=cid,
                name=s["name"],
                state=state,
                reason=reason,
                notes=notes,
                bookings=occupied,
            )
        )
    return rows
