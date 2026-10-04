from datetime import UTC

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.core.database import create_pool

mongo_client = None
postgres_pool = None


async def connect_clients():
    global mongo_client, postgres_pool
    postgres_pool = await create_pool(settings.database_url)
    mongo_client = AsyncIOMotorClient(
        settings.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True, tzinfo=UTC
    )
    # History is independent: an unavailable MongoDB must not prevent live processing.


async def close_clients():
    global mongo_client, postgres_pool
    if postgres_pool is not None:
        await postgres_pool.close()
        postgres_pool = None
    if mongo_client is not None:
        mongo_client.close()
        mongo_client = None


def get_database():
    if postgres_pool is None:
        raise RuntimeError("PostgreSQL client is not connected")
    return postgres_pool


def get_history_database():
    if mongo_client is None:
        raise RuntimeError("History client is not connected")
    return mongo_client[settings.mongodb_db]
