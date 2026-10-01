from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class OperatingState(StrEnum):
    PARKED = "PARKED"
    DRIVING = "DRIVING"
    AT_CUSTOMER = "AT_CUSTOMER"
    WAITING_TO_CHARGE = "WAITING_TO_CHARGE"
    CHARGING = "CHARGING"
    READY = "READY"
    OFFLINE = "OFFLINE"


class TelemetryEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    vin: str = Field(min_length=11, max_length=17)
    ts: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_kmh: float = Field(ge=0)
    soc_pct: float = Field(ge=0, le=100)
    soh_pct: float | None = Field(default=None, ge=0, le=100)
    odo_km: float = Field(ge=0)
    evt: str = "TELEMETRY"
    seq: int = Field(ge=0)
    dtc: list[str] = Field(default_factory=list)
    battery_temperature_c: float | None = Field(default=None, ge=-50, le=100)
    power_kw: float | None = None
    operating_state: OperatingState = OperatingState.PARKED
    trip_id: str | None = None
    route_remaining_km: float | None = Field(default=None, ge=0)
    remaining_range_km: float | None = Field(default=None, ge=0)
    charger_id: str | None = None
    is_plugged_in: bool = False

    @field_validator("ts")
    @classmethod
    def timestamp_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts must include a timezone")
        return value


class TelemetryIngestResponse(BaseModel):
    id: str
    status: str
