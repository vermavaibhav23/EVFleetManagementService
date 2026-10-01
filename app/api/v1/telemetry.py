from fastapi import APIRouter, Query

from app.core.dependencies import get_database, get_kafka_bus, get_redis
from app.models.telemetry import TelemetryEvent, TelemetryIngestResponse
from app.services.coordination import serialized
from app.services.telemetry import store_telemetry

router = APIRouter()


@router.get("", response_model=list[TelemetryEvent])
async def telemetry_history(vin: str, limit: int = Query(default=100, ge=1, le=1000)):
    """Bounded history supports journey inspection, including short transitions."""
    db = get_database()
    return [
        TelemetryEvent(**doc)
        async for doc in db.telemetry.find({"vin": vin})
        .sort([("ts", -1), ("seq", -1)])
        .limit(limit)
    ]


@router.post("", response_model=TelemetryIngestResponse, status_code=202)
@serialized
async def ingest_telemetry(event: TelemetryEvent) -> TelemetryIngestResponse:
    db = get_database()
    redis = get_redis()
    kafka = get_kafka_bus()

    event_id, status = await store_telemetry(event, db, redis, kafka)
    return TelemetryIngestResponse(id=event_id, status=status)
