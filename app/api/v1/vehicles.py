import json

from fastapi import APIRouter, HTTPException

from app.core.dependencies import get_database, get_redis

router = APIRouter()


@router.get("/{vin}/latest")
async def latest_vehicle_state(vin: str) -> dict[str, object]:
    redis = get_redis()
    latest = await redis.get(f"vehicle:{vin}:latest")
    if latest:
        return {"source": "redis", "vehicle": json.loads(latest)}

    db = get_database()
    doc = await db.telemetry.find_one({"vin": vin}, sort=[("ts", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Vehicle telemetry not found")

    doc["_id"] = str(doc["_id"])
    return {"source": "mongodb", "vehicle": doc}


@router.get("/{vin}/alerts")
async def vehicle_alerts(vin: str, limit: int = 25) -> dict[str, object]:
    db = get_database()
    cursor = db.alerts.find({"vin": vin}).sort("created_at", -1).limit(min(limit, 100))
    alerts = []
    async for alert in cursor:
        alert["_id"] = str(alert["_id"])
        alerts.append(alert)
    return {"vin": vin, "alerts": alerts}
