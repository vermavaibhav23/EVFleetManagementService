"""Event-driven execution; simulated time never depends on the number of ticks."""

from datetime import timedelta

from app.domain import dt, effective_capacity
from app.services.energy import charge_for_seconds
from app.services.geometry import distance
from app.services.pricing import intervals


def record(doc, kind, message, vin=None):
    doc["history"].append(dict(at=doc["clock"], kind=kind, message=message, vin=vin))
    doc["history"] = doc["history"][-1000:]


def stopped_state(doc, v):
    if v["health_fault"] or v["temperature_c"] >= 60:
        return "ASSISTANCE"
    if v["energy_kwh"] <= 1e-7 and not any(
        s["status"] == "AVAILABLE"
        and s["connector"] == v["connector"]
        and distance(v, s) < 1e-6
        and doc["depots"][s["depot_id"]]["power_limit_kw"] > 0
        for s in doc["stations"].values()
    ):
        return "ASSISTANCE"
    return "WAITING_REVIEW"


def interrupt(doc, vin, reason):
    v = doc["vehicles"][vin]
    plan = doc["plans"].get(v.get("plan_id"))
    if plan:
        plan["status"] = "INTERRUPTED"
        for op in plan["operations"]:
            if (
                op["status"] == "ACTIVE"
                and op["kind"] == "CHARGE"
                and op.get("actual_arrival")
                and dt(op["start"]) <= dt(doc["clock"])
            ):
                # Preserve physical occupancy through unplug/release, independently of cancellation.
                op["status"] = "RELEASING"
                op["end"] = dt(doc["clock"]) + timedelta(
                    minutes=doc["policy"]["release_minutes"]
                )
                v["release_until"] = op["end"]
            elif op["status"] in ("PLANNED", "ACTIVE"):
                op["status"] = "CANCELLED"
    if v["state"] != "SERVICING":
        v["state"] = (
            "RELEASING"
            if v.get("release_until") and dt(v["release_until"]) > dt(doc["clock"])
            else stopped_state(doc, v)
        )
    v["plan_id"] = None
    v["incident"] = reason
    record(doc, "INTERRUPTED", reason, vin)


def apply_event(doc, event):
    kind = event["kind"]
    vin = event.get("vin")
    if kind == "DEPOT_POWER":
        doc["depots"][event["depot_id"]]["power_limit_kw"] = event["value"]
        affected = [
            v["vin"]
            for v in doc["vehicles"].values()
            if v.get("plan_id")
            and any(
                o["kind"] == "CHARGE"
                and doc["stations"][o["charger_id"]]["depot_id"] == event["depot_id"]
                and o["status"] in ("PLANNED", "ACTIVE")
                for o in doc["plans"][v["plan_id"]]["operations"]
            )
        ]
    elif kind == "CHARGER_STATUS":
        doc["stations"][event["charger_id"]]["status"] = event["value"]
        affected = [
            v["vin"]
            for v in doc["vehicles"].values()
            if v.get("plan_id")
            and any(
                o.get("charger_id") == event["charger_id"]
                and o["status"] in ("PLANNED", "ACTIVE")
                for o in doc["plans"][v["plan_id"]]["operations"]
            )
        ]
    elif kind == "SERVICE_DELAY":
        affected = [vin]
        v = doc["vehicles"][vin]
        if v.get("service_until"):
            v["service_until"] = dt(v["service_until"]) + timedelta(
                minutes=event["value"]
            )
        else:
            remaining = sorted(
                [d for d in v["deliveries"] if d["status"] == "PLANNED"],
                key=lambda d: d["sequence"],
            )
            if remaining:
                remaining[0]["service_minutes"] += event["value"]
    else:
        affected = [vin]
        field = {
            "CONSUMPTION": "consumption_kwh_km",
            "ENERGY_LOSS": "energy_kwh",
            "HEALTH": "health_fault",
        }[kind]
        doc["vehicles"][vin][field] = event["value"]
    message = {
        "DEPOT_POWER": "Available site power changed. Review a new charging plan.",
        "CHARGER_STATUS": "Charger availability changed. Review another charging option.",
        "SERVICE_DELAY": "Unloading is taking longer. Review the remaining delivery times.",
        "CONSUMPTION": "Battery use increased. Vehicle stopped for a new journey review.",
        "ENERGY_LOSS": "Battery energy changed. Check whether charging or physical assistance is needed; nobody has been dispatched.",
        "HEALTH": "Vehicle health changed. A health check is required before continuing.",
    }[kind]
    for vehicle in affected:
        interrupt(doc, vehicle, message)
    event["status"] = "APPLIED"
    event["applied_at"] = doc["clock"]
    record(doc, kind, "Scenario event applied", vin)


def _vehicle(doc, v, left, right):
    p = doc["policy"]
    if v.get("release_until"):
        if dt(v["release_until"]) > right:
            return
        v["release_until"] = None
        v["state"] = stopped_state(doc, v)
    # Service already begun survives cancellation/replanning and process restarts.
    if v.get("service_until") and dt(v["service_until"]) <= right:
        for d in v["deliveries"]:
            if d["status"] == "SERVICING":
                d.update(status="COMPLETED", completed_at=v["service_until"])
        if not v.get("plan_id"):
            v["state"] = stopped_state(doc, v)
        v["service_until"] = None
    plan = doc["plans"].get(v.get("plan_id"))
    if not plan or plan["status"] not in ("APPROVED", "EXECUTING"):
        return
    for o in plan["operations"][v["operation_index"] :]:
        depart, arrival, start, end = [
            dt(o[k]) for k in ("depart", "arrival", "start", "end")
        ]
        if right < depart:
            return
        if o["status"] == "PLANNED":
            if left > depart + timedelta(seconds=0.1):
                interrupt(doc, v["vin"], "Scheduled departure missed")
                return
            if v.get("service_until") and dt(v["service_until"]) > depart + timedelta(
                seconds=0.1
            ):
                interrupt(doc, v["vin"], "Service is not complete")
                return
            o.update(
                status="ACTIVE",
                origin_lat=v["lat"],
                origin_lon=v["lon"],
                origin_energy=v["energy_kwh"],
            )
            plan["status"] = "EXECUTING"
        if right < arrival:
            v.pop("node", None)
            fraction = (right - depart).total_seconds() / max(
                1e-9, (arrival - depart).total_seconds()
            )
            v["lat"] = o["origin_lat"] + (o["lat"] - o["origin_lat"]) * fraction
            v["lon"] = o["origin_lon"] + (o["lon"] - o["origin_lon"]) * fraction
            v["energy_kwh"] = (
                o["origin_energy"]
                + (o["energy_arrival"] - o["origin_energy"]) * fraction
            )
            v["state"] = "TRAVELLING"
            return
        if not o.get("actual_arrival"):
            o["actual_arrival"] = arrival
            v.update(lat=o["lat"], lon=o["lon"], energy_kwh=o["energy_arrival"])
            if doc.get("roads"):
                v["node"] = o.get("node") or o.get("trip_id") or o.get("charger_id")
            if o.get("trip_id"):
                d = next(d for d in v["deliveries"] if d["trip_id"] == o["trip_id"])
                d["arrived_at"] = arrival
        if o["kind"] == "CHARGE":
            s = doc["stations"][o["charger_id"]]
            if s["status"] != "AVAILABLE":
                interrupt(doc, v["vin"], "Charger unavailable")
                return
            charging_start = start + timedelta(minutes=p["connection_minutes"])
            charging_end = end - timedelta(minutes=p["release_minutes"])
            a = max(left, charging_start)
            b = min(right, charging_end)
            if b > a:
                cap = effective_capacity(v)
                for ta, tb, rate in intervals(
                    a, b, s, doc["depots"][s["depot_id"]].get("tariffs", [])
                ):
                    seconds = (tb - ta).total_seconds()
                    if p["taper"]:
                        soc, added = charge_for_seconds(
                            v["energy_kwh"] / cap * 100,
                            o["target_soc"],
                            cap,
                            o["power_kw"],
                            p["efficiency"],
                            seconds,
                        )
                    else:
                        added = min(
                            o["energy_end"] - v["energy_kwh"],
                            seconds / 3600 * o["power_kw"] * p["efficiency"],
                        )
                    v["energy_kwh"] += added
                    o["actual_grid_kwh"] = (
                        o.get("actual_grid_kwh", 0) + added / p["efficiency"]
                    )
                    o["actual_cost"] = (
                        o.get("actual_cost", 0) + added / p["efficiency"] * rate
                    )
            v["state"] = (
                "QUEUING"
                if right < start
                else "CONNECTING"
                if right < charging_start
                else "CHARGING"
                if right < charging_end
                else "RELEASING"
            )
        else:
            if o["kind"] == "DELIVERY" and right >= start:
                d = next(d for d in v["deliveries"] if d["trip_id"] == o["trip_id"])
                d["status"] = "SERVICING"
                v["service_until"] = end
            v["state"] = "WAITING_WINDOW" if right < start else "SERVICING"
        if right >= start:
            o.setdefault("actual_start", start)
        if right < end:
            return
        o.update(status="COMPLETED", actual_end=end)
        if o.get("trip_id"):
            d = next(d for d in v["deliveries"] if d["trip_id"] == o["trip_id"])
            d.update(status="COMPLETED", completed_at=end)
        v["service_until"] = None
        v["operation_index"] += 1
        # The same tick can finish a charge, drive, and begin unloading.
        left = end
    plan["status"] = "COMPLETED"
    v.update(state="COMPLETED", plan_id=None, incident=None)
    record(doc, "COMPLETED", "Whole journey completed", v["vin"])


def event_time(doc, event):
    earliest = max(dt(doc["clock"]), dt(event["at"]))
    if event.get("trigger") != "TRAVELLING":
        return earliest
    vehicle = doc["vehicles"][event["vin"]]
    plan = doc["plans"].get(vehicle.get("plan_id"))
    if not plan or plan["status"] not in ("APPROVED", "EXECUTING"):
        return None
    for operation in plan["operations"]:
        if operation["status"] not in ("PLANNED", "ACTIVE"):
            continue
        depart = max(dt(event["at"]), dt(operation["depart"]))
        arrival = dt(operation["arrival"])
        if arrival > max(depart, dt(doc["clock"])):
            return max(
                dt(doc["clock"]),
                depart + min(timedelta(seconds=1), (arrival - depart) / 2),
            )
    return None


def advance(doc, seconds):
    left = dt(doc["clock"])
    target = left + timedelta(seconds=seconds)
    while True:
        candidates = []
        for pending in doc["events"]:
            if pending["status"] != "PENDING":
                continue
            when = event_time(doc, pending)
            if when is not None and when <= target:
                candidates.append((when, pending["event_id"], pending))
        when, _, event = (
            min(candidates, key=lambda row: (row[0], row[1]))
            if candidates
            else (target, "", None)
        )
        right = max(left, when)
        for v in doc["vehicles"].values():
            _vehicle(doc, v, left, right)
        doc["clock"] = right.isoformat()
        for plan in doc["plans"].values():
            for o in plan["operations"]:
                if o["status"] == "RELEASING" and dt(o["end"]) <= right:
                    o.update(status="CANCELLED", actual_end=dt(o["end"]))
        if event:
            apply_event(doc, event)
        left = right
        if event is None:
            break
