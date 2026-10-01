from fastapi import APIRouter, Request

from app.core.dependencies import get_database, get_kafka_bus, get_redis
from app.models.simulator import ScenarioRequest, SimulatorStartRequest, SimulatorStatus
from app.services.coordination import serialized
from app.services.simulator import SimulatorManager, seed_scenario

router = APIRouter()


def _manager(request: Request) -> SimulatorManager:
    return request.app.state.simulator


@router.post("/scenarios")
@serialized
async def create_scenario(
    request_body: ScenarioRequest, request: Request
) -> dict[str, object]:
    await _manager(request).stop()
    _manager(request).reset()
    result = await seed_scenario(
        request_body,
        get_database(),
        get_redis(),
        get_kafka_bus(),
    )
    _manager(request).reset()
    return result


@router.post("/start", response_model=SimulatorStatus)
async def start_simulator(
    request_body: SimulatorStartRequest, request: Request
) -> SimulatorStatus:
    return await _manager(request).start(
        get_database(),
        get_redis(),
        get_kafka_bus(),
        request_body.tick_seconds,
        request_body.time_scale,
    )


@router.post("/stop", response_model=SimulatorStatus)
async def stop_simulator(request: Request) -> SimulatorStatus:
    return await _manager(request).stop()


@router.get("/status", response_model=SimulatorStatus)
async def simulator_status(request: Request) -> SimulatorStatus:
    return _manager(request).status()
