import json
from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.config import settings
from app.core.dependencies import get_database, get_kafka_bus, get_redis
from app.models.telemetry import TelemetryEvent, TelemetryIngestResponse

router = APIRouter()


@router.post("", response_model=TelemetryIngestResponse, status_code=202)
async def ingest_telemetry(event: TelemetryEvent) -> TelemetryIngestResponse:
    db = get_database()
    redis = get_redis()
    kafka = get_kafka_bus()

    event_doc = event.model_dump(mode="json")
    event_doc["ingested_at"] = datetime.now(UTC)

    result = await db.telemetry.insert_one(event_doc)
    await redis.set(f"vehicle:{event.vin}:latest", json.dumps(event.model_dump(mode="json")))
    await redis.expire(f"vehicle:{event.vin}:latest", 3600)
    await kafka.publish(settings.kafka_telemetry_topic, event_doc, key=event.vin)

    return TelemetryIngestResponse(id=str(result.inserted_id), status="accepted")
