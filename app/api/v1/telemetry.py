from fastapi import APIRouter

from app.core.dependencies import get_database, get_kafka_bus, get_redis
from app.models.telemetry import TelemetryEvent, TelemetryIngestResponse
from app.services.telemetry import store_telemetry

router = APIRouter()


@router.post("", response_model=TelemetryIngestResponse, status_code=202)
async def ingest_telemetry(event: TelemetryEvent) -> TelemetryIngestResponse:
    db = get_database()
    redis = get_redis()
    kafka = get_kafka_bus()

    event_id, status = await store_telemetry(event, db, redis, kafka)
    return TelemetryIngestResponse(id=event_id, status=status)
