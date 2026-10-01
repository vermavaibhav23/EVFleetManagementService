from enum import StrEnum

from pydantic import BaseModel, Field


class SimulationScenario(StrEnum):
    NORMAL_DAY = "NORMAL_DAY"
    LOW_BATTERY_BEFORE_TRIP = "LOW_BATTERY_BEFORE_TRIP"
    CHARGER_CONGESTION = "CHARGER_CONGESTION"
    CHARGER_FAILURE = "CHARGER_FAILURE"
    NONFINAL_RELAXED = "NONFINAL_RELAXED"
    NONFINAL_TIGHT = "NONFINAL_TIGHT"
    NONFINAL_PRIORITY = "NONFINAL_PRIORITY"
    NONFINAL_CONFLICT = "NONFINAL_CONFLICT"
    FINAL_RELAXED = "FINAL_RELAXED"
    FINAL_TIGHT = "FINAL_TIGHT"
    FINAL_PRIORITY = "FINAL_PRIORITY"
    UNREACHABLE_CHARGER = "UNREACHABLE_CHARGER"


class ScenarioRequest(BaseModel):
    scenario: SimulationScenario = SimulationScenario.NONFINAL_RELAXED
    vehicle_count: int = Field(default=10, ge=1, le=1000)
    seed: int = 42


class SimulatorStartRequest(BaseModel):
    tick_seconds: float = Field(default=1, ge=0.1, le=60)
    time_scale: float = Field(default=60, ge=1, le=3600)


class SimulatorStatus(BaseModel):
    running: bool
    state: str = "STOPPED"
    error: str | None = None
    time_scale: float = 60
    tick_seconds: float = 1
    simulated_time: str | None = None
    tracked_vehicles: int = 0
    emitted_events: int = 0
