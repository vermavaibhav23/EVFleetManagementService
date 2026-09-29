from pydantic import BaseModel, Field


class Charger(BaseModel):
    charger_id: str
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    available_kw: float = Field(gt=0)
    price_per_kwh: float = Field(ge=0)
    status: str = "available"


class ChargerRecommendation(BaseModel):
    vin: str
    recommendation: str
    charger: Charger | None = None
    reason: str | None = None

