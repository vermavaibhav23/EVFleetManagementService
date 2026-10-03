from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from app.api.routes import router
from app.core.config import settings
from app.core.dependencies import close_clients, connect_clients, get_database
from app.services.ingestion import Ingestion
from app.services.runner import Runner


@asynccontextmanager
async def lifespan(app):
    await connect_clients()
    runner = Runner(get_database())
    runner.start()
    app.state.runner = runner
    ingress = Ingestion(get_database()) if settings.kafka_enabled else None
    try:
        if ingress:
            await ingress.start()
        yield
    finally:
        if ingress:
            await ingress.stop()
        await runner.stop()
        await close_clients()


app = FastAPI(title=settings.app_name, version="2.0", lifespan=lifespan)
app.include_router(router, prefix=settings.api_v1_prefix)
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")


@app.exception_handler(PyMongoError)
async def persistence_error(request: Request, exc: Exception):
    return JSONResponse(
        status_code=503,
        content={"detail": "MongoDB unavailable; retry the same action after recovery"},
    )


@app.get("/")
async def root():
    return {"service": settings.app_name, "portal": "/portal", "docs": "/docs"}


@app.get("/portal", include_in_schema=False)
async def portal():
    return FileResponse(static / "index.html")
