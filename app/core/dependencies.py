from app.core.config import settings
from app.core.database import create_pool

postgres_pool = None


async def connect_clients():
    global postgres_pool
    postgres_pool = await create_pool(settings.database_url)


async def close_clients():
    global postgres_pool
    if postgres_pool is not None:
        await postgres_pool.close()
        postgres_pool = None


def get_database():
    if postgres_pool is None:
        raise RuntimeError("PostgreSQL client is not connected")
    return postgres_pool
