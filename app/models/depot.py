from pydantic import BaseModel, Field


class Depot(BaseModel):
    depot_id: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    power_limit_kw: float = Field(gt=0)
