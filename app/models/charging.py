from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class ReadinessStatus(StrEnum):
    CRITICAL = "CRITICAL"
    CHARGE_SOON = "CHARGE_SOON"
    SAFE = "SAFE"
    UNKNOWN = "UNKNOWN"


class ReadinessAssessment(BaseModel):
    vin: str
    status: ReadinessStatus
    current_soc_pct: float
    current_range_km: float
    trip_distance_km: float | None = None
    post_trip_range_km: float | None = None
    reserve_range_km: float
    range_margin_km: float | None = None
    available_energy_kwh: float
    required_energy_kwh: float | None = None
    energy_deficit_kwh: float = 0
    next_trip_id: str | None = None
    next_departure_time: datetime | None = None
    health_flags: list[str] = Field(default_factory=list)
    explanation: str


class CandidateCharger(BaseModel):
    charger_id: str
    port_number: int
    start_time: datetime
    end_time: datetime
    travel_distance_km: float
    wait_minutes: float
    charging_minutes: float
    allocated_power_kw: float
    electricity_cost: float
    total_score: float
    deadline_margin_minutes: float


class ChargingPlanStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHARGING = "CHARGING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ChargingPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    vin: str
    trip_id: str | None = None
    charger_id: str
    port_number: int = Field(ge=1)
    start_time: datetime
    end_time: datetime
    starting_soc_pct: float
    target_soc_pct: float
    energy_required_kwh: float
    allocated_power_kw: float
    estimated_cost: float
    predicted_ready_time: datetime
    next_departure_time: datetime | None = None
    status: ChargingPlanStatus = ChargingPlanStatus.PROPOSED
    reason: str
    alternatives: list[CandidateCharger] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChargingRecommendation(BaseModel):
    vin: str
    readiness: ReadinessAssessment
    plan: ChargingPlan | None = None
    reason: str
