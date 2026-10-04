from contextlib import asynccontextmanager
from pathlib import Path

from asyncpg import PostgresError
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.core.dependencies import (
    close_clients,
    connect_clients,
    get_database,
    get_history_database,
)
from app.services.pipeline import Pipeline


@asynccontextmanager
async def lifespan(app):
    await connect_clients()
    pipeline = Pipeline(get_database(), get_history_database())
    app.state.pipeline = pipeline
    try:
        await pipeline.start()
        yield
    finally:
        await pipeline.stop()
        await close_clients()


app = FastAPI(title=settings.app_name, version="3.0", lifespan=lifespan)
app.include_router(router, prefix=settings.api_v1_prefix)
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")


@app.exception_handler(PostgresError)
async def persistence_error(request: Request, exc: Exception):
    return JSONResponse(
        status_code=503,
        content={"detail": "Operational database unavailable; retry after recovery"},
    )


@app.get("/")
async def root():
    return {"service": settings.app_name, "portal": "/portal", "docs": "/docs"}


@app.get("/portal", include_in_schema=False)
async def portal():
    return FileResponse(static / "index.html")
