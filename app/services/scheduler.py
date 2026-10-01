"""Deadline-aware charging with bounded look-ahead through a fixed timetable."""

import math
from datetime import UTC, datetime, timedelta

from app.models.charger import ChargerStatus
from app.models.charging import (
    CandidateCharger,
    ChargingPlan,
    ChargingRecommendation,
    ReadinessStatus,
)
from app.models.reservation import Reservation
from app.services.energy import charge_for_seconds, charging_seconds
from app.services.pricing import charging_cost, tariffs_for_charger
from app.services.reservations import has_reservation_conflict, intervals_overlap


def haversine_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    x = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.atan2(math.sqrt(x), math.sqrt(max(0, 1 - x)))


def _round_up(moment, slot_minutes):
    seconds = slot_minutes * 60
    return datetime.fromtimestamp(
        math.ceil(moment.timestamp() / seconds) * seconds, UTC
    )


def healthy_chargers(vehicle, chargers):
    return [
        c
        for c in chargers
        if c.status in {ChargerStatus.AVAILABLE, ChargerStatus.OCCUPIED}
        and c.connector_type.casefold() == vehicle.connector_type.casefold()
    ]


def destination(trip, origin):
    return (
        (trip.destination_lat, trip.destination_lon)
        if trip.destination_lat is not None
        else origin
    )


def leg_distance(origin, trip):
    return (
        haversine_km(*origin, trip.destination_lat, trip.destination_lon)
        if trip.destination_lat is not None
        else trip.distance_km
    )


def escape_km(vehicle, trip, future, chargers):
    if not future or trip.destination_lat is None:
        return 0.0
    ds = [
        haversine_km(trip.destination_lat, trip.destination_lon, c.lat, c.lon)
        for c in healthy_chargers(vehicle, chargers)
    ]
    # Finishing the whole remaining route is also a valid escape, without another charger.
    remainder = sum(t.distance_km for t in future)
    return min([remainder] + ds)


def create_recommendation(
    vehicle,
    telemetry,
    trip,
    readiness,
    chargers,
    reservations,
    tariffs,
    depot_power_limits=None,
    now=None,
    reserve_range_km=15,
    charge_soon_margin_km=15,
    deadline_buffer_minutes=20,
    slot_minutes=15,
    average_travel_speed_kmh=35,
    charging_efficiency=0.92,
    future_trips=None,
    reviewed_plan=None,
):
    now = (now or datetime.now(UTC)).astimezone(UTC)
    future = sorted(
        [
            t
            for t in (future_trips or [])
            if t.trip_id != getattr(trip, "trip_id", None)
            and t.simulation_enabled
            and t.status.value in {"PLANNED", "IN_PROGRESS"}
        ],
        key=lambda t: t.departure_time,
    )

    def no(reason, exclusions=None, required=False):
        return ChargingRecommendation(
            vin=vehicle.vin,
            decision_required=required,
            readiness=readiness,
            reason=reason,
            exclusions=exclusions or [],
        )

    if trip is None or readiness.status == ReadinessStatus.UNKNOWN:
        return no("No delivery needs a charging plan.")
    if {
        "HIGH_BATTERY_TEMPERATURE",
        "LOW_BATTERY_TEMPERATURE",
        "DIAGNOSTIC_TROUBLE_CODE",
    }.intersection(readiness.health_flags):
        return no("Charging is blocked until the battery-health warning is inspected.")
    capacity = (
        vehicle.usable_capacity_kwh
        * (telemetry.soh_pct if telemetry.soh_pct is not None else 100)
        / 100
    )
    consumption = vehicle.consumption_kwh_per_km
    if capacity <= 0:
        return no("Battery capacity is unavailable.")
    available = capacity * telemetry.soc_pct / 100
    current_location = (telemetry.lat, telemetry.lon)
    minimum_km = (
        leg_distance(current_location, trip)
        + reserve_range_km
        + escape_km(vehicle, trip, future, chargers)
    )
    if trip.reserve_exception:
        return no(
            "Manager approved priority delivery with a reserve or continuation exception. Recovery is requested."
        )
    depot_power_limits = depot_power_limits or {}
    charger_depots = {c.charger_id: c.depot_id for c in chargers}

    def effective_deadline(t, moment):
        original = t.delivery_deadline or t.departure_time + timedelta(
            hours=t.distance_km / average_travel_speed_kmh
            + (2 if t.status.value == "IN_PROGRESS" else 0)
        )
        return (
            max(original, moment + timedelta(hours=6)) if t.accepted_delay else original
        )

    def safe_min(origin, t, later):
        return (
            leg_distance(origin, t)
            + reserve_range_km
            + escape_km(vehicle, t, later, chargers)
        ) * consumption

    def station_options(origin, energy, moment, t, later, held):
        options = []
        exclusions = []
        for c in chargers:
            reviewed = (
                reviewed_plan
                if reviewed_plan and t.trip_id == reviewed_plan.trip_id
                else None
            )
            if reviewed and c.charger_id != reviewed.charger_id:
                continue

            def exclude(reason):
                exclusions.append({"charger_id": c.charger_id, "reason": reason})

            if c.status not in {ChargerStatus.AVAILABLE, ChargerStatus.OCCUPIED}:
                exclude(f"Station {c.status.value.lower()}")
                continue
            if c.connector_type.casefold() != vehicle.connector_type.casefold():
                exclude(
                    f"Incompatible connector: {c.connector_type}; vehicle needs {vehicle.connector_type}"
                )
                continue
            travel = haversine_km(*origin, c.lat, c.lon)
            arrival_energy = energy - travel * consumption
            if arrival_energy <= 0:
                exclude(f"Unreachable with current charge ({travel:.1f} km away)")
                continue
            loc = (c.lat, c.lon)
            delivery = leg_distance(loc, t)
            min_energy = safe_min(loc, t, later)
            if min_energy > capacity:
                exclude(
                    "Delivery, reachable continuation and reserve exceed battery capacity"
                )
                continue
            power = min(vehicle.max_charge_power_kw, c.available_kw)
            if power <= 0:
                exclude("No charging power available")
                continue
            arrival = moment + timedelta(hours=travel / average_travel_speed_kmh)
            cutoff = effective_deadline(t, moment) - timedelta(
                minutes=deadline_buffer_minutes,
                hours=delivery / average_travel_speed_kmh,
            )
            cumulative = (
                delivery / average_travel_speed_kmh * 60 + t.service_duration_minutes
            )
            previous = destination(t, loc)
            for later_trip in later:
                cumulative += (
                    leg_distance(previous, later_trip) / average_travel_speed_kmh * 60
                )
                if later_trip.delivery_deadline and not later_trip.accepted_delay:
                    cutoff = min(
                        cutoff,
                        later_trip.delivery_deadline
                        - timedelta(minutes=cumulative + deadline_buffer_minutes),
                    )
                cumulative += later_trip.service_duration_minutes
                previous = destination(later_trip, previous)
            slot = (
                _round_up(
                    arrival + timedelta(minutes=5 if depth_review(t, moment) else 0),
                    slot_minutes,
                )
                if not reviewed
                else reviewed.start_time
            )
            if slot < arrival:
                exclude("Reviewed slot can no longer be reached in time")
                continue
            best = None
            while slot < cutoff:
                for port in range(1, c.port_count + 1):
                    if reviewed and port != reviewed.port_number:
                        continue
                    if any(
                        r.charger_id == c.charger_id
                        and r.port_number == port
                        and r.start_time <= slot < r.end_time
                        for r in held
                    ):
                        continue
                    end_limit = cutoff
                    for r in held:
                        if (
                            r.charger_id == c.charger_id
                            and r.port_number == port
                            and slot < r.start_time < end_limit
                        ):
                            end_limit = r.start_time
                    # Leave a discrete simulation tick for plugging in / release.
                    usable = max(0, (end_limit - slot).total_seconds() - 120)
                    target, _ = charge_for_seconds(
                        arrival_energy / capacity * 100,
                        100,
                        capacity,
                        power,
                        charging_efficiency,
                        usable,
                    )
                    target = math.floor(target * 10 + 1e-7) / 10
                    if reviewed:
                        if target + 1e-7 < reviewed.target_soc_pct:
                            continue
                        target = reviewed.target_soc_pct
                    if (
                        target * capacity / 100 + 1e-8 < min_energy
                        or target <= arrival_energy / capacity * 100
                    ):
                        continue
                    duration = timedelta(
                        seconds=charging_seconds(
                            arrival_energy / capacity * 100,
                            target,
                            capacity,
                            power,
                            charging_efficiency,
                        )
                        + 120
                    )
                    end = slot + duration
                    if has_reservation_conflict(held, c.charger_id, port, slot, end):
                        continue
                    # Check aggregate depot power at every interval boundary, not the sum of disjoint reservations.
                    overlapping = [
                        r
                        for r in held
                        if charger_depots.get(r.charger_id) == c.depot_id
                        and intervals_overlap(slot, end, r.start_time, r.end_time)
                    ]
                    boundaries = [slot] + [
                        r.start_time for r in overlapping if slot < r.start_time < end
                    ]
                    if any(
                        power
                        + sum(
                            r.reserved_power_kw
                            for r in overlapping
                            if r.start_time <= b < r.end_time
                        )
                        > depot_power_limits.get(c.depot_id, float("inf"))
                        for b in boundaries
                    ):
                        continue
                    grid = (
                        target * capacity / 100 - arrival_energy
                    ) / charging_efficiency
                    cost = charging_cost(
                        slot,
                        end,
                        power,
                        tariffs_for_charger(tariffs, c.charger_id, c.depot_id),
                        c.price_per_kwh,
                        charging_efficiency,
                        grid,
                    )
                    wait = max(0, (slot - arrival).total_seconds() / 60)
                    margin = (cutoff - end).total_seconds() / 60
                    unit = cost / grid if grid else c.price_per_kwh
                    # Targets differ: compare unit energy price, travel and waiting after feasibility.
                    score = unit
                    candidate = CandidateCharger(
                        charger_id=c.charger_id,
                        port_number=port,
                        start_time=slot,
                        end_time=end,
                        travel_distance_km=round(travel, 3),
                        travel_minutes=round(travel / average_travel_speed_kmh * 60, 1),
                        wait_minutes=round(wait, 1),
                        charging_minutes=round(duration.total_seconds() / 60, 1),
                        allocated_power_kw=power,
                        electricity_cost=cost,
                        total_score=round(score, 3),
                        deadline_margin_minutes=round(margin, 1),
                        target_soc_pct=target,
                        remaining_delivery_km=round(delivery, 3),
                        grid_energy_kwh=round(grid, 4),
                        average_price_per_kwh=round(unit, 3),
                        arrival_soc_pct=round(arrival_energy / capacity * 100, 3),
                        minimum_soc_pct=round(min_energy / capacity * 100, 1),
                        delivery_eta=end
                        + timedelta(hours=delivery / average_travel_speed_kmh),
                    )
                    if best is None or (
                        candidate.total_score,
                        -candidate.target_soc_pct,
                    ) < (best.total_score, -best.target_soc_pct):
                        best = candidate
                # An earlier available slot gives the most time to charge; retain later slots if blocked.
                if best:
                    break
                if reviewed:
                    break
                slot += timedelta(minutes=slot_minutes)
            if best:
                options.append(best)
            else:
                free_times = []
                for port in range(1, c.port_count + 1):
                    free_at = arrival
                    for booking in sorted(
                        [
                            r
                            for r in held
                            if r.charger_id == c.charger_id and r.port_number == port
                        ],
                        key=lambda r: r.start_time,
                    ):
                        if booking.start_time <= free_at < booking.end_time:
                            free_at = booking.end_time
                    free_times.append(free_at)
                free_at = _round_up(min(free_times), slot_minutes)
                min_charge = (
                    charging_seconds(
                        arrival_energy / capacity * 100,
                        min_energy / capacity * 100,
                        capacity,
                        power,
                        charging_efficiency,
                    )
                    + 120
                )
                earliest_delivery = free_at + timedelta(
                    seconds=min_charge, hours=delivery / average_travel_speed_kmh
                )
                shortfall = max(
                    0,
                    (
                        earliest_delivery
                        + timedelta(minutes=deadline_buffer_minutes)
                        - effective_deadline(t, moment)
                    ).total_seconds()
                    / 60,
                )
                queue = max(0, (free_at - arrival).total_seconds() / 60)
                exclude(
                    f"Queue {queue:.0f} min; minimum charging {min_charge / 60:.0f} min. "
                    + (
                        f"Needs {shortfall:.0f} more minutes to retain the traffic buffer."
                        if shortfall > 0
                        else "No uninterrupted port or depot-power window fits the timetable."
                    )
                )
        return sorted(
            options,
            key=lambda o: (
                o.average_price_per_kwh,
                o.travel_distance_km,
                o.wait_minutes,
                -o.target_soc_pct,
            ),
        ), exclusions

    def route_check(origin, energy, moment, route, held, depth=0):
        if not route:
            return [], 0
        if depth > len(future) + 1:
            return None, 0
        follow = []
        covered = 0
        for i, t in enumerate(route):
            later = route[i + 1 :]
            moment = max(moment, t.departure_time)
            distance = leg_distance(origin, t)
            required = safe_min(origin, t, later)
            if energy + 1e-7 < required:
                choices, _ = station_options(origin, energy, moment, t, later, held)
                for o in choices[:3]:
                    c = next(c for c in chargers if c.charger_id == o.charger_id)
                    # The rest is checked with energy at this future charger.
                    rest, more = route_check(
                        (c.lat, c.lon),
                        o.target_soc_pct / 100 * capacity,
                        o.end_time,
                        route[i:],
                        held,
                        depth + 1,
                    )
                    if rest is not None:
                        d = o.model_dump(mode="json")
                        d.update(
                            trip_id=t.trip_id, starting_soc_pct=energy / capacity * 100
                        )
                        return follow + [d] + rest, covered
                return None, covered
            eta = moment + timedelta(hours=distance / average_travel_speed_kmh)
            if eta + timedelta(
                minutes=deadline_buffer_minutes
                if t.delivery_deadline or t.status.value == "IN_PROGRESS"
                else 0
            ) > effective_deadline(t, moment):
                return None, covered
            energy -= distance * consumption
            origin = destination(t, origin)
            moment = eta + timedelta(minutes=t.service_duration_minutes)
            covered += 1
        return follow, covered

    def depth_review(t, moment):
        return t.trip_id == trip.trip_id and moment == now

    continuation = None
    direct_covered = 0
    if available >= minimum_km * consumption:
        continuation, direct_covered = route_check(
            current_location, available, now, [trip] + future, reservations
        )
        if continuation == []:
            return no(
                "Enough energy and time for a safe continuation through the timetable. No charging detour is needed now."
            )

    options, exclusions = station_options(
        current_location, available, now, trip, future, reservations
    )
    feasible = []
    for o in options:
        c = next(c for c in chargers if c.charger_id == o.charger_id)
        held = reservations + [
            Reservation(
                charger_id=c.charger_id,
                port_number=o.port_number,
                vin=vehicle.vin,
                start_time=o.start_time,
                end_time=o.end_time,
                reserved_power_kw=o.allocated_power_kw,
            )
        ]
        follow, covered = route_check(
            (c.lat, c.lon),
            o.target_soc_pct / 100 * capacity,
            o.end_time,
            [trip] + future,
            held,
        )
        if follow is None:
            exclusions.append(
                {
                    "charger_id": c.charger_id,
                    "reason": "A later delivery or charging stop cannot fit the remaining timetable",
                }
            )
        else:
            o.follow_up_stops = follow
            o.covered_stops = covered
            feasible.append(o)
    if continuation is not None and not trip.accepted_delay:
        # Use a generous window now when a substantial top-up covers more stops.
        # Otherwise keep the already-validated direct journey and charge later.
        feasible = [
            o
            for o in feasible
            if o.target_soc_pct >= 90 and o.covered_stops > direct_covered
        ]
        if not feasible:
            return no(
                "Enough energy for the next delivery; later charging fits the timetable. Continue now."
            )
    if not feasible:
        return no(
            "No charging option meets the timetable with reserve and traffic buffer. Review a manager decision.",
            exclusions,
            required=True,
        )
    selected = feasible[0]
    c = next(c for c in chargers if c.charger_id == selected.charger_id)
    follow_count = len(selected.follow_up_stops)
    reason = (
        f"Charge to {selected.target_soc_pct:g}% at {c.name}. "
        f"{selected.covered_stops} stop(s) covered before another charge; {follow_count} later charging stop(s) validated. "
        f"{selected.wait_minutes:g} min wait, {selected.charging_minutes:g} min charge, INR {selected.electricity_cost:.2f}. "
        f"{reserve_range_km:g} km reserve and {deadline_buffer_minutes} min traffic buffer retained. "
        "Target uses the available time; selection prioritises the lowest energy price among feasible timetables, then distance and waiting."
    )
    if trip.accepted_delay:
        reason = (
            "Manager accepted a delay; original deadline remains visible. " + reason
        )
    plan = ChargingPlan(
        vin=vehicle.vin,
        trip_id=trip.trip_id,
        charger_id=c.charger_id,
        port_number=selected.port_number,
        start_time=selected.start_time,
        end_time=selected.end_time,
        starting_soc_pct=telemetry.soc_pct,
        target_soc_pct=selected.target_soc_pct,
        minimum_soc_pct=selected.minimum_soc_pct,
        energy_required_kwh=round(selected.grid_energy_kwh * charging_efficiency, 3),
        allocated_power_kw=selected.allocated_power_kw,
        estimated_cost=selected.electricity_cost,
        predicted_ready_time=selected.end_time,
        next_departure_time=trip.departure_time,
        travel_distance_km=selected.travel_distance_km,
        estimated_arrival_time=now + timedelta(minutes=selected.travel_minutes),
        simulation_run_id=telemetry.simulation_run_id,
        delivery_deadline=trip.delivery_deadline,
        remaining_delivery_km=selected.remaining_delivery_km,
        grid_energy_kwh=selected.grid_energy_kwh,
        average_price_per_kwh=selected.average_price_per_kwh,
        reason=reason,
        covered_stops=selected.covered_stops,
        follow_up_stops=selected.follow_up_stops,
        accepted_delay=trip.accepted_delay,
        alternatives=feasible[1:3],
        evaluated_options=feasible[:3],
        exclusions=exclusions,
    )
    return ChargingRecommendation(
        vin=vehicle.vin,
        decision_required=True,
        readiness=readiness,
        plan=plan,
        reason=reason,
        exclusions=exclusions,
    )
