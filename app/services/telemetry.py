import json
from datetime import UTC, datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.models.telemetry import TelemetryEvent
from app.services.fleet_readiness import process_telemetry_for_operations


async def store_telemetry(
    event: TelemetryEvent, db: Any, redis: Any, kafka: Any
) -> tuple[str, str]:
    if event.vin.startswith("SIM"):
        run = await db.simulation.find_one({"simulation_id": "active"})
        if run and event.simulation_run_id != run.get("run_id"):
            return event.event_id, "stale_generation"
    event_doc = event.model_dump(mode="python")
    event_doc["ingested_at"] = datetime.now(UTC)
    duplicate = False
    try:
        await db.telemetry.insert_one(event_doc)
    except DuplicateKeyError:
        duplicate = True
    latest = await db.telemetry.find_one(
        {"vin": event.vin}, sort=[("ts", -1), ("seq", -1)]
    )
    if latest and latest.get("event_id") == event.event_id:
        await redis.set(
            f"vehicle:{event.vin}:latest",
            json.dumps(event.model_dump(mode="json")),
            ex=3600,
        )
        # Dashboard operations do not depend on a consumer catching up.
        await process_telemetry_for_operations(db, event)
    # Retrying an idempotent event repairs a previous Kafka publication failure.
    if not duplicate or not latest or not latest.get("published"):
        await kafka.publish(settings.kafka_telemetry_topic, event_doc, key=event.vin)
        await db.telemetry.update_one(
            {"event_id": event.event_id}, {"$set": {"published": True}}
        )
    return event.event_id, "duplicate" if duplicate else "accepted"
