from fastapi import APIRouter, Query

from app.core.dependencies import get_database
from app.services.fleet_readiness import evaluate_vehicle_readiness

router = APIRouter()


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
    available_chargers = await db.chargers.count_documents(
        {"status": {"$in": ["AVAILABLE", "available"]}}
    )
    return {
        "vehicles": vehicle_total,
        "open_alerts": open_alerts,
        "critical_alerts": critical,
        "active_charging_plans": active_plans,
        "chargers": chargers,
        "available_chargers": available_chargers,
    }


@router.get("/vehicles")
async def fleet_vehicle_statuses(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    db = get_database()
    rows: list[dict[str, object]] = []
    async for vehicle in db.vehicles.find({"active": True}).sort("vin", 1).limit(limit):
        vin = vehicle["vin"]
        telemetry = await db.telemetry.find_one({"vin": vin}, sort=[("ts", -1)])
        assessment = await evaluate_vehicle_readiness(db, vin) if telemetry else None
        rows.append(
            {
                "vin": vin,
                "name": vehicle.get("name", vin),
                "soc_pct": telemetry.get("soc_pct") if telemetry else None,
                "operating_state": telemetry.get("operating_state")
                if telemetry
                else "OFFLINE",
                "readiness": assessment.status.value if assessment else "UNKNOWN",
                "current_range_km": assessment.current_range_km if assessment else None,
                "post_trip_range_km": assessment.post_trip_range_km
                if assessment
                else None,
                "range_margin_km": assessment.range_margin_km if assessment else None,
                "next_departure_time": assessment.next_departure_time
                if assessment
                else None,
            }
        )
    return {
        "vehicles": rows,
        "total": await db.vehicles.count_documents({"active": True}),
    }
