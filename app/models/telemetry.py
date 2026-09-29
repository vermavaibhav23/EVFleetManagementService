from datetime import datetime

from pydantic import BaseModel, Field


class TelemetryEvent(BaseModel):
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


class TelemetryIngestResponse(BaseModel):
    id: str
    status: str

