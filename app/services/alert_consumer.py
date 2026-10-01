import asyncio
from contextlib import suppress

from app.core.config import settings
from app.core.dependencies import get_database, get_kafka_bus
from app.core.kafka import build_consumer
from app.models.telemetry import TelemetryEvent
from app.services.fleet_readiness import process_telemetry_for_operations


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
            event = TelemetryEvent(**message.value)
            await process_telemetry_for_operations(
                get_database(), event, get_kafka_bus()
            )
