import unittest
from datetime import UTC, datetime, timedelta

from app.models.charger import Charger
from app.models.reservation import Reservation
from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip
from app.models.vehicle import Vehicle
from app.services.readiness import assess_readiness
from app.services.scheduler import create_recommendation


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
        self.vehicle = Vehicle(
            vin="SIM00000000000001",
            name="Test Van",
            depot_id="D1",
            battery_capacity_kwh=75,
            usable_capacity_kwh=70,
            consumption_kwh_per_km=0.2,
            max_charge_power_kw=60,
        )
        self.telemetry = TelemetryEvent(
            vin=self.vehicle.vin,
            ts=self.now,
            lat=12.9716,
            lon=77.5946,
            speed_kmh=0,
            soc_pct=20,
            soh_pct=100,
            odo_km=1000,
            seq=1,
        )
        self.trip = Trip(
            trip_id="T1",
            vin=self.vehicle.vin,
            origin="Depot",
            destination="Customer",
            departure_time=self.now + timedelta(hours=1),
            distance_km=100,
        )
        self.readiness = assess_readiness(self.vehicle, self.telemetry, self.trip)

    def charger(self, charger_id: str, price: float) -> Charger:
        return Charger(
            charger_id=charger_id,
            name=charger_id,
            depot_id=charger_id,
            lat=12.9716,
            lon=77.5946,
            available_kw=60,
            price_per_kwh=price,
        )

    def test_chooses_cheapest_feasible_charger(self) -> None:
        recommendation = create_recommendation(
            self.vehicle,
            self.telemetry,
            self.trip,
            self.readiness,
            [self.charger("EXPENSIVE", 12), self.charger("CHEAP", 7)],
            [],
            [],
            now=self.now,
        )
        self.assertIsNotNone(recommendation.plan)
        self.assertEqual("CHEAP", recommendation.plan.charger_id)

    def test_rejects_cheap_charger_when_reservation_breaks_deadline(self) -> None:
        cheap = self.charger("CHEAP", 7)
        expensive = self.charger("EXPENSIVE", 12)
        reservation = Reservation(
            charger_id="CHEAP",
            vin="SIM00000000000002",
            start_time=self.now,
            end_time=self.now + timedelta(minutes=55),
            reserved_power_kw=60,
        )
        recommendation = create_recommendation(
            self.vehicle,
            self.telemetry,
            self.trip,
            self.readiness,
            [cheap, expensive],
            [reservation],
            [],
            now=self.now,
        )
        self.assertIsNotNone(recommendation.plan)
        self.assertEqual("EXPENSIVE", recommendation.plan.charger_id)

    def test_respects_shared_depot_power_limit(self) -> None:
        cheap = self.charger("CHEAP", 7)
        cheap.depot_id = "D1"
        occupied = self.charger("OCCUPIED", 7)
        occupied.depot_id = "D1"
        occupied.connector_type = "CHAdeMO"
        expensive = self.charger("EXPENSIVE", 12)
        expensive.depot_id = "D2"
        reservation = Reservation(
            charger_id="OCCUPIED",
            vin="SIM00000000000002",
            start_time=self.now,
            end_time=self.now + timedelta(hours=1),
            reserved_power_kw=60,
        )
        recommendation = create_recommendation(
            self.vehicle,
            self.telemetry,
            self.trip,
            self.readiness,
            [cheap, occupied, expensive],
            [reservation],
            [],
            depot_power_limits={"D1": 60, "D2": 60},
            now=self.now,
        )
        self.assertIsNotNone(recommendation.plan)
        self.assertEqual("EXPENSIVE", recommendation.plan.charger_id)


if __name__ == "__main__":
    unittest.main()
