import asyncio
import json
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
from mongomock_motor import AsyncMongoMockClient

from app.main import app
from app.models.simulator import ScenarioRequest, SimulationScenario
from app.models.telemetry import OperatingState, TelemetryEvent
from app.models.trip import TripUpdate
from app.services.energy import charge_for_seconds, charging_seconds
from app.services.fleet_readiness import update_charging_lifecycle
from app.services.scheduler import _round_up, haversine_km
from app.services.simulator import (
    SimulatorManager,
    advance_toward_location,
    seed_scenario,
)
from app.services.telemetry import store_telemetry


class MemoryRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, **kwargs):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def scan_iter(self, **kwargs):
        for key in list(self.data):
            if key.startswith("vehicle:SIM"):
                yield key


class MemoryKafka:
    def __init__(self):
        self.events = []
        self.fail = False

    async def publish(self, topic, payload, key=None):
        if self.fail:
            raise ConnectionError("broker unavailable")
        self.events.append(payload)


class DemoJourneyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient(tz_aware=True).fleet
        await self.db.telemetry.create_index("event_id", unique=True)
        await self.db.charging_plans.create_index(
            "vin", unique=True, partialFilterExpression={"active": True}
        )
        self.redis = MemoryRedis()
        self.kafka = MemoryKafka()
        self.manager = SimulatorManager()
        app.state.simulator = self.manager
        self.patches = ExitStack()
        for module in (
            "charging",
            "simulator",
            "fleet",
            "vehicles",
            "trips",
            "chargers",
            "reservations",
            "telemetry",
            "alerts",
        ):
            self.patches.enter_context(
                patch(f"app.api.v1.{module}.get_database", return_value=self.db)
            )
        for module in ("simulator", "vehicles", "telemetry"):
            self.patches.enter_context(
                patch(f"app.api.v1.{module}.get_redis", return_value=self.redis)
            )
        for module in ("simulator", "telemetry"):
            self.patches.enter_context(
                patch(f"app.api.v1.{module}.get_kafka_bus", return_value=self.kafka)
            )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
        )
        await self.seed()

    async def asyncTearDown(self):
        await self.manager.stop()
        await self.client.aclose()
        self.patches.close()

    async def seed(self, scenario=SimulationScenario.LOW_BATTERY_BEFORE_TRIP, count=10):
        await seed_scenario(
            ScenarioRequest(scenario=scenario, vehicle_count=count),
            self.db,
            self.redis,
            self.kafka,
        )
        self.manager.reset()
        self.manager._db, self.manager._redis, self.manager._kafka = (
            self.db,
            self.redis,
            self.kafka,
        )
        self.manager._simulated_time = await self.manager._load_states()
        self.manager._scenario = scenario.value
        self.vin = "SIM00000000000001"

    async def latest(self, vin=None):
        return await self.db.telemetry.find_one(
            {"vin": vin or self.vin}, sort=[("ts", -1), ("seq", -1)]
        )

    async def plan(self):
        response = await self.client.post(f"/charging/plans/{self.vin}")
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    async def test_complete_journey_and_port_release(self):
        await self.manager._tick()
        self.assertEqual("DRIVING", (await self.latest())["operating_state"])
        plan = await self.plan()
        approval = await self.client.post(f"/charging/plans/{plan['plan_id']}/approve")
        self.assertEqual(200, approval.status_code, approval.text)
        seen = set()
        previous = await self.latest()
        for _ in range(260):
            await self.manager._tick()
            event = await self.latest()
            state = event["operating_state"]
            seen.add(state)
            if state == "CHARGING":
                self.assertTrue(event["is_plugged_in"])
                reservation = await self.db.reservations.find_one(
                    {"plan_id": plan["plan_id"]}
                )
                self.assertEqual("OCCUPIED", reservation["status"])
                charger = await self.db.chargers.find_one(
                    {"charger_id": event["charger_id"]}
                )
                self.assertLess(
                    haversine_km(
                        event["lat"], event["lon"], charger["lat"], charger["lon"]
                    ),
                    0.001,
                )
                self.assertGreater(event["soc_pct"], previous["soc_pct"])
            elif state in {"DRIVING", "RESUMING_TRIP", "EN_ROUTE_TO_CHARGER"}:
                self.assertLess(event["soc_pct"], previous["soc_pct"])
                if previous["navigation_target"] == event["navigation_target"]:
                    self.assertLessEqual(
                        event["distance_to_destination_km"],
                        previous["distance_to_destination_km"],
                    )
            previous = event
            if state == "AT_CUSTOMER":
                break
        self.assertTrue(
            {
                "EN_ROUTE_TO_CHARGER",
                "WAITING_FOR_CHARGER",
                "CHARGING",
                "READY",
                "RESUMING_TRIP",
                "AT_CUSTOMER",
            }.issubset(seen),
            seen,
        )
        self.assertEqual(
            "COMPLETED",
            (await self.db.charging_plans.find_one({"plan_id": plan["plan_id"]}))[
                "status"
            ],
        )
        self.assertEqual(
            "COMPLETED",
            (await self.db.reservations.find_one({"plan_id": plan["plan_id"]}))[
                "status"
            ],
        )
        for _ in range(3):
            await self.manager._tick()
        self.assertEqual("AT_CUSTOMER", (await self.latest())["operating_state"])
        await self.manager._load_states()
        await self.manager._tick()
        self.assertEqual("AT_CUSTOMER", (await self.latest())["operating_state"])

    async def test_repeated_concurrent_plan_lifecycle_requests(self):
        responses = await asyncio.gather(
            *(self.client.post(f"/charging/plans/{self.vin}") for _ in range(5))
        )
        ids = {r.json()["plan_id"] for r in responses}
        self.assertEqual(1, len(ids))
        plan_id = ids.pop()
        approved = await asyncio.gather(
            *(self.client.post(f"/charging/plans/{plan_id}/approve") for _ in range(5))
        )
        self.assertTrue(
            all(r.status_code == 200 for r in approved), [r.text for r in approved]
        )
        self.assertEqual(
            1, await self.db.reservations.count_documents({"plan_id": plan_id})
        )
        for action in ("cancel", "cancel"):
            self.assertEqual(
                200,
                (
                    await self.client.post(f"/charging/plans/{plan_id}/{action}")
                ).status_code,
            )
        new_plan = await self.plan()
        self.assertNotEqual(plan_id, new_plan["plan_id"])
        for _ in range(2):
            self.assertEqual(
                200,
                (
                    await self.client.post(
                        f"/charging/plans/{new_plan['plan_id']}/reject"
                    )
                ).status_code,
            )
        self.assertNotEqual(new_plan["plan_id"], (await self.plan())["plan_id"])

    async def test_concurrent_vehicles_never_double_book_a_port(self):
        # Give four independent vehicles a charging need and one common deadline.
        for i in range(1, 5):
            vin = f"SIM{i:014d}"
            event = TelemetryEvent(**await self.latest(vin)).model_copy(
                update={"event_id": f"low-{i}", "seq": 1, "soc_pct": 20}
            )
            await store_telemetry(event, self.db, self.redis, self.kafka)
        plans = await asyncio.gather(
            *(self.client.post(f"/charging/plans/SIM{i:014d}") for i in range(1, 5))
        )
        results = await asyncio.gather(
            *(
                self.client.post(f"/charging/plans/{r.json()['plan_id']}/approve")
                for r in plans
            )
        )
        self.assertTrue(
            all(r.status_code == 200 for r in results), [r.text for r in results]
        )
        reservations = await self.db.reservations.find({}).to_list(None)
        for i, first in enumerate(reservations):
            for second in reservations[i + 1 :]:
                if (first["charger_id"], first["port_number"]) == (
                    second["charger_id"],
                    second["port_number"],
                ):
                    self.assertFalse(
                        first["start_time"] < second["end_time"]
                        and first["end_time"] > second["start_time"]
                    )

    async def test_seed_while_running_stops_and_removes_cache_and_stale_events(self):
        old = TelemetryEvent(**await self.latest())
        self.redis.data["vehicle:SIM99999999999999:latest"] = "old"
        await self.client.post(
            "/simulator/start", json={"tick_seconds": 0.1, "time_scale": 60}
        )
        response = await self.client.post(
            "/simulator/scenarios", json={"vehicle_count": 2}
        )
        self.assertEqual(200, response.status_code)
        self.assertFalse(self.manager.status().running)
        self.assertEqual(0, self.manager.status().emitted_events)
        self.assertNotIn("vehicle:SIM99999999999999:latest", self.redis.data)
        self.assertEqual(
            "stale_generation",
            (await store_telemetry(old, self.db, self.redis, self.kafka))[1],
        )
        self.assertEqual(2, await self.db.telemetry.count_documents({}))

    async def test_concurrent_start_and_stop_are_truthful(self):
        await asyncio.gather(
            *(
                self.manager.start(self.db, self.redis, self.kafka, 0.1, 60)
                for _ in range(5)
            )
        )
        tasks = [t for t in asyncio.all_tasks() if t.get_name() == "ev-simulator"]
        self.assertEqual(1, len(tasks))
        await asyncio.sleep(0.15)
        await self.manager.stop()
        count = len(self.kafka.events)
        await asyncio.sleep(0.15)
        self.assertEqual(count, len(self.kafka.events))
        self.assertFalse(self.manager.status().running)

    async def test_background_failure_visible_and_recoverable(self):
        self.kafka.fail = True
        await self.manager.start(self.db, self.redis, self.kafka, 0.1, 60)
        await asyncio.sleep(0.15)
        self.assertEqual("FAILED", self.manager.status().state)
        self.assertIn("ConnectionError", self.manager.status().error)
        await self.manager.stop()
        self.kafka.fail = False
        await self.manager.start(self.db, self.redis, self.kafka, 0.1, 60)
        await asyncio.sleep(0.15)
        self.assertTrue(self.manager.status().running)
        self.assertIsNone(self.manager.status().error)

    async def test_stale_or_wrong_port_telemetry_cannot_complete_plan(self):
        plan = await self.plan()
        await self.client.post(f"/charging/plans/{plan['plan_id']}/approve")
        event = TelemetryEvent(**await self.latest()).model_copy(
            update={
                "soc_pct": 100,
                "is_plugged_in": True,
                "charger_id": plan["charger_id"],
            }
        )
        await update_charging_lifecycle(self.db, event)
        self.assertEqual(
            "APPROVED",
            (await self.db.charging_plans.find_one({"plan_id": plan["plan_id"]}))[
                "status"
            ],
        )

    async def test_missing_reservation_cannot_charge(self):
        plan = await self.plan()
        await self.client.post(f"/charging/plans/{plan['plan_id']}/approve")
        await self.db.reservations.delete_many({})
        await self.manager._tick()
        self.assertNotEqual("CHARGING", (await self.latest())["operating_state"])
        self.assertEqual(
            "CANCELLED",
            (await self.db.charging_plans.find_one({"plan_id": plan["plan_id"]}))[
                "status"
            ],
        )

    async def test_all_scenarios_and_failed_charger_and_overheating(self):
        for scenario in SimulationScenario:
            await self.seed(scenario)
            if scenario == SimulationScenario.UNEXPECTED_LONG_TRIP:
                for _ in range(6):
                    await self.manager._tick()
            response = await self.client.post(f"/charging/plans/{self.vin}")
            if scenario in {
                SimulationScenario.NORMAL_DAY,
                SimulationScenario.BATTERY_OVERHEATING,
            }:
                self.assertEqual(422, response.status_code, response.text)
            else:
                self.assertEqual(201, response.status_code, response.text)
                plan = response.json()
                charger = await self.db.chargers.find_one(
                    {"charger_id": plan["charger_id"]}
                )
                self.assertEqual("AVAILABLE", charger["status"])
            await self.manager._tick()
            if scenario == SimulationScenario.BATTERY_OVERHEATING:
                self.assertGreaterEqual(
                    (await self.latest())["battery_temperature_c"], 45
                )

    async def test_zero_energy_stops_at_actual_range(self):
        state = self.manager._states[self.vin]
        state.soc_pct = 0.01
        available = (
            state.vehicle.usable_capacity_kwh
            * state.soh_pct
            / 100
            * state.soc_pct
            / 100
            / state.vehicle.consumption_kwh_per_km
        )
        distance, _ = advance_toward_location(state, 14, 78, 3600, 60)
        self.assertAlmostEqual(available, distance)
        self.assertAlmostEqual(0, state.soc_pct)
        await self.manager._tick()
        self.assertEqual("STRANDED", (await self.latest())["operating_state"])

    async def test_cache_does_not_regress_and_duplicate_is_idempotent(self):
        old = TelemetryEvent(**await self.latest())
        await self.manager._tick()
        latest = await self.latest()
        self.assertEqual(
            "duplicate",
            (await store_telemetry(old, self.db, self.redis, self.kafka))[1],
        )
        cached = json.loads(await self.redis.get(f"vehicle:{self.vin}:latest"))
        self.assertEqual(latest["event_id"], cached["event_id"])

    def test_slot_rounding_never_precedes_arrival_and_taper_matches(self):
        now = datetime(2026, 10, 1, 10, 15, 1, tzinfo=UTC)
        self.assertEqual(30, _round_up(now, 15).minute)
        seconds = charging_seconds(75, 95, 70, 60, 0.92)
        soc, energy = charge_for_seconds(75, 95, 70, 60, 0.92, seconds)
        self.assertAlmostEqual(95, soc)
        self.assertAlmostEqual(14, energy)

    def test_trip_update_rejects_naive_datetime(self):
        with self.assertRaises(ValueError):
            TripUpdate(departure_time="2026-10-01T10:00:00")

    async def test_approval_rechecks_failed_charger(self):
        plan = await self.plan()
        await self.db.chargers.update_one(
            {"charger_id": plan["charger_id"]}, {"$set": {"status": "FAULTY"}}
        )
        response = await self.client.post(f"/charging/plans/{plan['plan_id']}/approve")
        self.assertEqual(200, response.status_code, response.text)
        self.assertNotEqual(plan["charger_id"], response.json()["charger_id"])

    async def test_confirmed_reservation_blocks_physical_charging(self):
        plan = await self.plan()
        approved = (
            await self.client.post(f"/charging/plans/{plan['plan_id']}/approve")
        ).json()
        charger = await self.db.chargers.find_one(
            {"charger_id": approved["charger_id"]}
        )
        state = self.manager._states[self.vin]
        state.lat, state.lon = charger["lat"], charger["lon"]
        self.manager._simulated_time = datetime.fromisoformat(approved["start_time"])
        await self.db.reservations.insert_one(
            {
                "reservation_id": "block",
                "vin": "SIM00000000000002",
                "plan_id": "other",
                "charger_id": approved["charger_id"],
                "port_number": approved["port_number"],
                "start_time": self.manager._simulated_time,
                "end_time": self.manager._simulated_time + timedelta(hours=1),
                "status": "CONFIRMED",
                "reserved_power_kw": 60,
            }
        )
        soc = state.soc_pct
        await self.manager._tick()
        self.assertEqual(
            "WAITING_FOR_CHARGER", (await self.latest())["operating_state"]
        )
        self.assertEqual(soc, state.soc_pct)
        self.assertFalse((await self.latest())["is_plugged_in"])

    async def test_ready_resume_survives_manager_reload(self):
        event = TelemetryEvent(**await self.latest()).model_copy(
            update={
                "event_id": "ready-event",
                "seq": 1,
                "operating_state": OperatingState.READY,
                "soc_pct": 80,
            }
        )
        await store_telemetry(event, self.db, self.redis, self.kafka)
        await self.manager._load_states()
        self.assertTrue(self.manager._states[self.vin].resume_pending)
        await self.manager._tick()
        self.assertEqual("RESUMING_TRIP", (await self.latest())["operating_state"])

    async def test_delayed_approval_rejects_missed_delivery_deadline(self):
        plan = await self.plan()
        await self.db.trips.update_one(
            {"vin": self.vin},
            {
                "$set": {
                    "delivery_deadline": self.manager._simulated_time
                    + timedelta(minutes=2)
                }
            },
        )
        response = await self.client.post(f"/charging/plans/{plan['plan_id']}/approve")
        self.assertEqual(422, response.status_code)
        self.assertEqual(
            0, await self.db.reservations.count_documents({"plan_id": plan["plan_id"]})
        )

    async def test_battery_target_includes_charger_to_customer_and_reserve(self):
        plan = await self.plan()
        vehicle = self.manager._states[self.vin]
        capacity = vehicle.vehicle.usable_capacity_kwh * vehicle.soh_pct / 100
        required = (
            plan["remaining_delivery_km"] + 30
        ) * vehicle.vehicle.consumption_kwh_per_km
        self.assertGreaterEqual(
            plan["target_soc_pct"] / 100 * capacity, required - 0.001
        )
        self.assertGreater(plan["grid_energy_kwh"], plan["energy_required_kwh"])
        arrival = datetime.fromisoformat(plan["estimated_arrival_time"])
        self.assertGreater(arrival, self.manager._simulated_time)
        self.assertGreaterEqual(datetime.fromisoformat(plan["start_time"]), arrival)

    async def test_simulation_cannot_select_legacy_depot_charger(self):
        charger = await self.db.chargers.find_one({"charger_id": "SIM-CHARGER-FAST"})
        charger.pop("_id")
        charger.update(
            charger_id="LEGACY",
            depot_id="OTHER",
            price_per_kwh=0,
            lat=12.9716,
            lon=77.5946,
        )
        await self.db.chargers.insert_one(charger)
        self.assertNotEqual("LEGACY", (await self.plan())["charger_id"])

    async def test_simulation_clock_includes_io_time(self):
        loop = asyncio.get_running_loop()
        self.manager._last_tick_real = loop.time() - 2
        before = self.manager._simulated_time
        await self.manager._tick()
        elapsed = (self.manager._simulated_time - before).total_seconds()
        self.assertGreaterEqual(elapsed, 120)
        self.assertLess(elapsed, 125)
