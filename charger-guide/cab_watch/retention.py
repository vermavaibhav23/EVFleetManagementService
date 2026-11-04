"""Periodic retention for the simulated fleet's high-volume and historical data."""

import asyncio
import logging
import os

log = logging.getLogger(__name__)


def positive_setting(name, default):
    value = int(os.getenv(name, default))
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def retention_settings():
    return (
        positive_setting("TRIP_RETENTION_DAYS", 90),
        positive_setting("RETENTION_INTERVAL_HOURS", 6),
    )


async def cleanup(db, trip_days):
    """Delete expired data without removing active trips or referenced vehicles."""
    async with db.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(73949204)"):
            return None
        trips = await conn.execute("""DELETE FROM cab_watch_accounts.trips
            WHERE (completed_at IS NOT NULL
                   AND completed_at < now()-($1::int * interval '1 day'))
               OR (vehicle_id IS NULL
                   AND created_at < now()-($1::int * interval '1 day'))""",
            trip_days)
        return dict(trips=trips)


async def run_retention(db, trip_days, interval_hours):
    while True:
        try:
            result = await cleanup(db, trip_days)
            if result:
                log.info("CabWatch retention cleanup: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("CabWatch retention cleanup failed; will retry")
        await asyncio.sleep(interval_hours * 3600)
