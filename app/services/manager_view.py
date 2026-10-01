"""A coherent manager projection of the existing operational records."""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.charger import Charger
from app.models.tariff import Tariff
from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip
from app.models.vehicle import Vehicle
from app.services.pricing import price_at, tariffs_for_charger
from app.services.readiness import assess_readiness
from app.services.scheduler import haversine_km

BLOCKING_FLAGS = {
    "HIGH_BATTERY_TEMPERATURE",
    "LOW_BATTERY_TEMPERATURE",
    "DIAGNOSTIC_TROUBLE_CODE",
}


def manager_readiness(vehicle, event, trip, assessment, chargers):
    """Physical reachability is independent of queues and delivery deadlines."""
    reachable = [
        c.charger_id
        for c in chargers
        if c.status.value in {"AVAILABLE", "OCCUPIED"}
        and c.connector_type.casefold() == vehicle.connector_type.casefold()
        and haversine_km(event.lat, event.lon, c.lat, c.lon)
        * vehicle.consumption_kwh_per_km
        < assessment.available_energy_kwh
    ]
    if BLOCKING_FLAGS.intersection(assessment.health_flags):
        return (
            "BLOCKED",
            "Charging blocked: inspect the battery-health warning before normal charging.",
            reachable,
        )
    if trip is None:
        if event.operating_state == "AT_CUSTOMER":
            return (
                "COMPLETE",
                "Delivery complete. This demonstration does not automatically execute the later itinerary legs.",
                reachable,
            )
        return "NO_DELIVERY", "No delivery scheduled for execution.", reachable
    if assessment.range_margin_km is not None and assessment.range_margin_km < 0:
        if not reachable:
            return (
                "EMERGENCY",
                "Assistance required: no compatible, healthy charger is physically reachable. No roadside assistance has been dispatched.",
                reachable,
            )
        return "NEEDS_CHARGING", assessment.explanation, reachable
    return "NORMAL", assessment.explanation, reachable


async def build_manager_snapshot(db, simulator):
    config = await db.simulation.find_one({"simulation_id": "active"}) or {}
    demo = bool(config.get("run_id"))
    scope = {"vin": {"$regex": "^SIM"}} if demo else {}
    resource_scope = {"depot_id": "SIM-DEPOT-01"} if demo else {}
    (
        vehicles,
        events,
        trips,
        charger_docs,
        depots,
        tariff_docs,
        reservations,
        plans,
        alerts,
    ) = await asyncio.gather(
        db.vehicles.find({**scope, "active": True}, {"_id": 0})
        .sort("vin", 1)
        .to_list(1000),
        db.telemetry.aggregate(
            [
                {
                    "$match": {
                        **scope,
                        **({"simulation_run_id": config["run_id"]} if demo else {}),
                    }
                },
                {"$sort": {"vin": 1, "ts": -1, "seq": -1}},
                {"$group": {"_id": "$vin", "event": {"$first": "$$ROOT"}}},
            ]
        ).to_list(1000),
        db.trips.find(scope, {"_id": 0}).sort("departure_time", 1).to_list(5000),
        db.chargers.find(
            {
                **resource_scope,
                **({"charger_id": {"$regex": "^SIM-CHARGER-"}} if demo else {}),
            },
            {"_id": 0},
        )
        .sort("charger_id", 1)
        .to_list(1000),
        db.depots.find(resource_scope, {"_id": 0}).to_list(1000),
        db.tariffs.find(
            {**resource_scope, **({"tariff_id": {"$regex": "^SIM-"}} if demo else {})},
            {"_id": 0},
        ).to_list(1000),
        db.reservations.find(
            {"charger_id": {"$regex": "^SIM-CHARGER"}} if demo else {}, {"_id": 0}
        ).to_list(5000),
        db.charging_plans.find(scope, {"_id": 0}).sort("created_at", -1).to_list(1000),
        db.alerts.find(scope, {"_id": 0}).sort("updated_at", -1).to_list(1000),
    )
    latest = {row["_id"]: row["event"] for row in events}
    clock = max(
        (e["ts"] for e in latest.values()),
        default=config.get("created_at") or datetime.now(UTC),
    )
    chargers = [Charger(**doc) for doc in charger_docs]
    tariffs = [Tariff(**doc) for doc in tariff_docs]
    rows = []
    for doc in vehicles:
        vehicle = Vehicle(**doc)
        event_doc = latest.get(vehicle.vin)
        vehicle_trips = [Trip(**t) for t in trips if t["vin"] == vehicle.vin]
        executable = [
            t
            for t in vehicle_trips
            if t.simulation_enabled and t.status.value in {"IN_PROGRESS", "PLANNED"}
        ]
        executable.sort(
            key=lambda t: (t.status.value != "IN_PROGRESS", t.departure_time)
        )
        trip = executable[0] if executable else None
        if not event_doc:
            rows.append(
                {
                    **vehicle.model_dump(mode="json"),
                    "manager_readiness": "OFFLINE",
                    "operating_state": "OFFLINE",
                    "explanation": "Waiting for telemetry.",
                    "itinerary": [t.model_dump(mode="json") for t in vehicle_trips],
                }
            )
            continue
        event = TelemetryEvent(**event_doc)
        assessment = assess_readiness(
            vehicle,
            event,
            trip,
            settings.reserve_range_km,
            settings.charge_soon_margin_km,
        )
        eligible = [
            c
            for c in chargers
            if not vehicle.vin.startswith("SIM") or c.depot_id == vehicle.depot_id
        ]
        status, reason, reachable = manager_readiness(
            vehicle, event, trip, assessment, eligible
        )
        direct_km = (
            haversine_km(
                event.lat, event.lon, trip.destination_lat, trip.destination_lon
            )
            if trip
            and trip.destination_lat is not None
            and trip.destination_lon is not None
            else assessment.trip_distance_km
        )
        deadline_margin = (
            (trip.delivery_deadline - event.ts).total_seconds() / 60
            - (direct_km or 0) / 35 * 60
            if trip and trip.delivery_deadline
            else None
        )
        rows.append(
            {
                **vehicle.model_dump(mode="json"),
                **event.model_dump(mode="json"),
                "telemetry_time": event.ts.isoformat(),
                "telemetry_lag_seconds": max(0, (clock - event.ts).total_seconds()),
                "readiness": assessment.status.value,
                "manager_readiness": status,
                "explanation": reason,
                "health_flags": assessment.health_flags,
                "current_range_km": assessment.current_range_km,
                "range_margin_km": assessment.range_margin_km,
                "reserve_range_km": assessment.reserve_range_km,
                "delivery_remaining_km": round(direct_km, 2)
                if direct_km is not None
                else None,
                "reachable_charger_ids": reachable,
                "deadline_margin_minutes": round(deadline_margin, 1)
                if deadline_margin is not None
                else None,
                "current_trip": trip.model_dump(mode="json") if trip else None,
                "itinerary": [t.model_dump(mode="json") for t in vehicle_trips],
            }
        )
    station_rows = []
    for charger in chargers:
        selected_tariffs = tariffs_for_charger(
            tariffs, charger.charger_id, charger.depot_id
        )
        zone = selected_tariffs[0].timezone if selected_tariffs else "Asia/Kolkata"
        midnight = clock.astimezone(ZoneInfo(zone)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        station_reservations = [
            r for r in reservations if r["charger_id"] == charger.charger_id
        ]
        occupied = {
            r["port_number"] for r in station_reservations if r["status"] == "OCCUPIED"
        }
        reserved = {
            r["port_number"]
            for r in station_reservations
            if r["status"] in {"CONFIRMED", "VEHICLE_EN_ROUTE"}
            and r["start_time"] <= clock < r["end_time"]
        } - occupied
        station_rows.append(
            {
                **charger.model_dump(mode="json"),
                "occupied_ports": len(occupied),
                "reserved_ports": len(reserved),
                "free_ports": max(0, charger.port_count - len(occupied | reserved))
                if charger.status.value == "AVAILABLE"
                else 0,
                "current_price_per_kwh": price_at(
                    clock, selected_tariffs, charger.price_per_kwh
                ),
                "currency": "INR",
                "timezone": zone,
                "tariff_scope": "Station-specific demo tariff"
                if any(t.charger_id for t in selected_tariffs)
                else "Shared depot tariff",
                "tariffs": [t.model_dump(mode="json") for t in selected_tariffs],
                "hourly_prices": [
                    {
                        "hour": h,
                        "price_per_kwh": price_at(
                            midnight + timedelta(hours=h),
                            selected_tariffs,
                            charger.price_per_kwh,
                        ),
                    }
                    for h in range(24)
                ],
                "reservations": station_reservations,
            }
        )
    status = simulator.status().model_dump(mode="json")
    status["simulated_time"] = status["simulated_time"] or clock.isoformat()
    return {
        "run_id": config.get("run_id"),
        "scenario": config.get("scenario"),
        "primary_demo_vin": "SIM00000000000001" if demo else None,
        "scope": "Demo fleet" if demo else "Fleet",
        "vehicles": rows,
        "chargers": station_rows,
        "depots": depots,
        "plans": plans,
        "alerts": alerts,
        "simulator": status,
        "snapshot_at": datetime.now(UTC).isoformat(),
    }
