from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator


class Vehicle(BaseModel):
    vin: str = Field(min_length=11, max_length=17)
    name: str = Field(min_length=1, max_length=100)
    depot_id: str = Field(min_length=1, max_length=50)
    battery_capacity_kwh: float = Field(gt=0)
    usable_capacity_kwh: float = Field(gt=0)
    consumption_kwh_per_km: float = Field(gt=0, le=2)
    max_charge_power_kw: float = Field(gt=0)
    connector_type: str = Field(default="CCS2", min_length=1, max_length=30)
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_usable_capacity(self) -> "Vehicle":
        if self.usable_capacity_kwh > self.battery_capacity_kwh:
            raise ValueError("usable_capacity_kwh cannot exceed battery_capacity_kwh")
        return self


class VehicleListResponse(BaseModel):
    vehicles: list[Vehicle]
    total: int
