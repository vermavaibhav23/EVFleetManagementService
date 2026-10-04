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

    pool = await asyncpg.create_pool(url, min_size=1, max_size=12, init=initialize)
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(72849101)")
        await connection.execute(Path(__file__).with_name("schema.sql").read_text())
    return pool
