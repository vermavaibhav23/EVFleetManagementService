from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.core.config import settings
from app.core.dependencies import close_clients, connect_clients
from app.services.alert_consumer import AlertConsumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_clients()
    consumer = AlertConsumer()
    await consumer.start()
    app.state.alert_consumer = consumer
    try:
        yield
    finally:
        with suppress(Exception):
            await consumer.stop()
        await close_clients()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "running"}

