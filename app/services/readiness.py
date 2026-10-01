from app.models.charging import ReadinessAssessment, ReadinessStatus
from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip
from app.models.vehicle import Vehicle


def assess_readiness(
    vehicle: Vehicle,
    telemetry: TelemetryEvent,
    trip: Trip | None,
    reserve_range_km: float = 15,
    charge_soon_margin_km: float = 15,
) -> ReadinessAssessment:
    soh_pct = telemetry.soh_pct if telemetry.soh_pct is not None else 100
    effective_capacity_kwh = vehicle.usable_capacity_kwh * soh_pct / 100
    available_energy_kwh = effective_capacity_kwh * telemetry.soc_pct / 100
    current_range_km = available_energy_kwh / vehicle.consumption_kwh_per_km

    health_flags: list[str] = []
    if telemetry.battery_temperature_c is not None:
        if telemetry.battery_temperature_c >= 45:
            health_flags.append("HIGH_BATTERY_TEMPERATURE")
        elif telemetry.battery_temperature_c <= 0:
            health_flags.append("LOW_BATTERY_TEMPERATURE")
    if soh_pct < 70:
        health_flags.append("LOW_STATE_OF_HEALTH")
    if telemetry.dtc:
        health_flags.append("DIAGNOSTIC_TROUBLE_CODE")

    if trip is None:
        return ReadinessAssessment(
            vin=vehicle.vin,
            status=ReadinessStatus.UNKNOWN,
            current_soc_pct=telemetry.soc_pct,
            current_range_km=round(current_range_km, 2),
            reserve_range_km=reserve_range_km,
            available_energy_kwh=round(available_energy_kwh, 3),
            health_flags=health_flags,
            explanation="No active or upcoming trip is assigned to this vehicle.",
        )

    trip_distance_km = trip.distance_km
    if telemetry.trip_id == trip.trip_id and telemetry.route_remaining_km is not None:
        trip_distance_km = telemetry.route_remaining_km

    trip_energy_kwh = trip_distance_km * vehicle.consumption_kwh_per_km
    reserve_energy_kwh = reserve_range_km * vehicle.consumption_kwh_per_km
    required_energy_kwh = trip_energy_kwh + reserve_energy_kwh
    energy_deficit_kwh = max(0.0, required_energy_kwh - available_energy_kwh)
    post_trip_energy_kwh = available_energy_kwh - trip_energy_kwh
    post_trip_range_km = max(0.0, post_trip_energy_kwh / vehicle.consumption_kwh_per_km)
    range_margin_km = current_range_km - trip_distance_km - reserve_range_km

    if range_margin_km < 0:
        status = ReadinessStatus.CRITICAL
        explanation = (
            f"The vehicle is short by {abs(range_margin_km):.1f} km after including the "
            f"{reserve_range_km:g} km safety reserve."
        )
    elif range_margin_km <= charge_soon_margin_km:
        status = ReadinessStatus.CHARGE_SOON
        explanation = f"The trip is feasible, but only {range_margin_km:.1f} km of usable range margin remains."
    else:
        status = ReadinessStatus.SAFE
        explanation = f"The vehicle has {range_margin_km:.1f} km of usable range margin after the trip."

    return ReadinessAssessment(
        vin=vehicle.vin,
        status=status,
        current_soc_pct=telemetry.soc_pct,
        current_range_km=round(current_range_km, 2),
        trip_distance_km=round(trip_distance_km, 2),
        post_trip_range_km=round(post_trip_range_km, 2),
        reserve_range_km=reserve_range_km,
        range_margin_km=round(range_margin_km, 2),
        available_energy_kwh=round(available_energy_kwh, 3),
        required_energy_kwh=round(required_energy_kwh, 3),
        energy_deficit_kwh=round(energy_deficit_kwh, 3),
        next_trip_id=trip.trip_id,
        next_departure_time=trip.departure_time,
        health_flags=health_flags,
        explanation=explanation,
    )
