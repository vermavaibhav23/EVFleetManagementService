from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class ChargerStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    OFFLINE = "OFFLINE"
    FAULTY = "FAULTY"


class Charger(BaseModel):
    charger_id: str
    name: str
    depot_id: str = "DEPOT-01"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    available_kw: float = Field(gt=0)
    price_per_kwh: float = Field(ge=0)
    connector_type: str = "CCS2"
    port_count: int = Field(default=1, ge=1, le=20)
    status: ChargerStatus = ChargerStatus.AVAILABLE

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class ChargerAvailability(Charger):
    """Live reservation counts returned by the API, never charger configuration."""

    occupied_ports: int = 0
    reserved_ports: int = 0
    free_ports: int = 0
