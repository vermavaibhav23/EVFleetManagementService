"""PostgreSQL connections and additive schema initialization."""

import json
from datetime import datetime
from pathlib import Path

import asyncpg


def dumps(value):
    return json.dumps(
        value, default=lambda x: x.isoformat() if isinstance(x, datetime) else str(x)
    )


async def create_pool(url):
    async def initialize(connection):
        await connection.set_type_codec(
            "jsonb", schema="pg_catalog", encoder=dumps, decoder=json.loads
        )

    import ssl

    from app.core.config import settings

    options = {}
    if settings.database_ssl_cafile or settings.database_ssl_ca_pem:
        options["ssl"] = ssl.create_default_context(
            cafile=settings.database_ssl_cafile, cadata=settings.database_ssl_ca_pem
        )
    pool = await asyncpg.create_pool(
        url, min_size=1, max_size=6, init=initialize, **options
    )
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(72849101)")
        await connection.execute(Path(__file__).with_name("schema.sql").read_text())
    return pool
