import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from pydantic import ValidationError

from app.core.config import settings
from app.core.dependencies import get_database, get_kafka_bus
from app.core.kafka import build_consumer
from app.models.telemetry import TelemetryEvent
from app.services.coordination import serialized
from app.services.fleet_readiness import process_telemetry_for_operations


class AlertConsumer:
    def __init__(self):
        self._task = None
        self._consumer = None
        self.error = None
        self.processed = 0
        self._inflight = 0
        self.last_processed_at = None

    async def start(self):
        self._consumer = build_consumer(settings.kafka_telemetry_topic)
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume(), name="ev-alert-consumer")

    async def stop(self):
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

    async def health(self):
        running = self._task is not None and not self._task.done()
        lag = 0
        if self._consumer:
            for partition in self._consumer.assignment():
                highwater = self._consumer.highwater(partition)
                position = await self._consumer.position(partition)
                if highwater is not None:
                    lag += max(0, highwater - position)
        return {
            "running": running,
            "error": self.error,
            "lag": lag + self._inflight,
            "processed": self.processed,
            "last_processed_at": self.last_processed_at,
        }

    async def _process(self, message):
        event = TelemetryEvent(**message.value)
        stored = await get_database().telemetry.find_one(
            {"event_id": event.event_id}, {"operations_processed": 1}
        )
        # Published simulator events have already applied operational state.
        # Acknowledge these without competing with the next simulation tick.
        # Missing history belongs to an old reset and must be discarded.
        if not stored or stored.get("operations_processed"):
            return
        await self._process_unprocessed(event)

    @serialized
    async def _process_unprocessed(self, event):
        await process_telemetry_for_operations(get_database(), event, get_kafka_bus())

    async def _process_message(self, message):
        try:
            await self._process(message)
        except ValidationError:
            await get_database().rejected_events.update_one(
                {
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                },
                {"$set": {"reason": "Invalid telemetry schema"}},
                upsert=True,
            )

    async def _consume(self):
        try:
            while True:
                batches = await self._consumer.getmany(timeout_ms=1000, max_records=100)
                messages = [message for batch in batches.values() for message in batch]
                if not messages:
                    continue
                self._inflight = len(messages)
                while True:
                    try:
                        semaphore = asyncio.Semaphore(20)

                        async def process(message):
                            async with semaphore:
                                await self._process_message(message)

                        results = await asyncio.gather(
                            *(process(message) for message in messages),
                            return_exceptions=True,
                        )
                        for result in results:
                            if isinstance(result, Exception):
                                raise result
                        # Every fetched record is processed before advancing offsets.
                        await self._consumer.commit()
                        self.processed += len(messages)
                        self._inflight = 0
                        self.last_processed_at = datetime.now(UTC).isoformat()
                        self.error = None
                        break
                    except Exception as exc:  # noqa: BLE001 - retry entire idempotent batch
                        self.error = type(exc).__name__
                        logging.getLogger(__name__).warning(
                            "Telemetry consumer retry: %s", self.error
                        )
                        await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - task boundary
            self.error = type(exc).__name__
            logging.getLogger(__name__).error(
                "Telemetry consumer stopped: %s", self.error
            )
