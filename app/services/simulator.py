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
from app.models.simulator import ScenarioRequest, SimulationScenario, SimulatorStatus
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


async def seed_scenario(
    request: ScenarioRequest,
    db: Any,
    redis: Any,
    kafka: Any,
) -> dict[str, object]:
    rng = random.Random(request.seed)
    now = datetime.now(UTC).replace(
        hour=3, minute=30, second=0, microsecond=0
    )  # 09:00 IST demo day
    depot_id = "SIM-DEPOT-01"

    run_id = str(uuid4())
    async for key in redis.scan_iter(match="vehicle:SIM*:latest"):
        await redis.delete(key)
    simulation_filter = {"vin": {"$regex": "^SIM"}}
    await db.telemetry.delete_many(simulation_filter)
    await db.trips.delete_many(simulation_filter)
    await db.alerts.delete_many(simulation_filter)
    await db.charging_plans.delete_many(simulation_filter)
    await db.reservations.delete_many(simulation_filter)
    await db.vehicles.delete_many(simulation_filter)

    depot = Depot(
        depot_id=depot_id,
        name="Central Fleet Depot",
        lat=12.9716,
        lon=77.5946,
        power_limit_kw=180,
    )
    await db.depots.update_one(
        {"depot_id": depot.depot_id},
        {"$set": depot.model_dump(mode="python")},
        upsert=True,
    )

    chargers = [
        Charger(
            charger_id="SIM-CHARGER-FAST",
            name="North Hub Fast Charger",
            depot_id=depot_id,
            lat=13.0600,
            lon=77.6350,
            available_kw=90,
            price_per_kwh=10.8,
            connector_type="CCS2",
            port_count=2,
        ),
        Charger(
            charger_id="SIM-CHARGER-CHEAP",
            name="Solar Canopy Charger",
            depot_id=depot_id,
            lat=12.9350,
            lon=77.6900,
            available_kw=60,
            price_per_kwh=7.2,
            connector_type="CCS2",
            port_count=1,
        ),
        Charger(
            charger_id="SIM-CHARGER-SLOW",
            name="Overflow Charger",
            depot_id=depot_id,
            lat=12.9900,
            lon=77.5600,
            available_kw=30,
            price_per_kwh=8.5,
            connector_type="CCS2",
            port_count=1,
        ),
    ]
    if request.scenario == SimulationScenario.CHARGER_FAILURE:
        chargers[1].status = ChargerStatus.FAULTY
    for charger in chargers:
        await db.chargers.update_one(
            {"charger_id": charger.charger_id},
            {"$set": charger.model_dump(mode="python")},
            upsert=True,
        )

    tariffs = [
        Tariff(
            tariff_id="SIM-TARIFF-SOLAR",
            depot_id=depot_id,
            start_time="10:00",
            end_time="18:00",
            price_per_kwh=7.2,
        ),
        Tariff(
            tariff_id="SIM-TARIFF-PEAK",
            depot_id=depot_id,
            start_time="18:00",
            end_time="22:00",
            price_per_kwh=10.8,
        ),
        Tariff(
            tariff_id="SIM-TARIFF-NORMAL",
            depot_id=depot_id,
            start_time="22:00",
            end_time="10:00",
            price_per_kwh=8.5,
        ),
    ]
    tariffs.extend(
        [
            Tariff(
                tariff_id="SIM-TARIFF-CHEAP-DAY",
                depot_id=depot_id,
                charger_id="SIM-CHARGER-CHEAP",
                start_time="10:00",
                end_time="18:00",
                price_per_kwh=5.4,
            ),
            Tariff(
                tariff_id="SIM-TARIFF-CHEAP-NIGHT",
                depot_id=depot_id,
                charger_id="SIM-CHARGER-CHEAP",
                start_time="18:00",
                end_time="10:00",
                price_per_kwh=6.4,
            ),
        ]
    )
    for tariff in tariffs:
        await db.tariffs.update_one(
            {"tariff_id": tariff.tariff_id},
            {"$set": tariff.model_dump(mode="python")},
            upsert=True,
        )

    await db.simulation.update_one(
        {"simulation_id": "active"},
        {
            "$set": {
                "seed": request.seed,
                "scenario": request.scenario.value,
                "created_at": now,
                "run_id": run_id,
            }
        },
        upsert=True,
    )

    vehicles: list[str] = []
    seed_records: list[tuple[Vehicle, Trip, TelemetryEvent]] = []
    for index in range(request.vehicle_count):
        vin = f"SIM{index + 1:014d}"
        vehicle = Vehicle(
            vin=vin,
            name=f"Fleet Van {index + 1:03d}",
            depot_id=depot_id,
            battery_capacity_kwh=75,
            usable_capacity_kwh=70,
            consumption_kwh_per_km=round(rng.uniform(0.18, 0.24), 3),
            max_charge_power_kw=60,
            connector_type="CCS2",
        )
        distance_km = round(rng.uniform(45, 85), 1)
        if index == 0 and request.scenario in {
            SimulationScenario.LOW_BATTERY_BEFORE_TRIP,
            SimulationScenario.CHARGER_CONGESTION,
        }:
            distance_km = 95
        if index == 0 and request.scenario == SimulationScenario.UNEXPECTED_LONG_TRIP:
            distance_km = 140
        destination_lat, destination_lon = point_at_distance(
            depot.lat,
            depot.lon,
            distance_km,
            25 + (index * 67) % 320,
        )
        trip = Trip(
            trip_id=f"SIM-TRIP-{index + 1:04d}",
            vin=vin,
            origin="Central Fleet Depot",
            destination=f"Customer {index + 1:03d}",
            origin_lat=depot.lat,
            origin_lon=depot.lon,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
            departure_time=demo_departure_time(now, index),
            delivery_deadline=now
            + timedelta(
                hours=6
                if request.scenario == SimulationScenario.UNEXPECTED_LONG_TRIP
                else 5
            ),
            distance_km=distance_km,
            service_duration_minutes=20,
        )
        soc_pct = round(rng.uniform(55, 85), 1)
        if index == 0 and request.scenario != SimulationScenario.NORMAL_DAY:
            soc_pct = 20
        if index == 0 and request.scenario == SimulationScenario.UNEXPECTED_LONG_TRIP:
            soc_pct = 45
        temperature = (
            48
            if index == 0 and request.scenario == SimulationScenario.BATTERY_OVERHEATING
            else 31
        )
        if index == 0 and request.scenario == SimulationScenario.UNREACHABLE_CHARGER:
            soc_pct = 0
        telemetry = TelemetryEvent(
            simulation_run_id=run_id,
            vin=vin,
            ts=now,
            lat=depot.lat,
            lon=depot.lon,
            speed_kmh=0,
            soc_pct=soc_pct,
            soh_pct=round(rng.uniform(88, 98), 1),
            odo_km=round(rng.uniform(10000, 80000), 1),
            seq=0,
            battery_temperature_c=temperature,
            operating_state=OperatingState.PARKED,
            trip_id=trip.trip_id,
            route_remaining_km=trip.distance_km,
            remaining_range_km=round(
                vehicle.usable_capacity_kwh
                * soc_pct
                / 100
                / vehicle.consumption_kwh_per_km,
                1,
            ),
        )
        if index == 0 and request.scenario == SimulationScenario.UNEXPECTED_LONG_TRIP:
            # Load at the incident checkpoint, ready to explain without waiting.
            trip.status = TripStatus.IN_PROGRESS
            trip.destination = "Urgent extended delivery"
            telemetry.operating_state = OperatingState.DRIVING
        if index == 0 and request.scenario == SimulationScenario.UNREACHABLE_CHARGER:
            telemetry.lat, telemetry.lon = point_at_distance(
                depot.lat, depot.lon, 80, 220
            )
            trip.origin = "Remote delivery checkpoint"
            trip.origin_lat, trip.origin_lon = telemetry.lat, telemetry.lon
            trip.distance_km = haversine_km(
                telemetry.lat, telemetry.lon, trip.destination_lat, trip.destination_lon
            )
            trip.status = TripStatus.IN_PROGRESS
            telemetry.route_remaining_km = trip.distance_km
            telemetry.operating_state = OperatingState.STRANDED
        telemetry.navigation_phase = "DELIVERY"
        telemetry.navigation_target = trip.destination
        telemetry.destination_lat, telemetry.destination_lon = (
            trip.destination_lat,
            trip.destination_lon,
        )
        telemetry.distance_to_destination_km = trip.distance_km
        telemetry.eta_minutes = trip.distance_km / 35 * 60
        seed_records.append((vehicle, trip, telemetry))
        vehicles.append(vin)

    async def persist_seed_record(
        vehicle: Vehicle, trip: Trip, telemetry: TelemetryEvent
    ) -> None:
        await db.vehicles.update_one(
            {"vin": vehicle.vin},
            {"$set": vehicle.model_dump(mode="python")},
            upsert=True,
        )
        await db.trips.update_one(
            {"trip_id": trip.trip_id},
            {"$set": trip.model_dump(mode="python")},
            upsert=True,
        )
        # Stored, connected daily itinerary. Only the first leg is executed by this demo.
        next_lat, next_lon = point_at_distance(
            trip.destination_lat, trip.destination_lon, 12, 120
        )
        for leg, origin, destination, a, b, departure, deadline in [
            (
                2,
                trip.destination,
                f"Collection hub {vehicle.vin[-3:]}",
                (trip.destination_lat, trip.destination_lon),
                (next_lat, next_lon),
                now + timedelta(hours=6.5),
                now + timedelta(hours=7.5),
            ),
            (
                3,
                f"Collection hub {vehicle.vin[-3:]}",
                depot.name,
                (next_lat, next_lon),
                (depot.lat, depot.lon),
                now + timedelta(hours=8),
                now + timedelta(hours=13),
            ),
        ]:
            scheduled = Trip(
                trip_id=f"{trip.trip_id}-LEG-{leg}",
                vin=vehicle.vin,
                origin=origin,
                destination=destination,
                origin_lat=a[0],
                origin_lon=a[1],
                destination_lat=b[0],
                destination_lon=b[1],
                departure_time=departure,
                delivery_deadline=deadline,
                distance_km=haversine_km(*a, *b),
                simulation_enabled=False,
            )
            await db.trips.insert_one(scheduled.model_dump(mode="python"))
        await store_telemetry(telemetry, db, redis, kafka)

    await asyncio.gather(
        *(
            persist_seed_record(vehicle, trip, telemetry)
            for vehicle, trip, telemetry in seed_records
        )
    )

    if request.scenario == SimulationScenario.CHARGER_CONGESTION:
        reservation = Reservation(
            reservation_id="SIM-CONGESTION-RESERVATION",
            charger_id="SIM-CHARGER-CHEAP",
            port_number=1,
            vin=vehicles[-1],
            start_time=now,
            end_time=now + timedelta(minutes=75),
            reserved_power_kw=60,
        )
        await db.reservations.update_one(
            {"reservation_id": reservation.reservation_id},
            {"$set": reservation.model_dump(mode="python")},
            upsert=True,
        )

    return {
        "scenario": request.scenario.value,
        "vehicles_seeded": len(vehicles),
        "primary_demo_vin": vehicles[0],
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
        self._last_tick_real: float | None = None
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
        self._last_tick_real = None
        self._emitted_events = 0

    @serialized
    async def stop(self) -> SimulatorStatus:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._last_tick_real = None
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
        self._last_tick_real = deadline - self._tick_seconds
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
        elapsed_real = self._tick_seconds
        if self._last_tick_real is not None:
            real_now = asyncio.get_running_loop().time()
            elapsed_real = max(0, real_now - self._last_tick_real)
            self._last_tick_real = real_now
        elapsed_simulated_seconds = elapsed_real * self._time_scale
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
            {"vin": state.vehicle.vin, "status": {"$in": ["APPROVED", "CHARGING"]}}
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
            if trip_doc and state.battery_temperature_c >= 45:
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
                    speed_kmh = self._rng.uniform(32, 48)
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
                        {"$set": {"status": TripStatus.COMPLETED.value}},
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
