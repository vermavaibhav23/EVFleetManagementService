from enum import StrEnum

from pydantic import BaseModel, Field


class SimulationScenario(StrEnum):
    NORMAL_DAY = "NORMAL_DAY"
    LOW_BATTERY_BEFORE_TRIP = "LOW_BATTERY_BEFORE_TRIP"
    CHARGER_CONGESTION = "CHARGER_CONGESTION"
    CHARGER_FAILURE = "CHARGER_FAILURE"
    UNEXPECTED_LONG_TRIP = "UNEXPECTED_LONG_TRIP"
    BATTERY_OVERHEATING = "BATTERY_OVERHEATING"


class ScenarioRequest(BaseModel):
    scenario: SimulationScenario = SimulationScenario.LOW_BATTERY_BEFORE_TRIP
    vehicle_count: int = Field(default=10, ge=1, le=1000)
    seed: int = 42


class SimulatorStartRequest(BaseModel):
    tick_seconds: float = Field(default=1, ge=0.1, le=60)
    time_scale: float = Field(default=60, ge=1, le=3600)


class SimulatorStatus(BaseModel):
    running: bool
    simulated_time: str | None = None
    tracked_vehicles: int = 0
    emitted_events: int = 0
