from fastapi import APIRouter

from app.api.v1 import chargers, health, telemetry, vehicles

router = APIRouter()
router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])
router.include_router(vehicles.router, prefix="/vehicles", tags=["vehicles"])
router.include_router(chargers.router, tags=["chargers"])

