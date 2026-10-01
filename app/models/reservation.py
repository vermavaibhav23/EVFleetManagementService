from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ReservationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    VEHICLE_EN_ROUTE = "VEHICLE_EN_ROUTE"
    OCCUPIED = "OCCUPIED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


ACTIVE_RESERVATION_STATUSES = {
    ReservationStatus.CONFIRMED,
    ReservationStatus.VEHICLE_EN_ROUTE,
    ReservationStatus.OCCUPIED,
}


class Reservation(BaseModel):
    reservation_id: str = Field(default_factory=lambda: str(uuid4()))
    charger_id: str
    port_number: int = Field(default=1, ge=1)
    vin: str = Field(min_length=11, max_length=17)
    plan_id: str | None = None
    start_time: datetime
    end_time: datetime
    reserved_power_kw: float = Field(gt=0)
    status: ReservationStatus = ReservationStatus.CONFIRMED
    grace_period_minutes: int = Field(default=10, ge=0, le=60)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("start_time", "end_time")
    @classmethod
    def interval_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reservation times must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "Reservation":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ReservationUpdate(BaseModel):
    status: ReservationStatus
