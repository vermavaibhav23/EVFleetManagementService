from datetime import UTC

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings

mongo_client = None


async def connect_clients():
    global mongo_client
    mongo_client = AsyncIOMotorClient(
        settings.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True, tzinfo=UTC
    )
    await mongo_client.admin.command("ping")
    # _id is the unique serialization point; no indexes from the retired schema.


async def close_clients():
    global mongo_client
    if mongo_client is not None:
        mongo_client.close()
        mongo_client = None


def get_database():
    if mongo_client is None:
        raise RuntimeError("MongoDB client is not connected")
    return mongo_client[settings.mongodb_db]
