from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.dependencies import get_connection_errors, get_database, get_kafka_bus, get_redis

router = APIRouter()


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@router.get("/ready")
async def ready() -> dict[str, object]:
    checks: dict[str, object] = {}
    started = perf_counter()
    errors = get_connection_errors()

    try:
        db = get_database()
        await db.command("ping")
        checks["mongodb"] = "ok"
    except Exception as exc:
        checks["mongodb"] = errors.get("mongodb", str(exc))

    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = errors.get("redis", str(exc))

    try:
        get_kafka_bus()
        checks["kafka"] = "producer_connected"
    except Exception as exc:
        checks["kafka"] = errors.get("kafka", str(exc))

    if "kafka_consumer" in errors:
        checks["kafka_consumer"] = errors["kafka_consumer"]

    checks["latency_ms"] = round((perf_counter() - started) * 1000, 2)
    if any(value != "ok" and value != "producer_connected" for value in checks.values() if not isinstance(value, float)):
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})

    return {"status": "ready", "checks": checks}
