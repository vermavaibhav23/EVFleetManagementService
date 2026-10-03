"""Optional Kafka ingress shares the HTTP telemetry CAS writer and run fence."""

import asyncio
import logging
from contextlib import suppress

from aiokafka import TopicPartition
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.core.kafka import build_consumer
from app.domain import Telemetry, effective_capacity
from app.services.control import telemetry
from app.services.ledger import Ledger


class Ingestion:
    def __init__(self, db):
        self.store = Ledger(db)
        self.consumer = None
        self.task = None

    async def start(self):
        self.consumer = build_consumer(settings.kafka_telemetry_topic)
        await self.consumer.start()
        self.task = asyncio.create_task(self.consume())

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.consumer:
            await self.consumer.stop()

    async def consume(self):
        async for message in self.consumer:
            while True:
                try:
                    event = Telemetry.model_validate_json(message.value)

                    def action(doc):
                        v = doc["vehicles"].get(event.vin)
                        if not v:
                            raise HTTPException(404, "Unknown vehicle")
                        if event.energy_kwh > effective_capacity(v):
                            raise HTTPException(422, "Energy exceeds capacity")
                        return telemetry(doc, event.model_dump())

                    await self.store.mutate(action, event.run_id)
                    break
                except ValidationError:
                    logging.warning(
                        "Rejected invalid telemetry schema at Kafka offset %s",
                        message.offset,
                    )
                    break
                except HTTPException as exc:
                    if exc.status_code == 409 and "Fleet changed" in str(exc.detail):
                        await asyncio.sleep(0.1)
                        continue
                    logging.info("Discarded telemetry: %s", exc.detail)
                    break
                except Exception:
                    logging.exception("Telemetry persistence failed; offset retained")
                    await asyncio.sleep(1)
            await self.consumer.commit(
                {TopicPartition(message.topic, message.partition): message.offset + 1}
            )
