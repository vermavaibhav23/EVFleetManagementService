import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from app.core.config import settings
from app.core.dependencies import get_database, get_kafka_bus
from app.core.kafka import build_consumer


class AlertConsumer:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._consumer = None

    async def start(self) -> None:
        self._consumer = build_consumer(settings.kafka_telemetry_topic)
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

    async def _consume(self) -> None:
        assert self._consumer is not None
        async for message in self._consumer:
            event = message.value
            if float(event.get("soc_pct", 100)) > settings.low_soc_alert_threshold:
                continue

            alert = {
                "vin": event["vin"],
                "type": "LOW_SOC",
                "severity": "critical" if float(event.get("soc_pct", 100)) <= 10 else "warning",
                "message": f"Vehicle battery is at {event.get('soc_pct')}%.",
                "telemetry_ts": event.get("ts"),
                "created_at": datetime.now(UTC),
            }

            db = get_database()
            await db.alerts.insert_one(alert)
            await get_kafka_bus().publish(settings.kafka_alerts_topic, alert, key=event["vin"])

