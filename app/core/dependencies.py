from datetime import UTC
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.config import settings
from app.core.kafka import KafkaBus

mongo_client: AsyncIOMotorClient | None = None
redis_client: Redis | None = None
kafka_bus: KafkaBus | None = None
connection_errors: dict[str, str] = {}


async def connect_clients() -> None:
    global kafka_bus, mongo_client, redis_client

    connection_errors.clear()

    try:
        mongo_client = AsyncIOMotorClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=5000,
            tz_aware=True,
            tzinfo=UTC,
        )
        await mongo_client.admin.command("ping")
        db = get_database()
        await db.telemetry.create_index("event_id", unique=True, sparse=True)
        await db.telemetry.create_index([("vin", 1), ("ts", -1)])
        await db.telemetry.create_index([("ts", -1)])
        await db.vehicles.create_index("vin", unique=True)
        await db.trips.create_index("trip_id", unique=True)
        await db.trips.create_index([("vin", 1), ("departure_time", 1)])
        await db.depots.create_index("depot_id", unique=True)
        await db.chargers.create_index("charger_id", unique=True)
        await db.tariffs.create_index("tariff_id", unique=True)
        await db.reservations.create_index("reservation_id", unique=True)
        await db.reservations.create_index(
            [("charger_id", 1), ("port_number", 1), ("start_time", 1), ("end_time", 1)]
        )
        await db.charging_plans.create_index("plan_id", unique=True)
        await db.charging_plans.create_index(
            "vin",
            unique=True,
            partialFilterExpression={"active": True},
            name="one_active_plan_per_vehicle",
        )
        await db.alerts.create_index("dedupe_key", unique=True, sparse=True)
        await db.alerts.create_index([("vin", 1), ("created_at", -1)])
    except Exception as exc:
        mongo_client = None
        connection_errors["mongodb"] = str(exc)

    try:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
    except Exception as exc:
        redis_client = None
        connection_errors["redis"] = str(exc)

    try:
        kafka_bus = KafkaBus()
        await kafka_bus.start()
    except Exception as exc:
        kafka_bus = None
        connection_errors["kafka"] = str(exc)


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


def is_kafka_connected() -> bool:
    return kafka_bus is not None


def get_connection_errors() -> dict[str, str]:
    return dict(connection_errors)
