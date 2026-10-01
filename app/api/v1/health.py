import asyncio
import os
from time import perf_counter

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.core.dependencies import get_database, get_kafka_bus, get_redis

router = APIRouter()


@router.get("/live")
async def live():
    return {
        "status": "ok",
        "service": settings.app_name,
        "commit": os.getenv("RAILWAY_GIT_COMMIT_SHA", "local"),
    }


@router.get("/ready")
async def ready(request: Request):
    started = perf_counter()

    async def check(name, operation):
        try:
            await asyncio.wait_for(operation(), timeout=5)
            return name, "ok"
        except Exception as exc:  # noqa: BLE001 - readiness must aggregate dependency failures
            return name, type(exc).__name__

    checks = dict(
        await asyncio.gather(
            check("mongodb", lambda: get_database().command("ping")),
            check("redis", lambda: get_redis().ping()),
            check("kafka", lambda: get_kafka_bus().health()),
        )
    )
    consumer = getattr(request.app.state, "alert_consumer", None)
    try:
        consumer_health = (
            await asyncio.wait_for(consumer.health(), 5)
            if consumer
            else {"running": False, "error": "Not started", "lag": None}
        )
    except Exception as exc:  # noqa: BLE001 - health aggregation boundary
        consumer_health = {"running": False, "error": type(exc).__name__, "lag": None}
    checks["kafka_consumer"] = (
        "ok"
        if consumer_health["running"] and not consumer_health["error"]
        else "unavailable"
    )
    payload = {
        "status": "ready",
        "checks": checks,
        "consumer": consumer_health,
        "latency_ms": round((perf_counter() - started) * 1000, 2),
    }
    if any(value != "ok" for value in checks.values()):
        payload["status"] = "not_ready"
        raise HTTPException(503, detail=payload)
    return payload
