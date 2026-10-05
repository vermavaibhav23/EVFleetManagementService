"""Domain contracts shared by the planner, device simulator and telemetry API."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import AliasChoices, AwareDatetime, ConfigDict, Field, model_validator
from pydantic import BaseModel as PydanticModel


class BaseModel(PydanticModel):
    model_config = ConfigDict(allow_inf_nan=False)


class Scenario(StrEnum):
    EVERYDAY_CHOICES = "EVERYDAY_CHOICES"
    SHARED_CHARGERS = "SHARED_CHARGERS"
    DELIVERY_DELAYS = "DELIVERY_DELAYS"
    ASSISTANCE_CASES = "ASSISTANCE_CASES"
    NORMAL_DAY = "NORMAL_DAY"
    EDGE_CASE_DAY = "EDGE_CASE_DAY"


class LoadRequest(BaseModel):
    scenario: Scenario = Scenario.NORMAL_DAY
    vehicle_count: int = Field(default=12, ge=1, le=100)
    seed: int = 42
    start_time: AwareDatetime | None = None

    @model_validator(mode="after")
    def coverage(self):
        if (
            self.scenario not in (Scenario.NORMAL_DAY, Scenario.EDGE_CASE_DAY)
            and self.vehicle_count < 4
        ):
            raise ValueError(
                "These scenarios need at least 4 vehicles to include every core case"
            )
        if self.scenario == Scenario.EDGE_CASE_DAY and self.vehicle_count < 12:
            raise ValueError("Edge-case coverage requires at least 12 vehicles")
        return self


class Policy(BaseModel):
    reserve_kwh: float = Field(default=3, ge=0)
    efficiency: float = Field(default=0.92, gt=0, le=1)
    speed_kmh: float = Field(default=35, gt=0)
    travel_allowance_pct: float = Field(default=0.05, ge=0, le=1)
    waiting_allowance_minutes: float = Field(default=1, ge=0)
    connection_minutes: float = Field(default=1, ge=0)
    release_minutes: float = Field(default=1, ge=0)
    horizon_minutes: float = Field(default=720, gt=0, le=1440)
    solver_seconds: float = Field(default=8, gt=0, le=60)
    max_customers: int = Field(default=8, ge=1, le=12)
    max_stations: int = Field(default=4, ge=1, le=6)
    taper: bool = True


class Delivery(BaseModel):
    trip_id: str
    sequence: int = Field(ge=1)
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    deadline: AwareDatetime
    ready_at: AwareDatetime
    accepts_at: AwareDatetime
    service_minutes: float = Field(default=4, ge=0)
    is_return: bool = False
    status: str = "PLANNED"
    arrived_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None


class Vehicle(BaseModel):
    vin: str
    name: str
    depot_id: str
    lat: float
    lon: float
    capacity_kwh: float = Field(gt=0)
    soh_pct: float = Field(default=100, ge=0, le=100)
    energy_kwh: float = Field(ge=0)
    consumption_kwh_km: float = Field(default=0.25, gt=0)
    max_power_kw: float = Field(default=60, gt=0)
    connector: str = "CCS2"
    temperature_c: float = 30.0
    health_fault: bool = False
    state: str = "PARKED"
    sequence: int = 0
    service_until: AwareDatetime | None = None
    release_until: AwareDatetime | None = None
    plan_id: str | None = None
    operation_index: int = 0
    deliveries: list[Delivery] = Field(default_factory=list)
    incident: str | None = None
    case: str | None = None

    @model_validator(mode="after")
    def valid(self):
        order = [d.sequence for d in self.deliveries]
        if len(set(order)) != len(order):
            raise ValueError("Customer sequence must be unique")
        if self.energy_kwh > self.capacity_kwh * self.soh_pct / 100 + 1e-6:
            raise ValueError("Energy exceeds effective capacity")
        self.deliveries.sort(key=lambda d: d.sequence)
        if any(d.is_return for d in self.deliveries[:-1]):
            raise ValueError("An explicit return must be the final stop")
        return self


class Tariff(BaseModel):
    id: str
    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(ge=0, le=1440)
    price: float = Field(ge=0)
    priority: int = 0
    from_date: str | None = Field(default=None, alias="from")
    until: str | None = None

    @model_validator(mode="after")
    def dates(self):
        from datetime import date

        for value in (self.from_date, self.until):
            if value:
                date.fromisoformat(value)
        if self.from_date and self.until and self.from_date > self.until:
            raise ValueError("Tariff validity is reversed")
        return self


class Station(BaseModel):
    charger_id: str
    name: str
    depot_id: str
    lat: float
    lon: float
    port_count: int = Field(default=2, ge=1, le=8)
    power_kw: float = Field(gt=0)
    price: float = Field(ge=0)
    connector: str = "CCS2"
    status: str = "AVAILABLE"
    # Daily local intervals; station entries override depot entries only when applicable.
    tariffs: list[Tariff] = Field(default_factory=list)


class Operation(BaseModel):
    stop_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["CHARGE", "DELIVERY", "RETURN"]
    name: str
    lat: float
    lon: float
    trip_id: str | None = None
    charger_id: str | None = None
    port: int | None = None
    # Derived leg-start estimate / execution timestamp, never a dispatch appointment.
    depart: AwareDatetime
    arrival: AwareDatetime
    start: AwareDatetime
    end: AwareDatetime
    energy_arrival: float
    energy_end: float
    grid_kwh: float = 0
    cost: float = 0
    power_kw: float = 0
    target_soc: float | None = None
    deadline: AwareDatetime | None = None
    lateness_minutes: float = 0
    status: str = "PLANNED"
    actual_arrival: AwareDatetime | None = None
    actual_start: AwareDatetime | None = None
    actual_end: AwareDatetime | None = None
    actual_grid_kwh: float = 0
    actual_cost: float = 0

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "CHARGE" and (
            not self.charger_id
            or not self.port
            or self.power_kw <= 0
            or self.target_soc is None
        ):
            raise ValueError(
                "Charging operation requires charger, port, power and target"
            )
        if self.kind == "DELIVERY" and (not self.trip_id or not self.deadline):
            raise ValueError(
                "Delivery operation requires a stable trip ID and original deadline"
            )
        return self


class JourneyPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    vin: str
    version: int = 1
    snapshot_revision: int
    fingerprint: str
    created_at: AwareDatetime
    valid_until: AwareDatetime
    status: str = "PROPOSED"
    recovery: bool = False
    reserve_kwh: float = Field(ge=0)
    initial_reserve_exception: bool = False
    operations: list[Operation] = Field(min_length=1, max_length=27)
    total_cost: float
    solver: dict
    reason: str
    replaces: str | None = None


class Approval(BaseModel):
    run_id: str
    version: int = 1
    acknowledge_recovery: bool = False


class PlanRequest(BaseModel):
    run_id: str | None = None
    pause_for_review: bool = False
    compare_tradeoffs: bool = False
    recovery: bool = False
    reserve_exception: bool = False
    alternatives: bool = True


class Telemetry(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    run_id: str
    vehicle_id: str = Field(validation_alias=AliasChoices("vehicle_id", "vin"))
    sequence: int = Field(ge=1)
    observed_at: AwareDatetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    energy_kwh: float = Field(ge=0)
    activity: Literal[
        "PARKED",
        "READY",
        "TRAVELLING",
        "QUEUING",
        "CONNECTING",
        "CHARGING",
        "RELEASING",
        "SERVICING",
        "WAITING_WINDOW",
        "WAITING_REVIEW",
        "ASSISTANCE",
        "COMPLETED",
    ]
    temperature_c: float = 30.0
    health_fault: bool = False
    control_version: int = Field(default=0, ge=0)


class ClockAction(BaseModel):
    run_id: str
    speed: float = Field(default=30, gt=0, le=600)
    seconds: float = Field(default=60, gt=0, le=86400)


class ResourceChange(BaseModel):
    run_id: str
    power_limit_kw: float | None = Field(default=None, ge=0)
    station: Station | None = None


def effective_capacity(vehicle: dict) -> float:
    return vehicle["capacity_kwh"] * vehicle["soh_pct"] / 100


def dt(value) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value
