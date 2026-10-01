from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


def _validate_clock(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM format")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("time must use HH:MM format") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("time must use a valid 24-hour clock value")
    return f"{hour:02d}:{minute:02d}"


class Tariff(BaseModel):
    tariff_id: str = Field(min_length=1, max_length=80)
    depot_id: str = Field(min_length=1, max_length=50)
    charger_id: str | None = None
    start_time: str
    end_time: str
    price_per_kwh: float = Field(ge=0)
    timezone: str = "Asia/Kolkata"
    effective_from: date | None = None
    effective_until: date | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _validate_clock(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value
