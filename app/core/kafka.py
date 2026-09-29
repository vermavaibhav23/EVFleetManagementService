import json
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.core.config import settings


class KafkaBus:
    def __init__(self) -> None:
        self._producer = AIOKafkaProducer(**settings.kafka_config)

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def publish(self, topic: str, payload: dict[str, Any], key: str | None = None) -> None:
        await self._producer.send_and_wait(
            topic,
            json.dumps(payload, default=str).encode("utf-8"),
            key=key.encode("utf-8") if key else None,
        )


def build_consumer(topic: str) -> AIOKafkaConsumer:
    return AIOKafkaConsumer(
        topic,
        group_id=settings.kafka_consumer_group,
        enable_auto_commit=True,
        auto_offset_reset="latest",
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        **settings.kafka_config,
    )

