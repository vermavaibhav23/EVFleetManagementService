"""Tariff precedence and exact integration of the shared charging curve."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.services.energy import charge_for_seconds, charging_seconds


def price_at(moment, station, depot_tariffs=()):
    local = moment.astimezone(ZoneInfo("Asia/Kolkata"))
    minute = local.hour * 60 + local.minute

    def matching(rows):
        result = []
        for r in rows:
            if r.get("from") and local.date().isoformat() < r["from"]:
                continue
            if r.get("until") and local.date().isoformat() > r["until"]:
                continue
            start, end = r["start_minute"], r["end_minute"]
            if start == end or (
                start <= minute < end
                if start < end
                else minute >= start or minute < end
            ):
                result.append(r)
        return sorted(result, key=lambda r: (-r.get("priority", 0), r["id"]))

    rows = matching(station.get("tariffs", [])) or matching(depot_tariffs)
    return float(rows[0]["price"] if rows else station["price"])


def intervals(start, end, station, depot_tariffs=()):
    result, cursor = [], start
    while cursor < end:
        stop = min(end, cursor.replace(second=0, microsecond=0) + timedelta(minutes=1))
        price = price_at(cursor, station, depot_tariffs)
        if result and result[-1][2] == price:
            result[-1] = (result[-1][0], stop, price)
        else:
            result.append((cursor, stop, price))
        cursor = stop
    return result


def session(
    start,
    energy,
    target,
    capacity,
    power,
    efficiency,
    station,
    depot_tariffs=(),
    taper=True,
):
    """Duration, metered energy and unrounded cost. Connection time is not billed."""
    if (
        target < energy - 1e-7
        or target > capacity + 1e-7
        or capacity <= 0
        or power <= 0
    ):
        raise ValueError("Invalid charging state")
    soc, target_soc = energy / capacity * 100, min(100, target / capacity * 100)
    seconds = (
        charging_seconds(soc, target_soc, capacity, power, efficiency)
        if taper
        else (target - energy) / power / efficiency * 3600
    )
    end = start + timedelta(seconds=seconds)
    cost, grid = 0.0, 0.0
    for a, b, price in intervals(start, end, station, depot_tariffs):
        elapsed = (b - a).total_seconds()
        if taper:
            soc, added = charge_for_seconds(
                soc, target_soc, capacity, power, efficiency, elapsed
            )
        else:
            added = power * efficiency * elapsed / 3600
        purchased = added / efficiency
        grid += purchased
        cost += price * purchased
    return seconds, grid, cost
