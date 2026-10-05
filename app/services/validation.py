"""Independent forward simulation before publishing and committing a journey."""

from datetime import timedelta
from math import isfinite

from pydantic import ValidationError

from app.domain import JourneyPlan, dt, effective_capacity
from app.services.ledger import bookings
from app.services.pricing import session

EPS = 2e-4
TIME_EPS = timedelta(seconds=0.05)


def validate(doc, plan):
    from app.services.optimizer import leg, stops

    errors = []
    try:
        JourneyPlan.model_validate(plan)
    except ValidationError as exc:
        return ["Invalid journey contract: " + str(exc)]
    v = doc["vehicles"].get(plan["vin"])
    if not v or plan["run_id"] != doc["run_id"]:
        return ["Run or vehicle no longer exists"]
    p = doc["policy"]
    cap = effective_capacity(v)
    energy = v["energy_kwh"]
    previous = v
    cursor = max(
        dt(doc["clock"]),
        dt(v["service_until"]) if v.get("service_until") else dt(doc["clock"]),
    )
    expected = stops(doc, v)
    index = 0
    total = 0
    existing = bookings(doc, exclude_plan=plan.get("replaces")) + doc.get(
        "external_bookings", []
    )
    charges = []
    if v["health_fault"] or v["temperature_c"] >= 60:
        errors.append("Vehicle health blocks movement")
    reserve = plan["reserve_kwh"]
    if reserve < p["reserve_kwh"] - EPS and not plan["recovery"]:
        errors.append("Unacknowledged reserve exception")
    initial_exception = plan.get("initial_reserve_exception", False)
    if initial_exception and (
        not plan["recovery"]
        or reserve < p["reserve_kwh"] - EPS
        or plan["operations"][0]["kind"] != "CHARGE"
    ):
        errors.append("Invalid initial reserve exception")
    for operation_index, o in enumerate(plan["operations"]):
        if not all(
            isfinite(o.get(k, 0))
            for k in ("energy_arrival", "energy_end", "cost", "grid_kwh", "power_kw")
        ):
            errors.append("Non-finite operation values")
            continue
        depart, arrival, start, end = [
            dt(o[k]) for k in ("depart", "arrival", "start", "end")
        ]
        if depart + TIME_EPS < cursor:
            errors.append("Departure precedes service completion/current clock")
        consumed, minutes = leg(doc, v, previous, o)
        if abs((arrival - depart).total_seconds() - minutes * 60) > 0.1:
            errors.append("Travel duration does not match route")
        energy -= consumed
        already_at_charger = (
            o["kind"] == "CHARGE" and not charges and index == 0 and consumed < 1e-9
        )
        initial_approach = (
            initial_exception and operation_index == 0 and o["kind"] == "CHARGE"
        )
        if energy < -EPS or (
            energy < reserve - EPS and not already_at_charger and not initial_approach
        ):
            errors.append("Arrival reserve violated")
        if abs(energy - o["energy_arrival"]) > EPS:
            errors.append("Arrival energy mismatch")
        if start + TIME_EPS < arrival or end + TIME_EPS < start:
            errors.append("Invalid operation times")
        if (
            index < len(expected)
            and expected[index].get("ready_at")
            and depart + TIME_EPS < dt(expected[index]["ready_at"])
        ):
            errors.append("Departure before customer readiness")
        ready = cursor
        if index < len(expected) and expected[index].get("ready_at"):
            ready = max(ready, dt(expected[index]["ready_at"]))
        if depart > ready + TIME_EPS:
            errors.append("Leg must begin when vehicle and route are ready")
        if o["kind"] == "CHARGE":
            s = doc["stations"].get(o["charger_id"])
            if not s:
                errors.append("Unknown charger")
                continue
            depot = doc["depots"][s["depot_id"]]
            if s["status"] != "AVAILABLE" or s["connector"] != v["connector"]:
                errors.append("Charger unavailable or incompatible")
            if o["port"] not in range(1, s["port_count"] + 1):
                errors.append("Invalid port")
            if (
                o["power_kw"] <= 0
                or o["power_kw"]
                > min(s["power_kw"], v["max_power_kw"], depot["power_limit_kw"]) + EPS
            ):
                errors.append("Invalid reserved power")
                continue
            if start + TIME_EPS < arrival + timedelta(
                minutes=p["waiting_allowance_minutes"]
            ):
                errors.append("Charging waiting allowance omitted")
            if o["energy_end"] > cap + EPS or o["energy_end"] < energy - EPS:
                errors.append("Invalid charging target")
                continue
            seconds, grid, cost = session(
                start + timedelta(minutes=p["connection_minutes"]),
                max(0, energy),
                min(cap, o["energy_end"]),
                cap,
                o["power_kw"],
                p["efficiency"],
                s,
                depot.get("tariffs", []),
                p["taper"],
            )
            predicted = start + timedelta(
                seconds=seconds, minutes=p["connection_minutes"] + p["release_minutes"]
            )
            if abs((end - predicted).total_seconds()) > 0.15:
                errors.append("Charging duration mismatch")
            if abs(grid - o["grid_kwh"]) > EPS or abs(cost - o["cost"]) > 0.01:
                errors.append("Charging tariff integration mismatch")
            for r in existing + charges:
                if (
                    r["charger_id"] == o["charger_id"]
                    and r["port"] == o["port"]
                    and start < dt(r["end"]) - TIME_EPS
                    and end > dt(r["start"]) + TIME_EPS
                ):
                    errors.append("Port reservation conflict")
            charges.append(o)
            energy = o["energy_end"]
            total += cost
        else:
            if index >= len(expected):
                errors.append("Extra delivery/return operation")
                continue
            d = expected[index]
            index += 1
            if o["kind"] == "RETURN":
                depot = doc["depots"][v["depot_id"]]
                if (
                    abs(o["lat"] - depot["lat"]) > 1e-7
                    or abs(o["lon"] - depot["lon"]) > 1e-7
                ):
                    errors.append("Return endpoint is not the depot")
            if o.get("trip_id") != d.get("trip_id") or o["kind"] != d.get(
                "kind", "DELIVERY"
            ):
                errors.append("Customer sequence/return mismatch")
            if d.get("accepts_at") and start + TIME_EPS < dt(d["accepts_at"]):
                errors.append("Service before acceptance window")
            if d.get("deadline"):
                if not o.get("deadline") or dt(o["deadline"]) != dt(d["deadline"]):
                    errors.append("Original arrival deadline changed")
                predicted_late = max(
                    0, (arrival - dt(d["deadline"])).total_seconds() / 60
                )
                if abs(predicted_late - o.get("lateness_minutes", 0)) > 0.001:
                    errors.append("Displayed lateness does not match actual arrival")
            if (
                abs((end - start).total_seconds() - 60 * d.get("service_minutes", 0))
                > 0.1
            ):
                errors.append("Service duration mismatch")
            if (
                d.get("deadline")
                and arrival > dt(d["deadline"]) + TIME_EPS
                and not plan["recovery"]
            ):
                errors.append("Arrival deadline violated")
            if abs(o["energy_end"] - energy) > EPS:
                errors.append("Non-charging operation changes energy")
        if (
            abs(o["lat"] - (s if o["kind"] == "CHARGE" else d)["lat"]) > 1e-7
            or abs(o["lon"] - (s if o["kind"] == "CHARGE" else d)["lon"]) > 1e-7
        ):
            errors.append("Stop location mismatch")
        previous = o
        cursor = end
    if index != len(expected):
        errors.append("Incomplete journey")
    if abs(total - plan["total_cost"]) > 0.01:
        errors.append("Total cost mismatch")
    if cursor > dt(doc["clock"]) + timedelta(minutes=p["horizon_minutes"]) + TIME_EPS:
        errors.append("Planning horizon exceeded")
    for depot_id, depot in doc["depots"].items():
        rows = [
            r
            for r in existing + charges
            if doc["stations"][r["charger_id"]]["depot_id"] == depot_id
        ]
        times = sorted({dt(r[t]) for r in rows for t in ("start", "end")})
        for a, b in zip(times, times[1:]):
            middle = a + (b - a) / 2
            if (
                any(dt(c["start"]) <= middle < dt(c["end"]) for c in charges)
                and sum(
                    r["power_kw"]
                    for r in rows
                    if dt(r["start"]) <= middle < dt(r["end"])
                )
                > depot["power_limit_kw"] + EPS
            ):
                errors.append("Depot power capacity exceeded")
                break
    return list(dict.fromkeys(errors))
