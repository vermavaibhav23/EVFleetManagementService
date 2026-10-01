import unittest
from datetime import UTC, datetime, timedelta

from app.models.charging import ReadinessStatus
from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip
from app.models.vehicle import Vehicle
from app.services.readiness import assess_readiness


class ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vehicle = Vehicle(
            vin="SIM00000000000001",
            name="Test Van",
            depot_id="D1",
            battery_capacity_kwh=75,
            usable_capacity_kwh=70,
            consumption_kwh_per_km=0.2,
            max_charge_power_kw=60,
        )

    def telemetry(self, soc: float) -> TelemetryEvent:
        return TelemetryEvent(
            vin=self.vehicle.vin,
            ts=datetime.now(UTC),
            lat=12.97,
            lon=77.59,
            speed_kmh=0,
            soc_pct=soc,
            soh_pct=100,
            odo_km=1000,
            seq=1,
        )

    def trip(self, distance: float) -> Trip:
        return Trip(
            trip_id="T1",
            vin=self.vehicle.vin,
            origin="Depot",
            destination="Customer",
            departure_time=datetime.now(UTC) + timedelta(hours=2),
            distance_km=distance,
        )

    def test_critical_when_trip_and_reserve_exceed_range(self) -> None:
        assessment = assess_readiness(self.vehicle, self.telemetry(20), self.trip(80))
        self.assertEqual(ReadinessStatus.CRITICAL, assessment.status)
        self.assertAlmostEqual(70, assessment.current_range_km)
        self.assertAlmostEqual(-25, assessment.range_margin_km)
        self.assertGreater(assessment.energy_deficit_kwh, 0)

    def test_safe_and_distance_values_are_derived_from_energy(self) -> None:
        assessment = assess_readiness(self.vehicle, self.telemetry(80), self.trip(60))
        self.assertEqual(ReadinessStatus.SAFE, assessment.status)
        self.assertAlmostEqual(280, assessment.current_range_km)
        self.assertAlmostEqual(220, assessment.post_trip_range_km)
        self.assertAlmostEqual(205, assessment.range_margin_km)

    def test_health_flag_is_independent_of_readiness(self) -> None:
        telemetry = self.telemetry(80)
        telemetry.battery_temperature_c = 48
        assessment = assess_readiness(self.vehicle, telemetry, self.trip(60))
        self.assertEqual(ReadinessStatus.SAFE, assessment.status)
        self.assertIn("HIGH_BATTERY_TEMPERATURE", assessment.health_flags)


if __name__ == "__main__":
    unittest.main()
