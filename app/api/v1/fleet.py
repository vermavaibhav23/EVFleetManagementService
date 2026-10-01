import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import get_database
from app.models.telemetry import TelemetryEvent
from app.services.coordination import serialized
from app.services.fleet_readiness import evaluate_vehicle_readiness

router = APIRouter()


@router.get("/manager")
@serialized
async def manager_snapshot(request: Request):
    from app.services.manager_view import build_manager_snapshot

    return await build_manager_snapshot(get_database(), request.app.state.simulator)


@router.get("/overview")
async def fleet_overview() -> dict[str, object]:
    db = get_database()
    vehicle_total = await db.vehicles.count_documents({"active": True})
    open_alerts = await db.alerts.count_documents({"status": {"$ne": "RESOLVED"}})
    critical = await db.alerts.count_documents(
        {"status": {"$ne": "RESOLVED"}, "severity": "critical"}
    )
    active_plans = await db.charging_plans.count_documents(
        {"status": {"$in": ["PROPOSED", "APPROVED", "CHARGING"]}}
    )
    chargers = await db.chargers.count_documents({})
    from app.api.v1.chargers import list_chargers

    charger_rows = await list_chargers()
    available_chargers = sum(1 for item in charger_rows if item.free_ports)
    free_ports = sum(item.free_ports or 0 for item in charger_rows)
    return {
        "vehicles": vehicle_total,
        "open_alerts": open_alerts,
        "critical_alerts": critical,
        "active_charging_plans": active_plans,
        "chargers": chargers,
        "available_chargers": available_chargers,
        "free_ports": free_ports,
    }


@router.get("/vehicles")
async def fleet_vehicle_statuses(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    db = get_database()
    vehicles = await db.vehicles.find({"active": True}).sort("vin", 1).to_list(limit)

    async def build_row(vehicle: dict[str, Any]) -> dict[str, object]:
        vin = vehicle["vin"]
        telemetry = await db.telemetry.find_one(
            {"vin": vin}, sort=[("ts", -1), ("seq", -1)]
        )
        assessment = (
            await evaluate_vehicle_readiness(db, vin, TelemetryEvent(**telemetry))
            if telemetry
            else None
        )
        return {
            "vin": vin,
            "name": vehicle.get("name", vin),
            "soc_pct": telemetry.get("soc_pct") if telemetry else None,
            "health_flags": assessment.health_flags if assessment else [],
            "route_remaining_km": telemetry.get("route_remaining_km")
            if telemetry
            else None,
            "telemetry_time": telemetry.get("ts") if telemetry else None,
            "lat": telemetry.get("lat") if telemetry else None,
            "lon": telemetry.get("lon") if telemetry else None,
            "speed_kmh": telemetry.get("speed_kmh") if telemetry else None,
            "operating_state": telemetry.get("operating_state")
            if telemetry
            else "OFFLINE",
            "navigation_phase": telemetry.get("navigation_phase")
            if telemetry
            else None,
            "navigation_target": telemetry.get("navigation_target")
            if telemetry
            else None,
            "destination_lat": telemetry.get("destination_lat") if telemetry else None,
            "destination_lon": telemetry.get("destination_lon") if telemetry else None,
            "distance_to_destination_km": telemetry.get("distance_to_destination_km")
            if telemetry
            else None,
            "eta_minutes": telemetry.get("eta_minutes") if telemetry else None,
            "charger_id": telemetry.get("charger_id") if telemetry else None,
            "is_plugged_in": telemetry.get("is_plugged_in", False)
            if telemetry
            else False,
            "readiness": assessment.status.value if assessment else "UNKNOWN",
            "current_range_km": assessment.current_range_km if assessment else None,
            "post_trip_range_km": assessment.post_trip_range_km if assessment else None,
            "range_margin_km": assessment.range_margin_km if assessment else None,
            "next_departure_time": assessment.next_departure_time
            if assessment
            else None,
        }

    rows = await asyncio.gather(*(build_row(vehicle) for vehicle in vehicles))
    return {
        "vehicles": rows,
        "total": await db.vehicles.count_documents({"active": True}),
    }
