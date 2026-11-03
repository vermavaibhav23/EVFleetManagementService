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
        positive_setting("GPS_RETENTION_DAYS", 7),
        positive_setting("TRIP_RETENTION_DAYS", 90),
        positive_setting("FLEET_RETENTION_DAYS", 365),
        positive_setting("RETENTION_INTERVAL_HOURS", 6),
    )


async def cleanup(db, gps_days, trip_days, fleet_days):
    """Delete expired data without removing active trips or referenced vehicles."""
    async with db.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(73949204)"):
            return None
        gps = await conn.execute("""DELETE FROM cab_watch.gps_readings g
            USING cab_watch.trips t
            WHERE g.trip_id=t.id AND t.completed_at IS NOT NULL
              AND g.sequence<>t.last_sequence
              AND g.received_at < now()-($1::int * interval '1 day')""", gps_days)
        trips = await conn.execute("""DELETE FROM cab_watch.trips
            WHERE (completed_at IS NOT NULL
                   AND completed_at < now()-($1::int * interval '1 day'))
               OR (vehicle_id IS NULL
                   AND created_at < now()-($1::int * interval '1 day'))""",
            trip_days)
        vehicles = await conn.execute("""DELETE FROM cab_watch.vehicles v
            WHERE v.created_at < now()-($1::int * interval '1 day')
              AND NOT EXISTS(SELECT 1 FROM cab_watch.trips t WHERE t.vehicle_id=v.id)""",
            fleet_days)
        return dict(gps=gps, trips=trips, vehicles=vehicles)


async def run_retention(db, gps_days, trip_days, fleet_days, interval_hours):
    while True:
        try:
            result = await cleanup(db, gps_days, trip_days, fleet_days)
            if result:
                log.info("CabWatch retention cleanup: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("CabWatch retention cleanup failed; will retry")
        await asyncio.sleep(interval_hours * 3600)
