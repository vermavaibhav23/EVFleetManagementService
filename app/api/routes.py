from fastapi import APIRouter

from app.api.v1 import (
    alerts,
    chargers,
    charging,
    depots,
    fleet,
    health,
    reservations,
    simulator,
    tariffs,
    telemetry,
    trips,
    vehicles,
)

router = APIRouter()
router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])
router.include_router(vehicles.router, prefix="/vehicles", tags=["vehicles"])
router.include_router(chargers.router, tags=["chargers"])
router.include_router(depots.router, prefix="/depots", tags=["depots"])
router.include_router(trips.router, prefix="/trips", tags=["trips"])
router.include_router(tariffs.router, prefix="/tariffs", tags=["tariffs"])
router.include_router(
    reservations.router, prefix="/reservations", tags=["reservations"]
)
router.include_router(charging.router, prefix="/charging", tags=["charging"])
router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
router.include_router(fleet.router, prefix="/fleet", tags=["fleet"])
router.include_router(simulator.router, prefix="/simulator", tags=["simulator"])
