"""Manager-facing explanations derived from the actual journey and live bookings."""

from app.domain import dt, effective_capacity
from app.services.optimizer import fingerprint
from app.services.validation import validate


def issue_message(doc, plan):
    if doc.get("schema_version", 2) >= 3:
        from app.core.config import settings
        from app.services.simulator import fresh

        if not fresh(
            doc, doc["vehicles"][plan["vin"]], settings.telemetry_max_age_seconds
        ):
            return "Vehicle data is outdated. Wait for a fresh reading before booking."
    if dt(doc["clock"]) > dt(plan["valid_until"]):
        missed = [
            d["name"]
            for d in doc["vehicles"][plan["vin"]]["deliveries"]
            if d["status"] == "PLANNED"
            and not d.get("arrived_at")
            and not d.get("is_return")
            and dt(d["deadline"]) < dt(doc["clock"])
        ]
        if missed:
            return (
                "Arrival deadline passed for "
                + ", ".join(missed)
                + ". On-time delivery is no longer possible. Update options for a feasible delayed journey; nothing new is booked."
            )
        return "The simulation has moved on since these estimates were calculated. Refresh options for current delivery times and charging slots."
    errors = validate(doc, plan)
    if "Port reservation conflict" in errors:
        return (
            "A requested charging slot is already booked. Search for available slots."
        )
    if "Depot power capacity exceeded" in errors:
        return "The site no longer has enough power for these slots. Search for another time or station."
    if "Charger unavailable or incompatible" in errors:
        return "A charger is unavailable or incompatible. Search for another station."
    if "Vehicle health blocks movement" in errors:
        return "A vehicle health check is required before continuing."
    if fingerprint(doc, plan["vin"]) != plan["fingerprint"]:
        return "Battery, vehicle or charger details changed. Refresh options before booking."
    if errors:
        return (
            "This journey no longer fits the current route or timing. Refresh options."
        )
    return None


def review(doc, plan):
    vehicle = doc["vehicles"][plan["vin"]]
    capacity = effective_capacity(vehicle)

    def pct(energy):
        return round(100 * energy / capacity, 1) if capacity else 0

    reason = None
    if plan["status"] == "PROPOSED":
        reason = issue_message(doc, plan)
    state = "OUTDATED" if reason else plan["status"]
    reasons = {
        "REJECTED": "Rejected by manager. No charging slots were booked.",
        "SUPERSEDED": "Another option was approved or newer options replaced this one.",
        "CANCELLED": "Cancelled. Remaining charging slots are no longer requested.",
        "INTERRUPTED": vehicle.get("incident")
        or "The journey was interrupted and needs a fresh review.",
    }
    reason = reason or reasons.get(state)
    charges = []
    operations = plan["operations"]
    for index, op in enumerate(operations):
        if op["kind"] != "CHARGE":
            continue
        following = next(
            (o for o in operations[index + 1 :] if o["kind"] == "CHARGE"), None
        )
        endpoint = following or operations[-1]
        remaining = op["energy_end"] - endpoint["energy_arrival"]
        extra = max(0, endpoint["energy_arrival"] - plan["reserve_kwh"])
        charges.append(
            dict(
                stop_id=op["stop_id"],
                station=op["name"],
                charger_id=op["charger_id"],
                port=op["port"],
                start=op["start"],
                end=op["end"],
                arrival_pct=pct(op["energy_arrival"]),
                target_pct=pct(op["energy_end"]),
                battery_added_kwh=round(op["energy_end"] - op["energy_arrival"], 2),
                grid_kwh=op["grid_kwh"],
                cost=op["cost"],
                reason=f"Covers {remaining:.2f} kWh to {'the next charger' if following else 'the depot'}; "
                f"arrive with {endpoint['energy_arrival']:.2f} kWh "
                f"({pct(endpoint['energy_arrival']):.1f}% battery).",
                reserve_pct=pct(plan["reserve_kwh"]),
                carry_reason=(
                    f"Carries {extra:.2f} kWh above reserve to reduce charging needed at the next station."
                    if following and extra > 0.01
                    else None
                ),
            )
        )
    late = [o for o in operations if o.get("lateness_minutes", 0) > 0.001]
    affected = "; ".join(f"{o['name']} +{o['lateness_minutes']:.1f} min" for o in late)
    minimum = min(o["energy_arrival"] for o in operations)
    reduced = plan["reserve_kwh"] < doc["policy"]["reserve_kwh"] - 0.001
    if reduced:
        tradeoff = (
            "Prioritise deadlines — uses emergency battery reserve"
            if minimum < doc["policy"]["reserve_kwh"] - 0.001
            else "Prioritise deadlines — reduced reserve allowed"
        )
        tradeoff += f"; lowest planned battery {pct(minimum):.1f}% ({minimum:.2f} kWh)."
        tradeoff += (
            f" Still delayed: {affected}." if late else " All deliveries on time."
        )
    elif late:
        tradeoff = f"Protect battery reserve — delays: {affected}."
    else:
        tradeoff = "Meet all deadlines and protect battery reserve."
    if plan.get("comparison_goal") == "EARLIER_RETURN":
        tradeoff = "Earlier return option — " + tradeoff[0].lower() + tradeoff[1:]
    return dict(
        state=state,
        reason=reason,
        can_approve=state == "PROPOSED",
        tradeoff=reason if state == "OUTDATED" else tradeoff,
        reduced_reserve=reduced,
        minimum_battery_pct=pct(minimum),
        affected_customers=[
            dict(name=o["name"], minutes=o["lateness_minutes"]) for o in late
        ],
        delivery_summary="All remaining deliveries on time."
        if not late
        else "; ".join(
            f"{o['name']}: {o['lateness_minutes']:.1f} min late" for o in late
        ),
        return_at=operations[-1]["end"],
        return_pct=round(100 * operations[-1]["energy_end"] / capacity, 1)
        if capacity
        else 0,
        reserve_pct=round(100 * plan["reserve_kwh"] / capacity, 1) if capacity else 0,
        slots=charges,
    )


def result_message(doc, row):
    if row.get("plan_id"):
        plan = doc["plans"].get(row["plan_id"], {})
        explanation = plan.get("review", {})
        if explanation.get("reason"):
            return explanation["reason"]
        if plan.get("status") in ("APPROVED", "EXECUTING", "COMPLETED"):
            return {
                "APPROVED": "Journey approved.",
                "EXECUTING": "Journey in progress.",
                "COMPLETED": "Vehicle completed its journey.",
            }[plan["status"]]
        return "Journey option ready to review."
    v = doc["vehicles"].get(row.get("vin"))
    if v and (v["health_fault"] or v["temperature_c"] >= 60):
        return "Vehicle health check required. No assistance has been dispatched."
    if v and v["energy_kwh"] <= 0:
        return "Battery empty. Check compatible charging at this location or arrange assistance. Nobody has been dispatched."
    if v and v["state"] in ("TRAVELLING", "CHARGING", "CONNECTING", "RELEASING"):
        return "Wait until the vehicle is safely stopped and unplugged before replacing its journey."
    if row["status"] == "STALE":
        return "Conditions changed during the search. Refresh options."
    if row["status"] == "LIMIT_NO_INCUMBENT":
        return "The search timed out without a usable option. Try again."
    if row["status"] == "INFEASIBLE_MODEL":
        return "No journey fits this option's battery, deadline and charger limits. Compare the other cards; assistance may be needed if stranded."
    return "No usable option was returned. Refresh options; technical details are available below."
