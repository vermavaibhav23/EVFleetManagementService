from contextlib import asynccontextmanager, suppress
from pathlib import Path

from aiokafka.errors import KafkaError
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from app.api.routes import router as api_router
from app.core.config import settings
from app.core.dependencies import (
    close_clients,
    connect_clients,
    is_kafka_connected,
)
from app.services.alert_consumer import AlertConsumer
from app.services.simulator import SimulatorManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_clients()
    simulator = SimulatorManager()
    app.state.simulator = simulator
    consumer = None
    if is_kafka_connected():
        consumer = AlertConsumer()
        try:
            await consumer.start()
        except Exception:  # noqa: BLE001 - dependency startup boundary
            consumer = None
    app.state.alert_consumer = consumer
    try:
        yield
    finally:
        with suppress(Exception):
            await simulator.stop()
        if consumer:
            with suppress(Exception):
                await consumer.stop()
        await close_clients()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router, prefix=settings.api_v1_prefix)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.exception_handler(RuntimeError)
async def dependency_runtime_error(_: Request, exc: RuntimeError) -> JSONResponse:
    message = type(exc).__name__
    unavailable = "not connected" in message.casefold()
    return JSONResponse(
        status_code=503 if unavailable else 500,
        content={
            "detail": "Dependency unavailable; check service health"
            if unavailable
            else "An internal operation failed; check application logs"
        },
    )


@app.exception_handler(PyMongoError)
@app.exception_handler(RedisError)
@app.exception_handler(KafkaError)
async def dependency_failure(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": f"Service dependency unavailable ({type(exc).__name__}); retry after checking health"
        },
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "running",
        "portal": "/portal",
        "docs": "/docs",
    }


@app.get("/portal", include_in_schema=False)
async def portal() -> FileResponse:
    return FileResponse(static_dir / "index.html")
