import json
from datetime import UTC, datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.models.telemetry import TelemetryEvent


async def store_telemetry(
    event: TelemetryEvent,
    db: Any,
    redis: Any,
    kafka: Any,
) -> tuple[str, str]:
    event_doc = event.model_dump(mode="python")
    event_doc["ingested_at"] = datetime.now(UTC)
    try:
        result = await db.telemetry.insert_one(event_doc)
    except DuplicateKeyError:
        return event.event_id, "duplicate"

    await redis.set(
        f"vehicle:{event.vin}:latest", json.dumps(event.model_dump(mode="json"))
    )
    await redis.expire(f"vehicle:{event.vin}:latest", 3600)
    await kafka.publish(settings.kafka_telemetry_topic, event_doc, key=event.vin)
    return str(result.inserted_id), "accepted"
