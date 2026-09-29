from time import perf_counter

from fastapi import APIRouter

from app.core.config import settings
from app.core.dependencies import get_database, get_redis

router = APIRouter()


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@router.get("/ready")
async def ready() -> dict[str, object]:
    checks: dict[str, object] = {}
    started = perf_counter()

    db = get_database()
    await db.command("ping")
    checks["mongodb"] = "ok"

    redis = get_redis()
    await redis.ping()
    checks["redis"] = "ok"

    # Kafka readiness is verified during startup by creating the shared producer.
    checks["kafka"] = "producer_connected"
    checks["latency_ms"] = round((perf_counter() - started) * 1000, 2)
    return {"status": "ready", "checks": checks}

