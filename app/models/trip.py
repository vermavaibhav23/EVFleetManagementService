from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class TripStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Trip(BaseModel):
    trip_id: str = Field(min_length=1, max_length=80)
    vin: str = Field(min_length=11, max_length=17)
    origin: str = Field(min_length=1, max_length=150)
    destination: str = Field(min_length=1, max_length=150)
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)
    destination_lat: float | None = Field(default=None, ge=-90, le=90)
    destination_lon: float | None = Field(default=None, ge=-180, le=180)
    departure_time: AwareDatetime
    delivery_deadline: AwareDatetime | None = None
    distance_km: float = Field(gt=0)
    service_duration_minutes: int = Field(default=20, ge=0, le=1440)
    status: TripStatus = TripStatus.PLANNED
    simulation_enabled: bool = True
    sequence: int = 1
    service_until: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    accepted_delay: bool = False
    reserve_exception: bool = False
    recovery_requested: bool = False
    transfer_from_vin: str | None = None
    handover_trip_id: str | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("departure_time")
    @classmethod
    def departure_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("departure_time must include a timezone")
        return value


class TripUpdate(BaseModel):
    departure_time: AwareDatetime | None = None
    delivery_deadline: AwareDatetime | None = None
    distance_km: float | None = Field(default=None, gt=0)
    status: TripStatus | None = None
