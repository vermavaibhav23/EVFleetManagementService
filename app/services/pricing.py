from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.tariff import Tariff


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _clock_to_minute(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def tariff_applies(tariff: Tariff, moment: datetime) -> bool:
    local_moment = moment.astimezone(ZoneInfo(tariff.timezone))
    if tariff.effective_from and local_moment.date() < tariff.effective_from:
        return False
    if tariff.effective_until and local_moment.date() > tariff.effective_until:
        return False

    minute = _minute_of_day(local_moment)
    start = _clock_to_minute(tariff.start_time)
    end = _clock_to_minute(tariff.end_time)
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def price_at(moment: datetime, tariffs: list[Tariff], fallback_price: float) -> float:
    matching = [
        tariff.price_per_kwh for tariff in tariffs if tariff_applies(tariff, moment)
    ]
    return min(matching) if matching else fallback_price


def charging_cost(
    start: datetime,
    end: datetime,
    power_kw: float,
    tariffs: list[Tariff],
    fallback_price: float,
    efficiency: float = 0.92,
    grid_energy_kwh: float | None = None,
) -> float:
    if end <= start:
        return 0

    metered_power = (
        grid_energy_kwh / ((end - start).total_seconds() / 3600)
        if grid_energy_kwh is not None
        else power_kw
    )
    cursor = start
    total = 0.0
    while cursor < end:
        segment_end = min(
            cursor.replace(second=0, microsecond=0) + timedelta(minutes=1), end
        )
        hours = (segment_end - cursor).total_seconds() / 3600
        delivered_energy = metered_power * hours
        total += delivered_energy * price_at(cursor, tariffs, fallback_price)
        cursor = segment_end
    return round(total, 2)
