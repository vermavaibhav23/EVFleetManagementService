import math
from datetime import UTC, datetime, timedelta

from app.models.charger import Charger, ChargerStatus
from app.models.charging import (
    CandidateCharger,
    ChargingPlan,
    ChargingRecommendation,
    ReadinessAssessment,
    ReadinessStatus,
)
from app.models.reservation import Reservation
from app.models.tariff import Tariff
from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip, TripStatus
from app.models.vehicle import Vehicle
from app.services.energy import charging_seconds
from app.services.pricing import charging_cost, price_at
from app.services.reservations import has_reservation_conflict, intervals_overlap


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _round_up(moment: datetime, slot_minutes: int) -> datetime:
    seconds = slot_minutes * 60
    return datetime.fromtimestamp(
        math.ceil(moment.timestamp() / seconds) * seconds, UTC
    )


def create_recommendation(
    vehicle: Vehicle,
    telemetry: TelemetryEvent,
    trip: Trip | None,
    readiness: ReadinessAssessment,
    chargers: list[Charger],
    reservations: list[Reservation],
    tariffs: list[Tariff],
    depot_power_limits: dict[str, float] | None = None,
    now: datetime | None = None,
    reserve_range_km: float = 15,
    charge_soon_margin_km: float = 15,
    deadline_buffer_minutes: int = 20,
    slot_minutes: int = 15,
    average_travel_speed_kmh: float = 35,
    charging_efficiency: float = 0.92,
) -> ChargingRecommendation:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if trip is None or readiness.status == ReadinessStatus.UNKNOWN:
        return ChargingRecommendation(
            vin=vehicle.vin,
            readiness=readiness,
            reason="A charging plan requires an active or upcoming trip.",
        )
    if readiness.status == ReadinessStatus.SAFE:
        return ChargingRecommendation(
            vin=vehicle.vin,
            readiness=readiness,
            reason="No charging plan is needed because the vehicle has sufficient range margin.",
        )
    blocking_health_flags = {
        "HIGH_BATTERY_TEMPERATURE",
        "LOW_BATTERY_TEMPERATURE",
        "DIAGNOSTIC_TROUBLE_CODE",
    }
    if blocking_health_flags.intersection(readiness.health_flags):
        return ChargingRecommendation(
            vin=vehicle.vin,
            readiness=readiness,
            reason="Normal charging is blocked until the battery-health warning is inspected.",
        )

    depot_power_limits = depot_power_limits or {}
    charger_depots = {item.charger_id: item.depot_id for item in chargers}
    soh_pct = telemetry.soh_pct if telemetry.soh_pct is not None else 100
    effective_capacity = vehicle.usable_capacity_kwh * soh_pct / 100
    desired_margin_km = reserve_range_km + charge_soon_margin_km
    if effective_capacity <= 0:
        return ChargingRecommendation(
            vin=vehicle.vin,
            readiness=readiness,
            reason="Battery capacity is unavailable; vehicle requires service.",
        )
    emergency_diversion = trip.status == TripStatus.IN_PROGRESS
    # A delivery deadline, if supplied, includes the journey AFTER charging.
    # Legacy trips without one use their scheduled journey duration plus 2 h slack.
    delivery_deadline = trip.delivery_deadline or (
        trip.departure_time
        + timedelta(hours=trip.distance_km / average_travel_speed_kmh + 2)
    )
    deadline = trip.departure_time - timedelta(minutes=deadline_buffer_minutes)
    candidates: list[CandidateCharger] = []

    for charger in chargers:
        if charger.status != ChargerStatus.AVAILABLE:
            continue
        if charger.connector_type.casefold() != vehicle.connector_type.casefold():
            continue

        travel_distance = haversine_km(
            telemetry.lat, telemetry.lon, charger.lat, charger.lon
        )
        travel_energy = travel_distance * vehicle.consumption_kwh_per_km
        if travel_energy >= readiness.available_energy_kwh:
            continue

        delivery_km = (
            haversine_km(
                charger.lat, charger.lon, trip.destination_lat, trip.destination_lon
            )
            if trip.destination_lat is not None and trip.destination_lon is not None
            else float(readiness.trip_distance_km or trip.distance_km) + travel_distance
        )
        desired_energy = (
            delivery_km + desired_margin_km
        ) * vehicle.consumption_kwh_per_km
        if desired_energy > effective_capacity:
            continue
        target_soc = math.ceil(desired_energy / effective_capacity * 1000) / 10
        desired_energy = target_soc / 100 * effective_capacity
        ready_deadline = (
            delivery_deadline
            - timedelta(
                hours=delivery_km / average_travel_speed_kmh,
                minutes=deadline_buffer_minutes,
            )
            if trip.delivery_deadline or emergency_diversion
            else deadline
        )
        travel_minutes = travel_distance / average_travel_speed_kmh * 60
        earliest_start = _round_up(
            now + timedelta(minutes=travel_minutes), slot_minutes
        )
        available_after_travel = max(0, readiness.available_energy_kwh - travel_energy)
        energy_required = max(0, desired_energy - available_after_travel)
        if energy_required <= 0:
            continue

        allocated_power = min(vehicle.max_charge_power_kw, charger.available_kw)
        duration = timedelta(
            seconds=charging_seconds(
                available_after_travel / effective_capacity * 100,
                target_soc,
                effective_capacity,
                allocated_power,
                charging_efficiency,
            )
        )
        # One tick of margin covers discrete arrival/plug-in in the 60x demo.
        duration += timedelta(minutes=1)
        latest_start = ready_deadline - duration
        if latest_start < earliest_start:
            continue

        slot = earliest_start
        while slot <= latest_start:
            end = slot + duration
            depot_limit = depot_power_limits.get(charger.depot_id)
            reserved_depot_power = sum(
                reservation.reserved_power_kw
                for reservation in reservations
                if reservation.status.value
                in {"CONFIRMED", "VEHICLE_EN_ROUTE", "OCCUPIED"}
                and charger_depots.get(reservation.charger_id) == charger.depot_id
                and intervals_overlap(
                    slot, end, reservation.start_time, reservation.end_time
                )
            )
            if (
                depot_limit is not None
                and reserved_depot_power + allocated_power > depot_limit
            ):
                slot += timedelta(minutes=slot_minutes)
                continue
            for port_number in range(1, charger.port_count + 1):
                if has_reservation_conflict(
                    reservations,
                    charger.charger_id,
                    port_number,
                    slot,
                    end,
                ):
                    continue

                depot_tariffs = [
                    tariff for tariff in tariffs if tariff.depot_id == charger.depot_id
                ]
                electricity_cost = charging_cost(
                    slot,
                    end,
                    allocated_power,
                    depot_tariffs,
                    charger.price_per_kwh,
                    charging_efficiency,
                    energy_required / charging_efficiency,
                )
                wait_minutes = max(
                    0,
                    (slot - (now + timedelta(minutes=travel_minutes))).total_seconds()
                    / 60,
                )
                deadline_margin = (ready_deadline - end).total_seconds() / 60
                travel_cost = travel_energy * price_at(
                    slot, depot_tariffs, charger.price_per_kwh
                )
                wait_cost = wait_minutes * 0.25
                reliability_penalty = max(0, 30 - deadline_margin) * 2
                candidates.append(
                    CandidateCharger(
                        charger_id=charger.charger_id,
                        port_number=port_number,
                        start_time=slot,
                        end_time=end,
                        travel_distance_km=round(travel_distance, 2),
                        wait_minutes=round(wait_minutes, 1),
                        charging_minutes=round(duration.total_seconds() / 60, 1),
                        allocated_power_kw=allocated_power,
                        electricity_cost=electricity_cost,
                        total_score=round(
                            electricity_cost
                            + travel_cost
                            + wait_cost
                            + reliability_penalty,
                            2,
                        ),
                        deadline_margin_minutes=round(deadline_margin, 1),
                    )
                )
            slot += timedelta(minutes=slot_minutes)

    if not candidates:
        return ChargingRecommendation(
            vin=vehicle.vin,
            readiness=readiness,
            reason="No compatible free charger can make the vehicle ready before its buffered deadline.",
        )

    if emergency_diversion:
        candidates.sort(
            key=lambda candidate: (candidate.start_time, candidate.total_score)
        )
    else:
        candidates.sort(
            key=lambda candidate: (
                candidate.total_score,
                -candidate.deadline_margin_minutes,
            )
        )
    selected = candidates[0]
    charger = next(item for item in chargers if item.charger_id == selected.charger_id)
    delivery_km = (
        haversine_km(
            charger.lat, charger.lon, trip.destination_lat, trip.destination_lon
        )
        if trip.destination_lat is not None and trip.destination_lon is not None
        else float(readiness.trip_distance_km or trip.distance_km)
        + selected.travel_distance_km
    )
    target_soc = (
        math.ceil(
            (delivery_km + desired_margin_km)
            * vehicle.consumption_kwh_per_km
            / effective_capacity
            * 1000
        )
        / 10
    )
    desired_energy = target_soc / 100 * effective_capacity
    travel_energy = selected.travel_distance_km * vehicle.consumption_kwh_per_km
    available_after_travel = max(0, readiness.available_energy_kwh - travel_energy)
    energy_required = max(0, desired_energy - available_after_travel)
    target_soc = min(100, desired_energy / effective_capacity * 100)
    if emergency_diversion:
        reason = (
            f"{charger.name} port {selected.port_number} is the earliest reliable "
            f"emergency option and starts after {selected.wait_minutes:.0f} minutes of waiting."
        )
    else:
        reason = (
            f"{charger.name} port {selected.port_number} is the lowest-cost reliable option. "
            f"It leaves {selected.deadline_margin_minutes:.0f} minutes before the safety-buffered deadline."
        )
    plan = ChargingPlan(
        vin=vehicle.vin,
        simulation_run_id=telemetry.simulation_run_id,
        delivery_deadline=delivery_deadline,
        remaining_delivery_km=round(delivery_km, 3),
        grid_energy_kwh=round(energy_required / charging_efficiency, 3),
        average_price_per_kwh=round(
            selected.electricity_cost / (energy_required / charging_efficiency), 3
        ),
        trip_id=trip.trip_id,
        charger_id=selected.charger_id,
        port_number=selected.port_number,
        start_time=selected.start_time,
        end_time=selected.end_time,
        starting_soc_pct=telemetry.soc_pct,
        target_soc_pct=round(target_soc, 1),
        energy_required_kwh=round(energy_required, 3),
        allocated_power_kw=selected.allocated_power_kw,
        estimated_cost=selected.electricity_cost,
        predicted_ready_time=selected.end_time,
        next_departure_time=trip.departure_time,
        travel_distance_km=selected.travel_distance_km,
        estimated_arrival_time=now
        + timedelta(hours=selected.travel_distance_km / average_travel_speed_kmh),
        reason=reason,
        alternatives=candidates[1:4],
    )
    return ChargingRecommendation(
        vin=vehicle.vin, readiness=readiness, plan=plan, reason=reason
    )
