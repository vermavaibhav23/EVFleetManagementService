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

    async def test_slow_publication_does_not_serialize_depot_telemetry(self):
        await self.seed(SimulationScenario.CHARGER_CONGESTION)
        planned = {
            p["vin"]
            async for p in self.db.charging_plans.find(
                {"status": {"$in": ["APPROVED", "CHARGING"]}}
            )
        }
        self.assertGreaterEqual(len(planned), 2)
        entered = set()
        overlap = asyncio.Event()
        release = asyncio.Event()
        publish = self.kafka.publish

        async def blocked_publication(topic, payload, key=None):
            if key in planned:
                entered.add(key)
                if len(entered) >= 2:
                    overlap.set()
                await release.wait()
            await publish(topic, payload, key=key)

        with patch.object(self.kafka, "publish", side_effect=blocked_publication):
            tick = asyncio.create_task(self.manager._tick())
            pause = None
            try:
                # Two planned cars in the same depot reach publication together.
                # A blocked broker must not keep the physical depot lock occupied.
                await asyncio.wait_for(overlap.wait(), 5)
                pause = asyncio.create_task(self.manager.stop())
                await asyncio.sleep(0)
                self.assertFalse(pause.done())
            finally:
                release.set()
                await tick
                if pause:
                    await pause
        self.assertEqual(len(self.manager._states), self.manager._emitted_events)
        for vin in planned:
            event = await self.latest(vin)
            self.assertEqual(event["seq"], self.manager._states[vin].sequence)
            self.assertTrue(event["published"])

    async def test_complete_journey_and_port_release(self):
        await self.manager._tick()
        self.assertEqual("AWAITING_DECISION", (await self.latest())["operating_state"])
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
                self.assertGreaterEqual(event["soc_pct"], previous["soc_pct"])
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
        self.assertEqual("DRIVING", (await self.latest())["operating_state"])
        self.assertEqual("SIM-TRIP-0001-LEG-2", (await self.latest())["trip_id"])

    async def test_arrival_reports_waiting_and_does_not_charge_before_slot(self):
        plan = await self.plan()
        await self.client.post(f"/charging/plans/{plan['plan_id']}/approve")
        plan = await self.db.charging_plans.find_one({"plan_id": plan["plan_id"]})
        charger = await self.db.chargers.find_one({"charger_id": plan["charger_id"]})
        state = self.manager._states[self.vin]
        state.lat, state.lon = charger["lat"] - 0.001, charger["lon"]
        self.manager._simulated_time = plan["start_time"] - timedelta(minutes=2)
        self.manager._time_scale = 60
        await self.manager._tick()
        arrived = await self.latest()
        self.assertEqual("WAITING_FOR_CHARGER", arrived["operating_state"])
        self.assertEqual(0, arrived["distance_to_destination_km"])
        self.assertFalse(arrived["is_plugged_in"])
        await self.manager._tick()
        at_start = await self.latest()
        self.assertEqual(arrived["soc_pct"], at_start["soc_pct"])
        await self.manager._tick()
        self.assertGreater((await self.latest())["soc_pct"], at_start["soc_pct"])

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
                update={"event_id": f"low-{i}", "seq": 1, "soc_pct": 8}
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
            all(r.status_code in {200, 409} for r in results), [r.text for r in results]
        )
        self.assertTrue(any(r.status_code == 200 for r in results))
        # A concurrent reservation invalidates the reviewed option; re-review explicitly.
        for proposed, result in zip(plans, results):
            if result.status_code == 409:
                old = proposed.json()
                await self.client.post(f"/charging/plans/{old['plan_id']}/reject")
                fresh = (await self.client.post(f"/charging/plans/{old['vin']}")).json()
                retried = await self.client.post(
                    f"/charging/plans/{fresh['plan_id']}/approve"
                )
                self.assertEqual(200, retried.status_code, retried.text)
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
        await asyncio.wait_for(asyncio.shield(self.manager._task), timeout=10)
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

    async def test_all_scenarios_use_healthy_chargers_or_explicit_decisions(self):
        no_plan = {
            "NORMAL_DAY",
            "NORMAL_LATER",
            "NONFINAL_CONTINUATION",
            "UNREACHABLE_CHARGER",
            "NONFINAL_PRIORITY",
            "NONFINAL_CONFLICT",
            "FINAL_PRIORITY",
        }
        for scenario in SimulationScenario:
            await self.seed(scenario)
            response = await self.client.post(f"/charging/plans/{self.vin}")
            self.assertEqual(
                422 if scenario.value in no_plan else 201,
                response.status_code,
                (scenario, response.text),
            )
            if response.status_code == 201:
                charger = await self.db.chargers.find_one(
                    {"charger_id": response.json()["charger_id"]}
                )
                self.assertEqual("AVAILABLE", charger["status"])

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
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual(
            0, await self.db.reservations.count_documents({"plan_id": plan["plan_id"]})
        )
        stored = await self.db.charging_plans.find_one({"plan_id": plan["plan_id"]})
        self.assertEqual(plan["charger_id"], stored["charger_id"])

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
        self.assertEqual(409, response.status_code)
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
            depot_id="SIM-DEPOT-01",
            price_per_kwh=0,
            lat=12.9716,
            lon=77.5946,
        )
        await self.db.chargers.insert_one(charger)
        self.assertNotEqual("LEGACY", (await self.plan())["charger_id"])

    async def test_simulation_clock_is_not_advanced_by_io_delay(self):
        loop = asyncio.get_running_loop()
        self.manager._last_tick_real = loop.time() - 2
        before = self.manager._simulated_time
        await self.manager._tick()
        elapsed = (self.manager._simulated_time - before).total_seconds()
        self.assertEqual(elapsed, 60)

    async def test_legacy_charger_without_port_count_keeps_dashboard_available(self):
        await self.db.chargers.insert_one(
            {
                "charger_id": "LEGACY",
                "name": "Legacy charger",
                "lat": 12.9716,
                "lon": 77.5946,
                "available_kw": 60,
                "price_per_kwh": 8,
                "status": "available",
            }
        )
        response = await self.client.get("/chargers")
        self.assertEqual(200, response.status_code, response.text)
        legacy = next(c for c in response.json() if c["charger_id"] == "LEGACY")
        self.assertEqual(1, legacy["port_count"])
        self.assertEqual(1, legacy["free_ports"])
        overview = await self.client.get("/fleet/overview")
        self.assertEqual(200, overview.status_code, overview.text)

    async def test_manager_projection_is_coherent_and_tariffs_match_scheduler(self):
        from app.models.tariff import Tariff
        from app.services.pricing import price_at, tariffs_for_charger

        response = await self.client.get("/fleet/manager")
        self.assertEqual(200, response.status_code, response.text)
        view = response.json()
        self.assertEqual(10, len(view["vehicles"]))
        self.assertFalse(view["simulator"]["running"])
        self.assertEqual("NEEDS_CHARGING", view["vehicles"][0]["manager_readiness"])
        self.assertEqual(3, len(view["chargers"]))
        tariffs = [Tariff(**t) async for t in self.db.tariffs.find({})]
        clock = datetime.fromisoformat(view["simulator"]["simulated_time"])
        for station in view["chargers"]:
            schedule = tariffs_for_charger(
                tariffs, station["charger_id"], station["depot_id"]
            )
            self.assertEqual(
                price_at(clock, schedule, station["price_per_kwh"]),
                station["current_price_per_kwh"],
            )
            self.assertEqual(24, len(station["hourly_prices"]))
        plan = await self.plan()
        options = plan["evaluated_options"]
        self.assertEqual(len(options), len({o["charger_id"] for o in options}))
        self.assertLessEqual(len(options), 3)
        self.assertEqual(plan["charger_id"], options[0]["charger_id"])
        self.assertEqual(plan["estimated_cost"], options[0]["electricity_cost"])
        for option in options:
            self.assertGreater(option["target_soc_pct"], 20)
            self.assertAlmostEqual(
                option["travel_distance_km"] / 35 * 60,
                option["travel_minutes"],
                delta=0.1,
            )

    async def test_unreachable_emergency_stays_stranded_and_has_no_plan(self):
        await self.seed(SimulationScenario.UNREACHABLE_CHARGER)
        first = await self.latest()
        view = (await self.client.get("/fleet/manager")).json()
        focus = view["vehicles"][0]
        self.assertEqual("EMERGENCY", focus["manager_readiness"])
        self.assertEqual([], focus["reachable_charger_ids"])
        self.assertIn("No roadside assistance", focus["explanation"])
        response = await self.client.post(f"/charging/plans/{self.vin}")
        self.assertEqual(422, response.status_code)
        for _ in range(3):
            await self.manager._tick()
        last = await self.latest()
        self.assertEqual("STRANDED", last["operating_state"])
        self.assertEqual((first["lat"], first["lon"]), (last["lat"], last["lon"]))

    async def test_queue_and_deadline_do_not_create_false_energy_emergency(self):
        await self.seed(SimulationScenario.CHARGER_CONGESTION)
        await self.db.trips.update_one(
            {"trip_id": "SIM-TRIP-0001"},
            {
                "$set": {
                    "delivery_deadline": self.manager._simulated_time
                    + timedelta(minutes=1)
                }
            },
        )
        view = (await self.client.get("/fleet/manager")).json()
        self.assertEqual("NEEDS_CHARGING", view["vehicles"][0]["manager_readiness"])
        self.assertTrue(view["vehicles"][0]["reachable_charger_ids"])
        self.assertLess(view["vehicles"][0]["deadline_margin_minutes"], 0)
        self.assertEqual(
            422, (await self.client.post(f"/charging/plans/{self.vin}")).status_code
        )

    async def test_daily_itinerary_connected_and_all_legs_enabled(self):
        trips = (await self.client.get(f"/trips/vehicle/{self.vin}")).json()
        self.assertEqual(6, len(trips))
        for a, b in zip(trips, trips[1:]):
            self.assertEqual(a["destination"], b["origin"])
            self.assertEqual(
                (a["destination_lat"], a["destination_lon"]),
                (b["origin_lat"], b["origin_lon"]),
            )
            self.assertLess(a["departure_time"], b["departure_time"])
        for trip in trips:
            self.assertAlmostEqual(
                trip["distance_km"],
                haversine_km(
                    trip["origin_lat"],
                    trip["origin_lon"],
                    trip["destination_lat"],
                    trip["destination_lon"],
                ),
                places=4,
            )
        self.assertTrue(all(t["simulation_enabled"] for t in trips))
        self.assertTrue(all(t["service_duration_minutes"] > 0 for t in trips))

    async def test_reset_preserves_non_demo_data_and_hides_legacy_resources(self):
        await self.db.vehicles.insert_one({"vin": "REAL00000000001", "active": True})
        await self.db.chargers.insert_one(
            {"charger_id": "REAL-CHARGER", "depot_id": "REAL-DEPOT"}
        )
        await self.db.trips.insert_one(
            {"vin": "REAL00000000001", "trip_id": "REAL-TRIP"}
        )
        old_run = (await self.client.get("/fleet/manager")).json()["run_id"]
        await self.client.post(
            "/simulator/scenarios", json={"scenario": "NORMAL_DAY", "vehicle_count": 10}
        )
        self.assertIsNotNone(
            await self.db.vehicles.find_one({"vin": "REAL00000000001"})
        )
        self.assertIsNotNone(await self.db.trips.find_one({"trip_id": "REAL-TRIP"}))
        view = (await self.client.get("/fleet/manager")).json()
        self.assertNotEqual(old_run, view["run_id"])
        self.assertEqual(10, len(view["vehicles"]))
        self.assertEqual(3, len(view["chargers"]))
        self.assertTrue(
            all(v["simulation_run_id"] == view["run_id"] for v in view["vehicles"])
        )

    async def test_health_hold_does_not_drive_or_offer_charging(self):
        before = TelemetryEvent(**await self.latest()).model_copy(
            update={"event_id": "hot-battery", "seq": 1, "battery_temperature_c": 48}
        )
        await store_telemetry(before, self.db, self.redis, self.kafka)
        await self.manager._load_states()
        before = await self.latest()
        await self.manager._tick()
        after = await self.latest()
        self.assertEqual("HEALTH_HOLD", after["operating_state"])
        self.assertEqual(before["soc_pct"], after["soc_pct"])
        view = (await self.client.get("/fleet/manager")).json()
        self.assertEqual("BLOCKED", view["vehicles"][0]["manager_readiness"])

    async def test_paused_start_uses_seed_clock_not_wall_clock(self):
        before = await self.latest()
        await self.manager.start(self.db, self.redis, self.kafka, 60, 1)
        self.assertEqual(before["ts"], self.manager._simulated_time)
        await self.manager.stop()

    async def test_100_vehicle_snapshot_shares_run_and_itinerary(self):
        await self.seed(SimulationScenario.NORMAL_DAY, count=100)
        view = (await self.client.get("/fleet/manager")).json()
        self.assertEqual(100, len(view["vehicles"]))
        self.assertTrue(all(len(v["itinerary"]) == 4 for v in view["vehicles"]))
        self.assertTrue(
            all(v["simulation_run_id"] == view["run_id"] for v in view["vehicles"])
        )
