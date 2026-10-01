import json

from fastapi import APIRouter, HTTPException, Query

from app.core.dependencies import get_database, get_redis
from app.models.vehicle import Vehicle, VehicleListResponse
from app.services.coordination import serialized
from app.services.fleet_readiness import (
    evaluate_vehicle_readiness,
    synchronize_readiness_alert,
)

router = APIRouter()


@router.post("", response_model=Vehicle, status_code=201)
@serialized
async def upsert_vehicle(vehicle: Vehicle) -> Vehicle:
    db = get_database()
    await db.vehicles.update_one(
        {"vin": vehicle.vin},
        {"$set": vehicle.model_dump(mode="python")},
        upsert=True,
    )
    return vehicle


@router.get("", response_model=VehicleListResponse)
async def list_vehicles(
    limit: int = Query(default=100, ge=1, le=1000),
) -> VehicleListResponse:
    db = get_database()
    vehicles: list[Vehicle] = []
    async for doc in db.vehicles.find({"active": True}).sort("vin", 1).limit(limit):
        doc.pop("_id", None)
        vehicles.append(Vehicle(**doc))
    total = await db.vehicles.count_documents({"active": True})
    return VehicleListResponse(vehicles=vehicles, total=total)


@router.get("/{vin}/latest")
async def latest_vehicle_state(vin: str) -> dict[str, object]:
    redis = get_redis()
    latest = await redis.get(f"vehicle:{vin}:latest")
    if latest:
        return {"source": "redis", "vehicle": json.loads(latest)}

    db = get_database()
    doc = await db.telemetry.find_one({"vin": vin}, sort=[("ts", -1), ("seq", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Vehicle telemetry not found")
    doc["_id"] = str(doc["_id"])
    return {"source": "mongodb", "vehicle": doc}


@router.get("/{vin}/readiness")
@serialized
async def vehicle_readiness(
    vin: str, synchronize_alert: bool = True
) -> dict[str, object]:
    db = get_database()
    assessment = await evaluate_vehicle_readiness(db, vin)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Vehicle or telemetry not found")
    if synchronize_alert:
        await synchronize_readiness_alert(db, assessment)
    return assessment.model_dump(mode="json")


@router.get("/{vin}/alerts")
async def vehicle_alerts(
    vin: str, limit: int = Query(default=25, ge=1, le=100)
) -> dict[str, object]:
    db = get_database()
    cursor = db.alerts.find({"vin": vin}).sort("updated_at", -1).limit(limit)
    alerts = []
    async for alert in cursor:
        alert["_id"] = str(alert["_id"])
        alerts.append(alert)
    return {"vin": vin, "alerts": alerts}
