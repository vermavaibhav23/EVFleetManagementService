import json

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from app.core.config import settings


def build_consumer(topic, group):
    return AIOKafkaConsumer(
        topic,
        group_id=group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_interval_ms=3600000,
        **settings.kafka_config,
    )


def build_producer():
    return AIOKafkaProducer(
        enable_idempotence=True,
        acks="all",
        value_serializer=lambda v: json.dumps(v).encode(),
        **settings.kafka_config,
    )


async def ensure_topics():
    admin = AIOKafkaAdminClient(**settings.kafka_config)
    await admin.start()
    try:
        for topic, partitions in (
            (settings.kafka_telemetry_topic, settings.kafka_partitions),
            (settings.kafka_planning_topic, 1),
        ):
            try:
                await admin.create_topics(
                    [
                        NewTopic(
                            topic,
                            partitions,
                            settings.kafka_replication_factor,
                            topic_configs={"retention.ms": "604800000"},
                        )
                    ]
                )
            except TopicAlreadyExistsError:
                pass
    finally:
        await admin.close()
