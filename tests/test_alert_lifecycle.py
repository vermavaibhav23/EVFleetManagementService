import unittest
from datetime import UTC, datetime, timedelta

from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip
from app.models.vehicle import Vehicle
from app.services.fleet_readiness import synchronize_readiness_alert
from app.services.readiness import assess_readiness


class FakeAlertsCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def find_one(self, query: dict) -> dict | None:
        return self.documents.get(query["dedupe_key"])

    async def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        key = query["dedupe_key"]
        existing = self.documents.get(key)
        if existing is None:
            if not upsert:
                return
            existing = {}
            existing.update(update.get("$setOnInsert", {}))
        existing.update(update.get("$set", {}))
        self.documents[key] = existing


class FakeDatabase:
    def __init__(self) -> None:
        self.alerts = FakeAlertsCollection()


class AlertLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_events_update_one_alert_then_resolve_it(self) -> None:
        db = FakeDatabase()
        vehicle = Vehicle(
            vin="SIM00000000000001",
            name="Test Van",
            depot_id="D1",
            battery_capacity_kwh=75,
            usable_capacity_kwh=70,
            consumption_kwh_per_km=0.2,
            max_charge_power_kw=60,
        )
        trip = Trip(
            trip_id="T1",
            vin=vehicle.vin,
            origin="Depot",
            destination="Customer",
            departure_time=datetime.now(UTC) + timedelta(hours=2),
            distance_km=80,
        )

        def telemetry(soc: float) -> TelemetryEvent:
            return TelemetryEvent(
                vin=vehicle.vin,
                ts=datetime.now(UTC),
                lat=0,
                lon=0,
                speed_kmh=0,
                soc_pct=soc,
                soh_pct=100,
                odo_km=100,
                seq=1,
            )

        critical = assess_readiness(vehicle, telemetry(20), trip)
        await synchronize_readiness_alert(db, critical)
        await synchronize_readiness_alert(db, critical)
        self.assertEqual(1, len(db.alerts.documents))
        self.assertEqual("OPEN", next(iter(db.alerts.documents.values()))["status"])

        next(iter(db.alerts.documents.values()))["status"] = "ACTION_SCHEDULED"
        await synchronize_readiness_alert(db, critical)
        self.assertEqual(
            "ACTION_SCHEDULED", next(iter(db.alerts.documents.values()))["status"]
        )

        safe = assess_readiness(vehicle, telemetry(90), trip)
        await synchronize_readiness_alert(db, safe)
        self.assertEqual("RESOLVED", next(iter(db.alerts.documents.values()))["status"])


if __name__ == "__main__":
    unittest.main()
