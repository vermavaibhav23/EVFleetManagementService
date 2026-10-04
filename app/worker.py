"""Optional separate Railway worker. Default API deployment embeds the same workers."""

import asyncio

from app.core.config import settings
from app.core.dependencies import (
    close_clients,
    connect_clients,
    get_database,
    get_history_database,
)
from app.services.pipeline import Pipeline


async def main():
    if not settings.provider_api_url:
        raise RuntimeError(
            "Set PROVIDER_API_URL to the FastAPI service URL for a separate worker"
        )
    settings.run_workers = True
    await connect_clients()
    pipeline = Pipeline(get_database(), get_history_database())
    try:
        await pipeline.start()
        await asyncio.Event().wait()
    finally:
        await pipeline.stop()
        await close_clients()


if __name__ == "__main__":
    asyncio.run(main())
