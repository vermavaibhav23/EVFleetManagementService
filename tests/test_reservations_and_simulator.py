import unittest
from datetime import UTC, datetime, timedelta

from app.models.reservation import Reservation
from app.models.vehicle import Vehicle
from app.services.reservations import (
    has_reservation_conflict,
    intervals_overlap,
    shift_window_to_now,
)
from app.services.scheduler import haversine_km
from app.services.simulator import (
    SimulatorManager,
    VehicleSimulationState,
    advance_charging_state,
    advance_driving_state,
    advance_toward_location,
    demo_departure_time,
    point_at_distance,
)


class ReservationTests(unittest.TestCase):
    def test_touching_intervals_do_not_overlap(self) -> None:
        start = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
        self.assertFalse(
            intervals_overlap(
                start,
                start + timedelta(hours=1),
                start + timedelta(hours=1),
                start + timedelta(hours=2),
            )
        )

    def test_same_port_conflicts_but_other_port_does_not(self) -> None:
        start = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
        reservation = Reservation(
            charger_id="C1",
            port_number=1,
            vin="SIM00000000000002",
            start_time=start,
            end_time=start + timedelta(hours=1),
            reserved_power_kw=50,
        )
        self.assertTrue(
            has_reservation_conflict(
                [reservation],
                "C1",
                1,
                start + timedelta(minutes=10),
                start + timedelta(minutes=20),
            )
        )
        self.assertFalse(
            has_reservation_conflict(
                [reservation],
                "C1",
                2,
                start + timedelta(minutes=10),
                start + timedelta(minutes=20),
            )
        )

    def test_late_approval_moves_the_full_window_to_now(self) -> None:
        start = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
        now = start + timedelta(hours=1)

        shifted_start, shifted_end = shift_window_to_now(
            start, start + timedelta(minutes=30), now
        )

        self.assertEqual(now, shifted_start)
        self.assertEqual(now + timedelta(minutes=30), shifted_end)


class SimulatorPhysicsTests(unittest.TestCase):
    def setUp(self) -> None:
        vehicle = Vehicle(
            vin="SIM00000000000001",
            name="Test Van",
            depot_id="D1",
            battery_capacity_kwh=75,
            usable_capacity_kwh=70,
            consumption_kwh_per_km=0.2,
            max_charge_power_kw=60,
        )
        self.state = VehicleSimulationState(
            vehicle, 50, 100, 0, 0, 1000, 0, 30, 20, "T1"
        )

    def test_driving_decreases_soc_and_route_remaining(self) -> None:
        distance = advance_driving_state(self.state, 360, 50)
        self.assertAlmostEqual(5, distance)
        self.assertAlmostEqual(15, self.state.route_remaining_km)
        self.assertLess(self.state.soc_pct, 50)

    def test_charging_increases_soc(self) -> None:
        advance_charging_state(self.state, 600, 60, 80)
        self.assertGreater(self.state.soc_pct, 50)
        self.assertLessEqual(self.state.soc_pct, 80)

    def test_demo_departures_begin_within_six_simulated_minutes(self) -> None:
        seed_time = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
        departures = [demo_departure_time(seed_time, index) for index in range(10)]

        self.assertEqual(seed_time + timedelta(minutes=1), departures[0])
        self.assertLessEqual(max(departures), seed_time + timedelta(minutes=6))

    def test_generated_customer_is_requested_distance_from_depot(self) -> None:
        destination = point_at_distance(12.9716, 77.5946, 50, 45)

        self.assertAlmostEqual(
            50,
            haversine_km(12.9716, 77.5946, *destination),
            places=2,
        )

    def test_geo_movement_updates_position_soc_and_remaining_distance(self) -> None:
        destination = point_at_distance(self.state.lat, self.state.lon, 10, 90)
        starting_soc = self.state.soc_pct

        travelled, remaining = advance_toward_location(
            self.state, *destination, elapsed_seconds=360, speed_kmh=50
        )

        self.assertAlmostEqual(5, travelled, places=2)
        self.assertAlmostEqual(5, remaining, places=2)
        self.assertLess(self.state.soc_pct, starting_soc)

    def test_reset_clears_runtime_state(self) -> None:
        manager = SimulatorManager()
        manager._states[self.state.vehicle.vin] = self.state
        manager._simulated_time = datetime.now(UTC)
        manager._emitted_events = 25

        manager.reset()

        self.assertEqual({}, manager._states)
        self.assertIsNone(manager._simulated_time)
        self.assertEqual(0, manager._emitted_events)


if __name__ == "__main__":
    unittest.main()
