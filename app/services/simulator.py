import asyncio
import logging
import math
import random
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.models.charger import Charger, ChargerStatus
from app.models.depot import Depot
from app.models.reservation import ACTIVE_RESERVATION_STATUSES, Reservation
from app.models.simulator import SimulatorStatus
from app.models.tariff import Tariff
from app.models.telemetry import OperatingState, TelemetryEvent
from app.models.trip import Trip, TripStatus
from app.models.vehicle import Vehicle
from app.services.coordination import serialized
from app.services.reservations import shift_window_to_now
from app.services.scheduler import haversine_km
from app.services.telemetry import store_telemetry


@dataclass
class VehicleSimulationState:
    vehicle: Vehicle
    soc_pct: float
    soh_pct: float
    lat: float
    lon: float
    odometer_km: float
    sequence: int
    battery_temperature_c: float
    route_remaining_km: float | None = None
    trip_id: str | None = None
    resume_pending: bool = False
    arrived: bool = False
    simulation_run_id: str | None = None


def advance_driving_state(
    state: VehicleSimulationState,
    elapsed_seconds: float,
    speed_kmh: float,
) -> float:
    distance_km = min(
        state.route_remaining_km or 0,
        speed_kmh * elapsed_seconds / 3600,
        state.vehicle.usable_capacity_kwh
        * state.soh_pct
        / 100
        * state.soc_pct
        / 100
        / state.vehicle.consumption_kwh_per_km,
    )
    energy_used_kwh = distance_km * state.vehicle.consumption_kwh_per_km
    effective_capacity = state.vehicle.usable_capacity_kwh * state.soh_pct / 100
    state.soc_pct = max(
        0, state.soc_pct - energy_used_kwh / max(effective_capacity, 1e-9) * 100
    )
    state.odometer_km += distance_km
    state.route_remaining_km = max(0, (state.route_remaining_km or 0) - distance_km)
    return distance_km


def advance_charging_state(
    state: VehicleSimulationState,
    elapsed_seconds: float,
    allocated_power_kw: float,
    target_soc_pct: float,
) -> float:
    from app.services.energy import charge_for_seconds

    state.soc_pct, delivered = charge_for_seconds(
        state.soc_pct,
        target_soc_pct,
        state.vehicle.usable_capacity_kwh * state.soh_pct / 100,
        allocated_power_kw,
        settings.charging_efficiency,
        elapsed_seconds,
    )
    return delivered


def demo_departure_time(seed_time: datetime, vehicle_index: int) -> datetime:
    return seed_time + timedelta(minutes=1 + vehicle_index % 6)


def point_at_distance(
    lat: float, lon: float, distance_km: float, bearing_degrees: float
) -> tuple[float, float]:
    radius_km = 6371.0
    angular_distance = distance_km / radius_km
    bearing = math.radians(bearing_degrees)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def advance_toward_location(
    state: VehicleSimulationState,
    destination_lat: float,
    destination_lon: float,
    elapsed_seconds: float,
    speed_kmh: float,
) -> tuple[float, float]:
    remaining_km = haversine_km(state.lat, state.lon, destination_lat, destination_lon)
    distance_km = min(
        remaining_km,
        speed_kmh * elapsed_seconds / 3600,
        state.vehicle.usable_capacity_kwh
        * state.soh_pct
        / 100
        * state.soc_pct
        / 100
        / state.vehicle.consumption_kwh_per_km,
    )
    if remaining_km > 0:
        fraction = distance_km / remaining_km
        state.lat += (destination_lat - state.lat) * fraction
        state.lon += (destination_lon - state.lon) * fraction
    energy_used_kwh = distance_km * state.vehicle.consumption_kwh_per_km
    effective_capacity = state.vehicle.usable_capacity_kwh * state.soh_pct / 100
    state.soc_pct = max(
        0, state.soc_pct - energy_used_kwh / max(effective_capacity, 1e-9) * 100
    )
    state.odometer_km += distance_km
    return distance_km, max(0, remaining_km - distance_km)


async def seed_scenario(request, db, redis, kafka):
    from app.models.charging import ChargingPlan, ChargingPlanStatus
    from app.models.reservation import ReservationStatus
    from app.services.energy import charging_seconds

    rng = random.Random(request.seed)
    now = datetime.now(UTC).replace(microsecond=0)
    scenario = request.scenario.value
    if scenario == "LOW_BATTERY_BEFORE_TRIP":
        scenario = "NONFINAL_RELAXED"
    run_id = str(uuid4())
    async for key in redis.scan_iter(match="vehicle:SIM*:latest"):
        await redis.delete(key)
    for collection in (
        "telemetry",
        "trips",
        "alerts",
        "charging_plans",
        "reservations",
        "vehicles",
        "manager_decisions",
    ):
        await db[collection].delete_many({"vin": {"$regex": "^SIM"}})
    await db.tariffs.delete_many({"tariff_id": {"$regex": "^SIM-"}})
    depot = Depot(
        depot_id="SIM-DEPOT-01",
        name="Central Depot",
        lat=12.9716,
        lon=77.5946,
        power_limit_kw=600,
    )
    await db.depots.update_one(
        {"depot_id": depot.depot_id},
        {"$set": depot.model_dump(mode="python")},
        upsert=True,
    )
    chargers = [
        Charger(
            charger_id="SIM-CHARGER-FAST",
            name="North Hub",
            depot_id=depot.depot_id,
            lat=13.055,
            lon=77.610,
            available_kw=60,
            price_per_kwh=11,
            connector_type="CCS2",
            port_count=2,
        ),
        Charger(
            charger_id="SIM-CHARGER-CHEAP",
            name="East Solar",
            depot_id=depot.depot_id,
            lat=12.960,
            lon=77.680,
            available_kw=45,
            price_per_kwh=6,
            connector_type="CCS2",
            port_count=2,
        ),
        Charger(
            charger_id="SIM-CHARGER-SLOW",
            name="West Park",
            depot_id=depot.depot_id,
            lat=12.910,
            lon=77.500,
            available_kw=30,
            price_per_kwh=8,
            connector_type="CCS2",
            port_count=2,
        ),
    ]
    if scenario == "CHARGER_FAILURE":
        chargers[1].status = ChargerStatus.FAULTY
    for c in chargers:
        await db.chargers.update_one(
            {"charger_id": c.charger_id},
            {"$set": c.model_dump(mode="python")},
            upsert=True,
        )
        # Station rates stay ordered through midnight so the scenario works at any current IST time.
        for suffix, start, end, price in [
            ("DAY", "06:00", "22:00", c.price_per_kwh),
            ("NIGHT", "22:00", "06:00", c.price_per_kwh - 0.5),
        ]:
            t = Tariff(
                tariff_id=f"SIM-{c.charger_id}-{suffix}",
                depot_id=depot.depot_id,
                charger_id=c.charger_id,
                start_time=start,
                end_time=end,
                price_per_kwh=price,
            )
            await db.tariffs.insert_one(t.model_dump(mode="python"))
    await db.simulation.update_one(
        {"simulation_id": "active"},
        {
            "$set": {
                "seed": request.seed,
                "scenario": scenario,
                "created_at": now,
                "run_id": run_id,
            }
        },
        upsert=True,
    )
    records = []
    # Distinct customer districts remain in one city-sized coordinate frame.
    districts = [
        (13.065, 77.715),
        (13.075, 77.525),
        (12.895, 77.545),
        (12.915, 77.710),
        (13.040, 77.650),
        (depot.lat, depot.lon),
    ]
    count = (
        max(10, request.vehicle_count)
        if scenario == "CHARGER_CONGESTION"
        else request.vehicle_count
    )
    for index in range(count):
        focus = index == 0
        vin = f"SIM{index + 1:014d}"
        vehicle = Vehicle(
            vin=vin,
            name=f"Van {index + 1:03d}",
            depot_id=depot.depot_id,
            battery_capacity_kwh=35 if focus else 75,
            usable_capacity_kwh=30 if focus else 70,
            consumption_kwh_per_km=0.30 if focus else 0.21,
            max_charge_power_kw=60,
            connector_type="CCS2",
        )
        start = (
            (12.974, 77.611)
            if focus
            else point_at_distance(
                depot.lat, depot.lon, 3 + (index % 6) * 1.6, (index * 137) % 360
            )
        )
        soc = 14.0 if focus else rng.uniform(75, 95)
        if scenario == "NORMAL_DAY":
            soc = 100 if focus else soc
        if focus and scenario == "UNREACHABLE_CHARGER":
            soc = 0
        first = (
            districts[0]
            if focus
            else point_at_distance(
                depot.lat, depot.lon, 5 + (index * 3) % 11, (index * 53) % 360
            )
        )
        direct = haversine_km(*start, *first)
        if focus and scenario in {"NONFINAL_PRIORITY", "FINAL_PRIORITY"}:
            soc = (
                (direct + 1)
                * vehicle.consumption_kwh_per_km
                / (vehicle.usable_capacity_kwh * 0.96)
                * 100
            )
        first_due = 180
        if focus and scenario in {"NONFINAL_TIGHT", "FINAL_TIGHT"}:
            first_due = 80
        if focus and scenario == "CHARGER_CONGESTION":
            first_due = 95
        if focus and scenario == "CHARGER_FAILURE":
            first_due = 100
        if focus and scenario in {"NONFINAL_PRIORITY", "FINAL_PRIORITY"}:
            first_due = direct / 35 * 60 + 25
        if focus and scenario == "NONFINAL_CONFLICT":
            first_due = direct / 35 * 60 + 5
        length = 1 if focus and scenario.startswith("FINAL_") else 6
        if scenario == "NORMAL_DAY":
            length = 4
        trips = []
        origin = start
        origin_name = "City dispatch point"
        for i in range(length):
            dest = (
                first
                if i == 0
                else (
                    districts[i]
                    if focus
                    else point_at_distance(
                        depot.lat,
                        depot.lon,
                        5 + (index * 3 + i * 7) % 11,
                        (index * 53 + i * 97) % 360,
                    )
                )
            )
            if i == length - 1 and length > 1:
                dest = (depot.lat, depot.lon)
            if dest == origin:
                dest = (dest[0] + 0.003, dest[1] + 0.003)
            name = (
                "Central Depot"
                if i == length - 1 and length > 1
                else f"Customer {index + 1:03d}-{i + 1}"
            )
            # Background schedules are generous; focus later deadlines remain finite and connected.
            due = first_due + i * 100 if focus else 220 + i * 100
            if (
                focus
                and scenario in {"NONFINAL_PRIORITY", "NONFINAL_CONFLICT"}
                and i > 0
            ):
                due = 240 + i * 90
            trip = Trip(
                trip_id=f"SIM-TRIP-{index + 1:04d}" + (f"-LEG-{i + 1}" if i else ""),
                vin=vin,
                origin=origin_name,
                destination=name,
                origin_lat=origin[0],
                origin_lon=origin[1],
                destination_lat=dest[0],
                destination_lon=dest[1],
                departure_time=now + timedelta(minutes=0 if i == 0 else i * 35),
                delivery_deadline=now + timedelta(minutes=due),
                distance_km=haversine_km(*origin, *dest),
                service_duration_minutes=4,
                sequence=i + 1,
                simulation_enabled=True,
            )
            trips.append(trip)
            origin = dest
            origin_name = name
        event = TelemetryEvent(
            vin=vin,
            ts=now,
            lat=start[0],
            lon=start[1],
            speed_kmh=0,
            soc_pct=round(soc, 4),
            soh_pct=96,
            odo_km=10000 + index * 123,
            seq=0,
            battery_temperature_c=31,
            operating_state=OperatingState.PARKED,
            trip_id=trips[0].trip_id,
            route_remaining_km=trips[0].distance_km,
            simulation_run_id=run_id,
            navigation_phase="DELIVERY",
            navigation_target=first and trips[0].destination,
            destination_lat=first[0],
            destination_lon=first[1],
            distance_to_destination_km=direct,
            eta_minutes=direct / 35 * 60,
        )
        records.append((vehicle, trips, event))
    if scenario in {"NONFINAL_PRIORITY", "FINAL_PRIORITY"} and len(records) > 1:
        # Standby van can physically collect the remaining packages at Customer 001-1.
        v, ts, e = records[-1]
        ts.clear()
        e.trip_id = None
        e.route_remaining_km = None
        e.lat, e.lon = districts[0][0] + 0.010, districts[0][1] - 0.010
        e.navigation_target = "Standby"
        e.soc_pct = 95

    async def price_existing_session(plan, charger):
        from app.services.pricing import charging_cost

        tariff_rows = [
            Tariff(**d)
            async for d in db.tariffs.find({"charger_id": charger.charger_id})
        ]
        plan.grid_energy_kwh = round(
            plan.energy_required_kwh / settings.charging_efficiency, 4
        )
        plan.estimated_cost = charging_cost(
            plan.start_time,
            plan.end_time,
            plan.allocated_power_kw,
            tariff_rows,
            charger.price_per_kwh,
            settings.charging_efficiency,
            plan.grid_energy_kwh,
        )
        plan.average_price_per_kwh = round(
            plan.estimated_cost / plan.grid_energy_kwh, 3
        )

    if scenario == "CHARGER_CONGESTION":
        # Six real occupied ports, plus three actual waiting vehicles. Focus van remains unapproved.
        sessions = [
            (1, 1, 1, 0, 15, 95),
            (2, 1, 2, 0, 18, 95),
            (3, 0, 1, 0, 85, 94),
            (4, 0, 2, 0, 85, 96),
            (5, 2, 1, 0, 20, 90),
            (6, 2, 2, 0, 25, 92),
        ]
        ends = {}
        for idx, ci, port, _, start_soc, target in sessions:
            v, ts, e = records[idx]
            c = chargers[ci]
            e.lat, e.lon = c.lat, c.lon
            e.soc_pct = start_soc
            duration = charging_seconds(
                start_soc,
                target,
                v.usable_capacity_kwh * 0.96,
                min(v.max_charge_power_kw, c.available_kw),
                settings.charging_efficiency,
            )
            end = now + timedelta(seconds=duration + 120)
            ends[(ci, port)] = end
            ts[0].origin_lat, ts[0].origin_lon = c.lat, c.lon
            ts[0].distance_km = haversine_km(
                c.lat, c.lon, ts[0].destination_lat, ts[0].destination_lon
            )
            ts[0].delivery_deadline = now + timedelta(hours=5)
            plan = ChargingPlan(
                plan_id=f"SIM-SESSION-{idx}",
                vin=v.vin,
                trip_id=ts[0].trip_id,
                charger_id=c.charger_id,
                port_number=port,
                start_time=now,
                end_time=end,
                starting_soc_pct=start_soc,
                target_soc_pct=target,
                energy_required_kwh=(target - start_soc)
                * v.usable_capacity_kwh
                * 0.96
                / 100,
                allocated_power_kw=min(v.max_charge_power_kw, c.available_kw),
                estimated_cost=0,
                predicted_ready_time=end,
                status=ChargingPlanStatus.CHARGING,
                reason="Existing charging session",
                simulation_run_id=run_id,
            )
            await price_existing_session(plan, c)
            await db.charging_plans.insert_one(plan.model_dump(mode="python"))
            r = Reservation(
                plan_id=plan.plan_id,
                vin=v.vin,
                charger_id=c.charger_id,
                port_number=port,
                start_time=now,
                end_time=end,
                reserved_power_kw=plan.allocated_power_kw,
                status=ReservationStatus.OCCUPIED,
            )
            await db.reservations.insert_one(r.model_dump(mode="python"))
            e.operating_state = OperatingState.CHARGING
            e.charger_id = c.charger_id
            e.port_number = port
            e.plan_id = plan.plan_id
            e.is_plugged_in = True
            e.navigation_phase = "CHARGING"
            e.navigation_target = c.name
            e.destination_lat = c.lat
            e.destination_lon = c.lon
            e.distance_to_destination_km = 0
        for idx, ci, port in [(7, 1, 1), (8, 1, 2), (9, 2, 1)]:
            v, ts, e = records[idx]
            c = chargers[ci]
            start = ends[(ci, port)]
            e.lat, e.lon = c.lat, c.lon
            e.soc_pct = 25
            duration = charging_seconds(
                25,
                90,
                v.usable_capacity_kwh * 0.96,
                min(v.max_charge_power_kw, c.available_kw),
                settings.charging_efficiency,
            )
            end = start + timedelta(seconds=duration + 120)
            plan = ChargingPlan(
                plan_id=f"SIM-QUEUE-{idx}",
                vin=v.vin,
                trip_id=ts[0].trip_id,
                charger_id=c.charger_id,
                port_number=port,
                start_time=start,
                end_time=end,
                starting_soc_pct=25,
                target_soc_pct=90,
                energy_required_kwh=0.65 * v.usable_capacity_kwh * 0.96,
                allocated_power_kw=min(v.max_charge_power_kw, c.available_kw),
                estimated_cost=0,
                predicted_ready_time=end,
                status=ChargingPlanStatus.APPROVED,
                reason="Booked charging session",
                simulation_run_id=run_id,
            )
            await price_existing_session(plan, c)
            await db.charging_plans.insert_one(plan.model_dump(mode="python"))
            await db.reservations.insert_one(
                Reservation(
                    plan_id=plan.plan_id,
                    vin=v.vin,
                    charger_id=c.charger_id,
                    port_number=port,
                    start_time=start,
                    end_time=end,
                    reserved_power_kw=plan.allocated_power_kw,
                ).model_dump(mode="python")
            )
            e.operating_state = OperatingState.WAITING_FOR_CHARGER
            e.charger_id = c.charger_id
            e.port_number = port
            e.plan_id = plan.plan_id
            e.navigation_phase = "AT_CHARGER"
            e.navigation_target = c.name
            e.destination_lat = c.lat
            e.destination_lon = c.lon
            e.distance_to_destination_km = 0
            ts[0].origin_lat, ts[0].origin_lon = c.lat, c.lon
            ts[0].distance_km = haversine_km(
                c.lat, c.lon, ts[0].destination_lat, ts[0].destination_lon
            )
    seed_limit = asyncio.Semaphore(20)

    async def persist_record(v, ts, e):
        async with seed_limit:
            if ts:
                e.route_remaining_km = haversine_km(
                    e.lat, e.lon, ts[0].destination_lat, ts[0].destination_lon
                )
            e.remaining_range_km = (
                v.usable_capacity_kwh
                * (e.soh_pct or 100)
                / 100
                * e.soc_pct
                / 100
                / v.consumption_kwh_per_km
            )
            await db.vehicles.insert_one(v.model_dump(mode="python"))
            if ts:
                await db.trips.insert_many([t.model_dump(mode="python") for t in ts])
            await store_telemetry(e, db, redis, kafka)

    results = await asyncio.gather(
        *(persist_record(*record) for record in records), return_exceptions=True
    )
    for result in results:
        if isinstance(result, Exception):
            raise result
    return {
        "scenario": scenario,
        "vehicles_seeded": len(records),
        "primary_demo_vin": records[0][0].vin,
        "simulated_start_time": now.isoformat(),
    }


class SimulatorManager:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._states: dict[str, VehicleSimulationState] = {}
        self._simulated_time: datetime | None = None
        self._emitted_events = 0
        self._tick_seconds = 1.0
        self._time_scale = 60.0
        self._db: Any = None
        self._redis: Any = None
        self._kafka: Any = None
        self._rng = random.Random(42)
        self._error: str | None = None
        self._scenario: str | None = None
        self._charger_locks: dict[str, asyncio.Lock] = {}

    @serialized
    async def start(
        self,
        db: Any,
        redis: Any,
        kafka: Any,
        tick_seconds: float,
        time_scale: float,
    ) -> SimulatorStatus:
        if self._task and not self._task.done():
            return self.status()
        self._db, self._redis, self._kafka = db, redis, kafka
        self._tick_seconds = tick_seconds
        self._time_scale = time_scale
        config = await db.simulation.find_one({"simulation_id": "active"}) or {}
        self._rng = random.Random(int(config.get("seed", 42)))
        self._scenario = config.get("scenario")
        latest_telemetry_time = await self._load_states()
        if self._simulated_time is None:
            self._simulated_time = latest_telemetry_time or datetime.now(UTC)
        if not self._states:
            from fastapi import HTTPException

            raise HTTPException(422, "Seed a scenario before starting the simulator")
        self._error = None
        self._task = asyncio.create_task(self._run(), name="ev-simulator")
        return self.status()

    def reset(self) -> None:
        self._states.clear()
        self._error = None
        self._simulated_time = None
        self._emitted_events = 0

    @serialized
    async def stop(self) -> SimulatorStatus:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        return self.status()

    def status(self) -> SimulatorStatus:
        return SimulatorStatus(
            running=self._task is not None and not self._task.done(),
            state="FAILED"
            if self._error
            else "RUNNING"
            if self._task and not self._task.done()
            else "STOPPED",
            error=self._error,
            time_scale=self._time_scale,
            tick_seconds=self._tick_seconds,
            simulated_time=self._simulated_time.isoformat()
            if self._simulated_time
            else None,
            tracked_vehicles=len(self._states),
            emitted_events=self._emitted_events,
        )

    async def _load_states(self) -> datetime | None:
        self._states.clear()
        latest_telemetry_time: datetime | None = None
        async for vehicle_doc in self._db.vehicles.find(
            {"vin": {"$regex": "^SIM"}, "active": True}
        ):
            vehicle_doc.pop("_id", None)
            vehicle = Vehicle(**vehicle_doc)
            telemetry_doc = await self._db.telemetry.find_one(
                {"vin": vehicle.vin}, sort=[("ts", -1), ("seq", -1)]
            )
            if telemetry_doc is None:
                continue
            telemetry_time = telemetry_doc.get("ts")
            if telemetry_time is not None and (
                latest_telemetry_time is None or telemetry_time > latest_telemetry_time
            ):
                latest_telemetry_time = telemetry_time
            self._states[vehicle.vin] = VehicleSimulationState(
                vehicle=vehicle,
                soc_pct=float(telemetry_doc["soc_pct"]),
                soh_pct=float(telemetry_doc.get("soh_pct") or 100),
                lat=float(telemetry_doc["lat"]),
                lon=float(telemetry_doc["lon"]),
                odometer_km=float(telemetry_doc["odo_km"]),
                sequence=int(telemetry_doc.get("seq", 0)),
                battery_temperature_c=float(
                    telemetry_doc.get("battery_temperature_c") or 31
                ),
                route_remaining_km=telemetry_doc.get("route_remaining_km"),
                trip_id=telemetry_doc.get("trip_id"),
                resume_pending=telemetry_doc.get("operating_state")
                in {"READY", "RESUMING_TRIP"},
                arrived=telemetry_doc.get("operating_state") == "AT_CUSTOMER",
                simulation_run_id=telemetry_doc.get("simulation_run_id"),
            )
        return latest_telemetry_time

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            while True:
                await self._tick()
                deadline = max(deadline + self._tick_seconds, loop.time())
                await asyncio.sleep(max(0, deadline - loop.time()))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - Task boundary: make failure observable without secrets.
            self._error = f"Simulation failed ({type(exc).__name__}). Check dependency health and restart."
            logging.getLogger(__name__).error(
                "Simulator stopped: %s", type(exc).__name__
            )

    @serialized
    async def _tick(self) -> None:
        assert self._simulated_time is not None
        # Database latency must not skip arrivals, bookings or service windows.
        # Speed controls the requested logical step, capped at one simulated minute.
        elapsed_simulated_seconds = min(self._tick_seconds * self._time_scale, 60.0)
        self._simulated_time += timedelta(seconds=elapsed_simulated_seconds)
        results = await asyncio.gather(
            *(
                self._advance_vehicle(state, elapsed_simulated_seconds)
                for state in self._states.values()
            ),
            return_exceptions=True,
        )
        # Drain siblings before reporting a failure; stop must freeze ALL telemetry.
        for result in results:
            if isinstance(result, Exception):
                raise result

    async def _advance_vehicle(
        self, state: VehicleSimulationState, elapsed_seconds: float
    ) -> None:
        # Serialize physical occupancy decisions within each depot. Vehicles still
        # move concurrently unless they have a charging plan in the same depot.
        plan = await self._db.charging_plans.find_one(
            {
                "vin": state.vehicle.vin,
                "status": {"$in": ["APPROVED", "CHARGING", "SCHEDULED"]},
            }
        )
        if plan:
            charger = await self._db.chargers.find_one(
                {"charger_id": plan["charger_id"]}
            )
            key = (
                charger.get("depot_id", plan["charger_id"])
                if charger
                else plan["charger_id"]
            )
            lock = self._charger_locks.setdefault(key, asyncio.Lock())
            async with lock:
                await self._advance_vehicle_unlocked(state, elapsed_seconds)
        else:
            await self._advance_vehicle_unlocked(state, elapsed_seconds)

    async def _advance_vehicle_unlocked(
        self, state: VehicleSimulationState, elapsed_seconds: float
    ) -> None:
        assert self._simulated_time is not None
        now = self._simulated_time
        plan = await self._db.charging_plans.find_one(
            {"vin": state.vehicle.vin, "status": {"$in": ["APPROVED", "CHARGING"]}},
            sort=[("created_at", -1)],
        )

        operating_state = (
            OperatingState.AT_CUSTOMER if state.arrived else OperatingState.PARKED
        )
        speed_kmh = 0.0
        power_kw = 0.0
        charger_id = None
        is_plugged_in = False
        navigation_phase = "IDLE"
        navigation_target = None
        destination_lat = None
        destination_lon = None
        distance_to_destination_km = None
        eta_minutes = None

        if not plan:
            next_trip = await self._db.trips.find_one(
                {
                    "vin": state.vehicle.vin,
                    "status": {"$in": ["IN_PROGRESS", "PLANNED"]},
                    "simulation_enabled": {"$ne": False},
                },
                sort=[("departure_time", 1)],
            )
            if next_trip:
                last = await self._db.trips.find_one(
                    {"vin": state.vehicle.vin, "status": "COMPLETED"},
                    sort=[("completed_at", -1)],
                )
                service_done = (
                    not last
                    or not last.get("service_until")
                    or last["service_until"] <= now
                )
                future_plan = await self._db.charging_plans.find_one(
                    {
                        "vin": state.vehicle.vin,
                        "trip_id": next_trip["trip_id"],
                        "status": "SCHEDULED",
                    }
                )
                if future_plan and service_done:
                    await self._db.charging_plans.update_one(
                        {"plan_id": future_plan["plan_id"]},
                        {"$set": {"status": "APPROVED", "active": True}},
                    )
                    plan = {**future_plan, "status": "APPROVED", "active": True}
        charger = None
        if plan:
            charger_id = plan["charger_id"]
            charger = await self._db.chargers.find_one({"charger_id": charger_id})

        if plan and charger:
            own_reservation = await self._db.reservations.find_one(
                {
                    "plan_id": plan["plan_id"],
                    "charger_id": charger_id,
                    "port_number": plan["port_number"],
                    "status": {
                        "$in": [item.value for item in ACTIVE_RESERVATION_STATUSES]
                    },
                }
            )
            if charger.get("status") in {"FAULTY", "OFFLINE"} or not own_reservation:
                await self._db.charging_plans.update_one(
                    {"plan_id": plan["plan_id"]},
                    {"$set": {"status": "CANCELLED", "active": False}},
                )
                await self._db.reservations.update_many(
                    {"plan_id": plan["plan_id"]}, {"$set": {"status": "CANCELLED"}}
                )
                plan = None

        if plan and charger:
            destination_lat = float(charger["lat"])
            destination_lon = float(charger["lon"])
            navigation_target = charger.get("name", charger_id)
            distance_to_destination_km = haversine_km(
                state.lat, state.lon, destination_lat, destination_lon
            )

            if distance_to_destination_km > 0.000001:
                navigation_phase = "TO_CHARGER"
                eta_minutes = distance_to_destination_km / 35 * 60
                if state.soc_pct <= 0:
                    operating_state = OperatingState.STRANDED
                else:
                    operating_state = OperatingState.EN_ROUTE_TO_CHARGER
                    speed_kmh = 35.0
                    distance, distance_to_destination_km = advance_toward_location(
                        state,
                        destination_lat,
                        destination_lon,
                        elapsed_seconds,
                        speed_kmh,
                    )
                    power_kw = -(distance * state.vehicle.consumption_kwh_per_km) / (
                        elapsed_seconds / 3600
                    )
                    eta_minutes = distance_to_destination_km / speed_kmh * 60
                    if distance_to_destination_km <= 0.000001:
                        # Arrival and plugging in are separate observable transitions.
                        operating_state = OperatingState.WAITING_FOR_CHARGER
                        navigation_phase = "AT_CHARGER"
                        speed_kmh = 0.0
                        eta_minutes = max(
                            0, (plan["start_time"] - now).total_seconds() / 60
                        )
            else:
                state.lat, state.lon = destination_lat, destination_lon
                if now > plan["end_time"] and plan["status"] != "CHARGING":
                    start_time, end_time = shift_window_to_now(
                        plan["start_time"], plan["end_time"], now
                    )
                    plan["start_time"] = start_time
                    plan["end_time"] = end_time
                    plan["predicted_ready_time"] = end_time
                    updates = {
                        "start_time": start_time,
                        "end_time": end_time,
                        "updated_at": datetime.now(UTC),
                    }
                    await self._db.charging_plans.update_one(
                        {"plan_id": plan["plan_id"]},
                        {"$set": {**updates, "predicted_ready_time": end_time}},
                    )
                    await self._db.reservations.update_one(
                        {"plan_id": plan["plan_id"]}, {"$set": updates}
                    )

                blocking_reservation = await self._db.reservations.find_one(
                    {
                        "charger_id": charger_id,
                        "port_number": plan["port_number"],
                        "plan_id": {"$ne": plan["plan_id"]},
                        "status": {
                            "$in": [item.value for item in ACTIVE_RESERVATION_STATUSES]
                        },
                        "$or": [
                            {"status": "OCCUPIED"},
                            {"start_time": {"$lte": now}, "end_time": {"$gt": now}},
                        ],
                    }
                )
                if (
                    now < plan["start_time"]
                    or blocking_reservation
                    or state.battery_temperature_c >= 45
                ):
                    operating_state = OperatingState.WAITING_FOR_CHARGER
                    navigation_phase = "AT_CHARGER"
                    eta_minutes = max(
                        0, (plan["start_time"] - now).total_seconds() / 60
                    )
                    distance_to_destination_km = 0.0
                else:
                    operating_state = OperatingState.CHARGING
                    navigation_phase = "CHARGING"
                    distance_to_destination_km = 0.0
                    eta_minutes = 0.0
                    power_kw = float(plan["allocated_power_kw"])
                    is_plugged_in = True
                    if plan.get("status") != "CHARGING":
                        lifecycle_updated_at = datetime.now(UTC)
                        await self._db.charging_plans.update_one(
                            {"plan_id": plan["plan_id"]},
                            {
                                "$set": {
                                    "status": "CHARGING",
                                    "updated_at": lifecycle_updated_at,
                                }
                            },
                        )
                        await self._db.reservations.update_one(
                            {"plan_id": plan["plan_id"]},
                            {
                                "$set": {
                                    "status": "OCCUPIED",
                                    "updated_at": lifecycle_updated_at,
                                }
                            },
                        )
                        plan["status"] = "CHARGING"
                    advance_charging_state(
                        state,
                        min(
                            elapsed_seconds,
                            max(0, (now - plan["start_time"]).total_seconds()),
                        ),
                        power_kw,
                        float(plan["target_soc_pct"]),
                    )
                    state.battery_temperature_c = min(
                        44, state.battery_temperature_c + 0.03
                    )
                    if state.soc_pct >= float(plan["target_soc_pct"]):
                        operating_state = OperatingState.READY
                        navigation_phase = "READY"
                        state.resume_pending = True
                        is_plugged_in = False
                        power_kw = 0.0
                        completed_at = datetime.now(UTC)
                        await self._db.charging_plans.update_one(
                            {"plan_id": plan["plan_id"]},
                            {
                                "$set": {
                                    "status": "COMPLETED",
                                    "active": False,
                                    "completed_at": completed_at,
                                    "updated_at": completed_at,
                                }
                            },
                        )
                        await self._db.reservations.update_one(
                            {"plan_id": plan["plan_id"]},
                            {
                                "$set": {
                                    "status": "COMPLETED",
                                    "completed_at": completed_at,
                                    "updated_at": completed_at,
                                }
                            },
                        )
        else:
            trip_doc = await self._db.trips.find_one(
                {
                    "vin": state.vehicle.vin,
                    "status": {
                        "$in": [TripStatus.IN_PROGRESS.value, TripStatus.PLANNED.value]
                    },
                    "departure_time": {"$lte": now},
                    "simulation_enabled": {"$ne": False},
                },
                sort=[("departure_time", 1)],
            )
            last_trip = await self._db.trips.find_one(
                {"vin": state.vehicle.vin, "status": "COMPLETED"},
                sort=[("completed_at", -1)],
            )
            service_pending = (
                last_trip
                and last_trip.get("service_until")
                and last_trip["service_until"] > now
            )
            recovery = last_trip and last_trip.get("recovery_requested")
            hold = False
            if (
                trip_doc
                and trip_doc["status"] == "PLANNED"
                and not trip_doc.get("reserve_exception")
            ):
                from app.services.scheduler import escape_km

                t = Trip(**trip_doc)
                future = [
                    Trip(**d)
                    async for d in self._db.trips.find(
                        {
                            "vin": state.vehicle.vin,
                            "status": "PLANNED",
                            "trip_id": {"$ne": t.trip_id},
                            "simulation_enabled": {"$ne": False},
                        }
                    ).sort("departure_time", 1)
                ]
                cs = [
                    Charger(**d)
                    async for d in self._db.chargers.find(
                        {"depot_id": state.vehicle.depot_id}
                    )
                ]
                required = (
                    haversine_km(
                        state.lat, state.lon, t.destination_lat, t.destination_lon
                    )
                    if t.destination_lat is not None
                    else t.distance_km
                )
                required += settings.reserve_range_km + escape_km(
                    state.vehicle, t, future, cs
                )
                available = (
                    state.vehicle.usable_capacity_kwh
                    * state.soh_pct
                    / 100
                    * state.soc_pct
                    / 100
                    / state.vehicle.consumption_kwh_per_km
                )
                # Evaluate before departure; do not stop a previously approved safe journey on rounding noise.
                hold = t.status == TripStatus.PLANNED and available + 0.05 < required
            if recovery:
                operating_state = OperatingState.RECOVERY_REQUIRED
                navigation_phase = "RECOVERY_REQUIRED"
            elif service_pending:
                operating_state = OperatingState.AT_CUSTOMER
                navigation_phase = "SERVICE"
            elif (
                trip_doc
                and trip_doc.get("handover_trip_id")
                and not await self._db.trips.find_one(
                    {"trip_id": trip_doc["handover_trip_id"], "status": "COMPLETED"}
                )
            ):
                operating_state = OperatingState.PARKED
                navigation_phase = "WAITING_FOR_HANDOVER"
            elif state.soc_pct <= 1e-8:
                operating_state = OperatingState.STRANDED
                navigation_phase = "STRANDED"
            elif hold and state.battery_temperature_c < 45:
                operating_state = OperatingState.AWAITING_DECISION
                navigation_phase = "AWAITING_DECISION"
            elif trip_doc and state.battery_temperature_c >= 45:
                operating_state = OperatingState.HEALTH_HOLD
                navigation_phase = "HEALTH_HOLD"
            elif trip_doc:
                trip = Trip(
                    **{key: value for key, value in trip_doc.items() if key != "_id"}
                )
                if trip.status == TripStatus.PLANNED:
                    await self._db.trips.update_one(
                        {"trip_id": trip.trip_id},
                        {"$set": {"status": TripStatus.IN_PROGRESS.value}},
                    )
                    state.trip_id = trip.trip_id
                    state.arrived = False
                    state.route_remaining_km = trip.distance_km
                navigation_phase = (
                    "RESUMING_DELIVERY" if state.resume_pending else "DELIVERY"
                )
                navigation_target = trip.destination
                destination_lat = trip.destination_lat
                destination_lon = trip.destination_lon
                if state.soc_pct <= 0:
                    operating_state = OperatingState.STRANDED
                    distance = 0.0
                else:
                    operating_state = (
                        OperatingState.RESUMING_TRIP
                        if state.resume_pending
                        else OperatingState.DRIVING
                    )
                    speed_kmh = 35.0
                    if destination_lat is not None and destination_lon is not None:
                        distance, distance_to_destination_km = advance_toward_location(
                            state,
                            destination_lat,
                            destination_lon,
                            elapsed_seconds,
                            speed_kmh,
                        )
                        state.route_remaining_km = distance_to_destination_km
                    else:
                        distance = advance_driving_state(
                            state, elapsed_seconds, speed_kmh
                        )
                        distance_to_destination_km = state.route_remaining_km
                    power_kw = -(distance * state.vehicle.consumption_kwh_per_km) / (
                        elapsed_seconds / 3600
                    )
                    eta_minutes = (
                        distance_to_destination_km / speed_kmh * 60
                        if distance_to_destination_km is not None
                        else None
                    )
                    state.battery_temperature_c = min(
                        max(43, state.battery_temperature_c),
                        state.battery_temperature_c + 0.01,
                    )
                if (
                    state.route_remaining_km is not None
                    and state.route_remaining_km <= 0.000001
                ):
                    await self._db.trips.update_one(
                        {"trip_id": trip.trip_id},
                        {
                            "$set": {
                                "status": TripStatus.COMPLETED.value,
                                "completed_at": now,
                                "service_until": now
                                + timedelta(minutes=trip.service_duration_minutes),
                            }
                        },
                    )
                    state.trip_id = None
                    state.route_remaining_km = 0
                    state.resume_pending = False
                    state.arrived = True
                    speed_kmh = 0.0
                    operating_state = OperatingState.AT_CUSTOMER
                    navigation_phase = "ARRIVED"
                    distance_to_destination_km = 0.0
                    eta_minutes = 0.0
            else:
                state.battery_temperature_c = max(
                    28, state.battery_temperature_c - 0.02
                )

        if state.soc_pct <= 1e-8 and operating_state in {
            OperatingState.DRIVING,
            OperatingState.RESUMING_TRIP,
            OperatingState.EN_ROUTE_TO_CHARGER,
        }:
            operating_state = OperatingState.STRANDED
            speed_kmh = 0.0
        # A diversion changes the straight-line distance back to the customer.
        # Keep readiness and displayed delivery distance on the same geometry.
        if plan and state.trip_id:
            delivery = await self._db.trips.find_one({"trip_id": state.trip_id})
            if (
                delivery
                and delivery.get("destination_lat") is not None
                and delivery.get("destination_lon") is not None
            ):
                state.route_remaining_km = haversine_km(
                    state.lat,
                    state.lon,
                    delivery["destination_lat"],
                    delivery["destination_lon"],
                )
        state.sequence += 1
        effective_capacity = state.vehicle.usable_capacity_kwh * state.soh_pct / 100
        remaining_range = (
            effective_capacity
            * state.soc_pct
            / 100
            / state.vehicle.consumption_kwh_per_km
        )
        event = TelemetryEvent(
            vin=state.vehicle.vin,
            ts=now,
            lat=state.lat,
            lon=state.lon,
            speed_kmh=round(speed_kmh, 2),
            soc_pct=round(state.soc_pct, 3),
            soh_pct=state.soh_pct,
            odo_km=round(state.odometer_km, 3),
            seq=state.sequence,
            battery_temperature_c=round(state.battery_temperature_c, 2),
            power_kw=round(power_kw, 3),
            operating_state=operating_state,
            trip_id=state.trip_id,
            route_remaining_km=state.route_remaining_km,
            remaining_range_km=round(remaining_range, 2),
            charger_id=charger_id,
            is_plugged_in=is_plugged_in,
            simulation_run_id=state.simulation_run_id,
            plan_id=plan["plan_id"] if plan else None,
            port_number=plan["port_number"] if plan else None,
            navigation_phase=navigation_phase,
            navigation_target=navigation_target,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
            distance_to_destination_km=round(distance_to_destination_km, 2)
            if distance_to_destination_km is not None
            else None,
            eta_minutes=round(eta_minutes, 1) if eta_minutes is not None else None,
        )
        await store_telemetry(event, self._db, self._redis, self._kafka)
        self._emitted_events += 1
