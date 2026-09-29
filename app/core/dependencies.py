from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.config import settings
from app.core.kafka import KafkaBus


mongo_client: AsyncIOMotorClient | None = None
redis_client: Redis | None = None
kafka_bus: KafkaBus | None = None


async def connect_clients() -> None:
    global kafka_bus, mongo_client, redis_client

    mongo_client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    await mongo_client.admin.command("ping")

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis_client.ping()

    kafka_bus = KafkaBus()
    await kafka_bus.start()

    db = get_database()
    await db.telemetry.create_index([("vin", 1), ("ts", -1)])
    await db.telemetry.create_index([("ts", -1)])
    await db.chargers.create_index("charger_id", unique=True)
    await db.alerts.create_index([("vin", 1), ("created_at", -1)])


async def close_clients() -> None:
    global kafka_bus, mongo_client, redis_client

    if kafka_bus:
        await kafka_bus.stop()
        kafka_bus = None

    if redis_client:
        await redis_client.aclose()
        redis_client = None

    if mongo_client:
        mongo_client.close()
        mongo_client = None


def get_database() -> AsyncIOMotorDatabase[Any]:
    if mongo_client is None:
        raise RuntimeError("MongoDB client is not connected")
    return mongo_client[settings.mongodb_db]


def get_redis() -> Redis:
    if redis_client is None:
        raise RuntimeError("Redis client is not connected")
    return redis_client


def get_kafka_bus() -> KafkaBus:
    if kafka_bus is None:
        raise RuntimeError("Kafka bus is not connected")
    return kafka_bus

