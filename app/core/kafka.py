from aiokafka import AIOKafkaConsumer

from app.core.config import settings


def build_consumer(topic):
    return AIOKafkaConsumer(
        topic,
        group_id=settings.kafka_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        **settings.kafka_config,
    )
