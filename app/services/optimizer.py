"""Continuous-time, continuous-energy MILP for a fixed customer sequence.

Bounded model: at most one station visit in each customer gap and the return gap.
Fleet coordination is constrained-first sequential reservation, not fleet optimality.
"""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from app.domain import dt, effective_capacity
from app.services.geometry import distance
from app.services.ledger import bookings
from app.services.milp import Expr, Model, value
from app.services.pricing import intervals, session


def fingerprint(doc, vin):
    state = {k: doc[k] for k in ("run_id", "policy", "stations", "depots")}
    # Heartbeats and job labels do not change physical planning inputs.
    state["vehicle"] = {
        k: v
        for k, v in doc["vehicles"][vin].items()
        if k
        not in {
            "sequence",
            "observed_at",
            "received_at",
            "last_event_id",
            "telemetry_status",
            "planning_status",
            "auto_plan_marker",
            "control_version",
            "reported_activity",
        }
    }
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, default=str).encode()
    ).hexdigest()


def leg(doc, vehicle, a, b):
    # Optional explicit road matrix is also used by deterministic arithmetic fixtures.
    key = f"{a.get('node') or a.get('trip_id') or a.get('charger_id', '')}>{b.get('node') or b.get('trip_id') or b.get('charger_id', '')}"
    if key in doc.get("roads", {}):
        row = doc["roads"][key]
        return row["energy"], row["minutes"]
    km = distance(a, b)
    p = doc["policy"]
    return km * vehicle["consumption_kwh_km"], km / p["speed_kmh"] * 60 * (
        1 + p["travel_allowance_pct"]
    )


def stops(doc, v):
    result = sorted(
        [deepcopy(d) for d in v["deliveries"] if d["status"] == "PLANNED"],
        key=lambda d: d["sequence"],
    )
    depot = deepcopy(doc["depots"][v["depot_id"]])
    depot.update(kind="RETURN", service_minutes=0, name="Return to " + depot["name"])
    # A final explicit depot delivery supplies the endpoint without a duplicate leg.
    if result and result[-1].get("is_return"):
        result[-1]["kind"] = "RETURN"
    else:
        result.append(depot)
    return result


def blocked_power(doc, station, power, existing, origin, horizon):
    depot = doc["depots"][station["depot_id"]]
    limit = depot["power_limit_kw"]
    related = [
        r
        for r in existing
        if doc["stations"][r["charger_id"]]["depot_id"] == station["depot_id"]
    ]
    edges = sorted(
        {
            0.0,
            horizon,
            *[
                max(0.0, min(horizon, (dt(r[t]) - origin).total_seconds() / 60))
                for r in related
                for t in ("start", "end")
            ],
        }
    )
    return [
        (a, b)
        for a, b in zip(edges, edges[1:])
        if b > a
        and power
        + sum(
            r["power_kw"]
            for r in related
            if dt(r["start"]) < origin + timedelta(minutes=(a + b) / 2) < dt(r["end"])
        )
        > limit + 1e-6
    ]


def optimize(
    doc,
    vin,
    recovery=False,
    reserve_exception=False,
    exclude_choices=None,
    fastest=False,
):
    started = monotonic()
    v, p = doc["vehicles"][vin], doc["policy"]
    origin, cap = dt(doc["clock"]), effective_capacity(v)
    horizon = p["horizon_minutes"]
    reserve = 0 if recovery and reserve_exception else p["reserve_kwh"]
    scope = "fixed customer order; at most one charge per gap; continuous time/energy; sequential fleet allocation"

    def fail(status, reason):
        return {
            "status": status,
            "reason": reason,
            "scope": scope,
            "elapsed_seconds": monotonic() - started,
        }

    if reserve_exception and not recovery:
        return fail("ERROR", "A reserve exception requires recovery mode")
    if v["health_fault"] or v["temperature_c"] >= 60 or cap <= 0:
        return fail(
            "INFEASIBLE_MODEL",
            "Vehicle requires physical assistance or health clearance",
        )
    if v["state"] in ("TRAVELLING", "CHARGING", "CONNECTING", "RELEASING"):
        return fail("ERROR", "Wait for a safe stopped state before replacing a journey")
    destinations = stops(doc, v)
    if len(destinations) - 1 > p["max_customers"]:
        return fail("ERROR", "Customer count exceeds documented model bound")
    ready = max(
        0,
        (dt(v["service_until"]) - origin).total_seconds() / 60
        if v.get("service_until")
        else 0,
    )
    if ready > horizon:
        return fail("INFEASIBLE_MODEL", "Service finishes outside the planning horizon")
    # Zero purchased energy is a global lower bound when all tariffs are nonnegative.
    # Forward construction also establishes the earliest possible direct completion.
    if not recovery and exclude_choices is None:
        direct = direct_plan(doc, vin, ready, reserve, scope)
        if direct:
            return dict(
                status="OPTIMAL_MODEL",
                plan=direct,
                scope=scope,
                elapsed_seconds=monotonic() - started,
            )
    m = Model()
    prev_time, prev_energy, previous = (
        Expr({None: ready}),
        Expr({None: v["energy_kwh"]}),
        v,
    )
    cost, late_count, late_sum = Expr(), Expr(), Expr()
    late_bound = horizon + max(
        [0]
        + [
            (origin - dt(d["deadline"])).total_seconds() / 60
            for d in destinations
            if d.get("deadline")
        ]
    )
    max_late = m.var(0, late_bound)
    candidates, rows = [], []
    existing = bookings(doc, exclude_plan=v.get("plan_id")) + doc.get(
        "external_bookings", []
    )
    stations = sorted(
        doc["stations"].values(), key=lambda s: (distance(v, s), s["charger_id"])
    )
    stations = [
        s
        for s in stations
        if s["status"] == "AVAILABLE" and s["connector"] == v["connector"]
    ][: p["max_stations"]]
    for gap, dest in enumerate(destinations):
        direct_e, direct_t = leg(doc, v, previous, dest)
        arrival, service, finish = [m.var(0, horizon) for _ in range(3)]
        energy = m.var(reserve, cap)
        ready_at = (
            max(0, (dt(dest["ready_at"]) - origin).total_seconds() / 60)
            if dest.get("ready_at")
            else 0
        )
        depart = m.var(0, horizon)
        m.ge(depart, prev_time)
        m.ge(depart, ready_at)
        # Dispatch as soon as the previous activity and route readiness allow.
        # Charging may wait for a booked/cheaper slot after arrival at the station.
        if ready_at:
            waiting_for_readiness = m.binary()
            m.le(depart, prev_time + horizon * waiting_for_readiness)
            m.le(depart, ready_at + horizon * (1 - waiting_for_readiness))
        else:
            m.eq(depart, prev_time)
        choices, deltas = [], []
        for s in stations:
            e1, t1 = leg(doc, v, previous, s)
            e2, t2 = leg(doc, v, s, dest)
            power = min(
                s["power_kw"],
                v["max_power_kw"],
                doc["depots"][s["depot_id"]]["power_limit_kw"],
            )
            if power <= 0 or e1 > cap or e2 + reserve > cap:
                continue
            depot_tariffs = doc["depots"][s["depot_id"]].get("tariffs", [])
            tariff = intervals(
                origin, origin + timedelta(minutes=horizon), s, depot_tariffs
            )
            points = [(0, 0.0)]
            for ta, tb, rate in tariff:
                points.append(
                    (
                        (tb - origin).total_seconds() / 60,
                        points[-1][1] + (tb - ta).total_seconds() / 60 * rate,
                    )
                )
            occupied = {
                r["port"] for r in existing if r["charger_id"] == s["charger_id"]
            }
            empty = next(
                (
                    port
                    for port in range(1, s["port_count"] + 1)
                    if port not in occupied
                ),
                None,
            )
            ports = sorted(occupied | ({empty} if empty else set()))
            for port in ports:
                y = m.binary()
                choices.append(y)
                ein, eout = m.var(0, cap), m.var(0, cap)
                begin, end = m.var(0, horizon), m.var(0, horizon)
                m.le(ein, cap * y)
                m.le(eout, cap * y)
                m.le(begin, horizon * y)
                m.le(end, horizon * y)
                m.ge(eout - ein, 0.001 * y)
                m.ge(ein, (0 if gap == 0 and e1 < 1e-9 else reserve) * y)
                m.when_eq(y, ein, prev_energy - e1, 3 * cap + e1)
                m.ge(
                    begin,
                    depart
                    + t1
                    + p["waiting_allowance_minutes"]
                    - 3 * horizon * (1 - y),
                )
                cursor = begin + p["connection_minutes"] * y
                bands = (
                    [
                        (0, 0.8 * cap, 1),
                        (0.8 * cap, 0.1 * cap, 0.6),
                        (0.9 * cap, 0.1 * cap, 0.3),
                    ]
                    if p["taper"]
                    else [(0, cap, 1)]
                )
                tariff_start = m.pwl(cursor, points)
                band_amounts = []
                for lower, width, factor in bands:
                    amount = m.clip(eout, lower, width, 0, cap) - m.clip(
                        ein, lower, width, 0, cap
                    )
                    m.ge(amount, 0)
                    band_amounts.append(amount)
                    band_end = cursor + amount * (
                        60 / (power * p["efficiency"] * factor)
                    )
                    tariff_end = m.pwl(band_end, points)
                    m.ge(tariff_end, tariff_start)
                    band_cost = (tariff_end - tariff_start) * (power * factor / 60)
                    m.ge(
                        band_cost,
                        amount * (min(rate for _, _, rate in tariff) / p["efficiency"]),
                    )
                    cost += band_cost
                    tariff_start = tariff_end
                    cursor = band_end
                m.eq(sum(band_amounts, Expr()), eout - ein)
                m.eq(end, cursor + p["release_minutes"] * y)
                m.when_eq(y, arrival, end + t2, 3 * horizon)
                deltas.append(eout - ein + (direct_e - e1 - e2) * y)
                conflicts = [
                    (
                        (dt(r["start"]) - origin).total_seconds() / 60,
                        (dt(r["end"]) - origin).total_seconds() / 60,
                    )
                    for r in existing
                    if r["charger_id"] == s["charger_id"] and r["port"] == port
                ]
                conflicts += blocked_power(doc, s, power, existing, origin, horizon)
                for a, b in conflicts:
                    if b <= 0 or a >= horizon:
                        continue
                    side = m.binary()
                    m.le(end, a + 3 * horizon * (side + 1 - y))
                    m.ge(begin, b - 3 * horizon * (2 - side - y))
                candidate = dict(
                    y=y,
                    ein=ein,
                    eout=eout,
                    begin=begin,
                    end=end,
                    station=s,
                    port=port,
                    power=power,
                    t1=t1,
                    e1=e1,
                    gap=gap,
                )
                candidates.append(candidate)
        chosen = sum(choices, Expr())
        m.le(chosen, 1)
        m.when_eq(1 - chosen, arrival, depart + direct_t, 3 * horizon)
        m.eq(energy, prev_energy - direct_e + sum(deltas, Expr()))
        m.ge(service, arrival)
        if dest.get("accepts_at"):
            acceptance = max(0, (dt(dest["accepts_at"]) - origin).total_seconds() / 60)
            m.ge(service, acceptance)
            after = m.binary()
            m.le(service, arrival + horizon * (1 - after))
            m.le(service, acceptance + horizon * after)
        else:
            m.eq(service, arrival)
        m.eq(finish, service + dest.get("service_minutes", 0))
        if dest.get("deadline"):
            deadline = (dt(dest["deadline"]) - origin).total_seconds() / 60
            if recovery:
                late = m.var(0, late_bound)
                flag = m.binary()
                m.ge(late, arrival - deadline)
                m.le(late, late_bound * flag)
                m.ge(max_late, late)
                late_count += flag
                late_sum += late
            else:
                m.le(arrival, deadline)
        rows.append(
            dict(
                dest=dest,
                arrival=arrival,
                service=service,
                finish=finish,
                energy=energy,
                depart=depart,
            )
        )
        prev_time, prev_energy, previous = finish, energy, dest
    if exclude_choices is not None:
        selected = set(exclude_choices)
        # Exclude a discrete visit pattern, retaining continuous amounts in each solve.
        m.ge(
            sum(
                (
                    1 - c["y"] if i in selected else c["y"]
                    for i, c in enumerate(candidates)
                ),
                Expr(),
            ),
            1,
        )
    objectives = ([late_count, max_late, late_sum] if recovery else []) + (
        [prev_time, cost] if fastest else [cost, prev_time]
    )
    result = None
    cost_result = None
    all_optimal = True
    phase_status = []
    for objective in objectives:
        remaining = p["solver_seconds"] - (monotonic() - started)
        if remaining <= 0.05:
            all_optimal = False
            break
        solved = m.solve(
            objective,
            remaining * 0.9 if len(phase_status) < len(objectives) - 1 else remaining,
        )
        phase_status.append(int(solved.status))
        if solved.x is None:
            if result is None:
                if solved.status == 1 and not recovery:
                    fallback = depot_fallback(doc, vin, ready, reserve, scope)
                    if fallback:
                        fallback["solver"]["elapsed_seconds"] = monotonic() - started
                        return dict(
                            status="FEASIBLE",
                            plan=fallback,
                            scope=scope,
                            elapsed_seconds=monotonic() - started,
                        )
                return fail(
                    "INFEASIBLE_MODEL"
                    if solved.status == 2
                    else "LIMIT_NO_INCUMBENT"
                    if solved.status == 1
                    else "ERROR",
                    str(solved.message),
                )
            all_optimal = False
            break
        result = solved
        if objective is cost:
            cost_result = solved
        if solved.status != 0:
            all_optimal = False
            if not recovery and not fastest and objective is cost:
                # Spend the remaining budget removing idle time from the incumbent.
                # Fix only visits/ports, retaining continuous energy and all tariff segments.
                for candidate in candidates:
                    m.eq(candidate["y"], round(value(candidate["y"], solved.x)))
            else:
                break
        m.le(objective, value(objective, solved.x) + 1e-5)
    if result is None:
        return fail(
            "LIMIT_NO_INCUMBENT", "Model construction exhausted the solve budget"
        )

    def val(expr):
        return value(expr, result.x)

    def stamp(minute):
        return origin + timedelta(minutes=max(0, minute))

    operations = []
    selected = []
    for gap, row in enumerate(rows):
        departure = stamp(val(row["depart"]))
        for i, c in enumerate(candidates):
            if c["gap"] != gap or val(c["y"]) < 0.5:
                continue
            selected.append(i)
            s = c["station"]
            ein, eout = max(0, val(c["ein"])), min(cap, val(c["eout"]))
            start = stamp(val(c["begin"]))
            seconds, grid, charge_cost = session(
                start + timedelta(minutes=p["connection_minutes"]),
                ein,
                eout,
                cap,
                c["power"],
                p["efficiency"],
                s,
                doc["depots"][s["depot_id"]].get("tariffs", []),
                p["taper"],
            )
            end = start + timedelta(
                seconds=seconds, minutes=p["connection_minutes"] + p["release_minutes"]
            )
            operations.append(
                dict(
                    stop_id=str(uuid4()),
                    kind="CHARGE",
                    name=s["name"],
                    node=s.get("node"),
                    lat=s["lat"],
                    lon=s["lon"],
                    charger_id=s["charger_id"],
                    port=c["port"],
                    depart=departure,
                    arrival=departure + timedelta(minutes=c["t1"]),
                    start=start,
                    end=end,
                    energy_arrival=ein,
                    energy_end=eout,
                    power_kw=c["power"],
                    grid_kwh=grid,
                    cost=charge_cost,
                    target_soc=eout / cap * 100,
                    status="PLANNED",
                )
            )
            departure = end
        d = row["dest"]
        arrive = stamp(val(row["arrival"]))
        operations.append(
            dict(
                stop_id=d.get("trip_id", f"return-{vin}"),
                kind=d.get("kind", "DELIVERY"),
                name=d["name"],
                lat=d["lat"],
                lon=d["lon"],
                node=d.get("node"),
                trip_id=d.get("trip_id"),
                depart=departure,
                arrival=arrive,
                start=stamp(val(row["service"])),
                end=stamp(val(row["finish"])),
                energy_arrival=val(row["energy"]),
                energy_end=val(row["energy"]),
                deadline=d.get("deadline"),
                lateness_minutes=max(
                    0, (arrive - dt(d["deadline"])).total_seconds() / 60
                )
                if d.get("deadline")
                else 0,
                cost=0,
                grid_kwh=0,
                power_kw=0,
                status="PLANNED",
            )
        )
    status = "OPTIMAL_MODEL" if all_optimal else "FEASIBLE"
    solver = dict(
        status=status,
        scope=scope,
        elapsed_seconds=monotonic() - started,
        phases=phase_status,
        variables=len(m.lower),
        binaries=sum(m.integer),
        gap=float(getattr(cost_result or result, "mip_gap", 0) or 0),
        bound=float(getattr(cost_result or result, "mip_dual_bound", 0) or 0),
        bound_objective="cost"
        if cost_result is not None
        else "first unfinished lexicographic phase",
    )
    plan = dict(
        plan_id=str(uuid4()),
        run_id=doc["run_id"],
        vin=vin,
        version=1,
        snapshot_revision=doc["revision"],
        fingerprint=fingerprint(doc, vin),
        created_at=origin,
        valid_until=operations[0]["depart"],
        status="PROPOSED",
        recovery=recovery,
        reserve_kwh=reserve,
        operations=operations,
        total_cost=sum(o["cost"] for o in operations),
        solver=solver,
        reason="Recovery requires acknowledgement"
        if recovery
        else "Earliest completion alternative"
        if fastest
        else "Minimum whole-journey energy cost within the stated model"
        if all_optimal
        else "Cost-first feasible journey; optimality not proven",
        replaces=v.get("plan_id"),
        choices=selected,
    )
    from app.services.validation import validate

    errors = validate(doc, plan)
    if errors:
        return fail("VALIDATION_REJECTED", "; ".join(errors))
    return dict(
        status=status, plan=plan, scope=scope, elapsed_seconds=monotonic() - started
    )


def direct_plan(doc, vin, ready, reserve, scope):
    from app.services.validation import validate

    v = doc["vehicles"][vin]
    now = dt(doc["clock"])
    cursor = now + timedelta(minutes=ready)
    previous = v
    energy = v["energy_kwh"]
    operations = []
    for d in stops(doc, v):
        if d.get("ready_at"):
            cursor = max(cursor, dt(d["ready_at"]))
        used, minutes = leg(doc, v, previous, d)
        energy -= used
        arrival = cursor + timedelta(minutes=minutes)
        start = max(arrival, dt(d["accepts_at"])) if d.get("accepts_at") else arrival
        end = start + timedelta(minutes=d.get("service_minutes", 0))
        operations.append(
            dict(
                stop_id=d.get("trip_id", f"return-{vin}"),
                kind=d.get("kind", "DELIVERY"),
                name=d["name"],
                node=d.get("node"),
                lat=d["lat"],
                lon=d["lon"],
                trip_id=d.get("trip_id"),
                depart=cursor,
                arrival=arrival,
                start=start,
                end=end,
                energy_arrival=energy,
                energy_end=energy,
                deadline=d.get("deadline"),
                lateness_minutes=0,
                cost=0,
                grid_kwh=0,
                power_kw=0,
                status="PLANNED",
            )
        )
        previous = d
        cursor = end
    plan = dict(
        plan_id=str(uuid4()),
        run_id=doc["run_id"],
        vin=vin,
        version=1,
        snapshot_revision=doc["revision"],
        fingerprint=fingerprint(doc, vin),
        created_at=now,
        valid_until=operations[0]["depart"],
        status="PROPOSED",
        recovery=False,
        reserve_kwh=reserve,
        operations=operations,
        total_cost=0,
        solver=dict(
            status="OPTIMAL_MODEL",
            scope=scope,
            proof="Nonnegative-cost lower bound; direct route, earliest service",
            elapsed_seconds=0,
            variables=0,
            binaries=0,
            gap=0,
        ),
        reason="No charging required for the complete journey",
        replaces=v.get("plan_id"),
        choices=[],
    )
    return None if validate(doc, plan) else plan


def depot_fallback(doc, vin, ready, reserve, scope):
    """A validated stopped-at-station fallback; no optimization claim."""
    from app.services.validation import validate

    v = doc["vehicles"][vin]
    p = doc["policy"]
    now = dt(doc["clock"])
    cap = effective_capacity(v)
    needed = reserve
    previous = v
    for dest in stops(doc, v):
        used, _ = leg(doc, v, previous, dest)
        needed += used
        previous = dest
    if needed > cap or needed <= v["energy_kwh"]:
        return None
    departure = now + timedelta(minutes=ready)
    first = stops(doc, v)[0]
    if first.get("ready_at"):
        departure = max(departure, dt(first["ready_at"]))
    existing = bookings(doc, exclude_plan=v.get("plan_id")) + doc.get(
        "external_bookings", []
    )
    options = []
    for s in doc["stations"].values():
        if (
            s["status"] != "AVAILABLE"
            or s["connector"] != v["connector"]
            or leg(doc, v, v, s)[0] > 1e-9
        ):
            continue
        power = min(
            s["power_kw"],
            v["max_power_kw"],
            doc["depots"][s["depot_id"]]["power_limit_kw"],
        )
        if power <= 0:
            continue
        for port in range(1, s["port_count"] + 1):
            starts = sorted(
                {
                    departure + timedelta(minutes=p["waiting_allowance_minutes"]),
                    *[dt(r["end"]) for r in existing if dt(r["end"]) >= departure],
                }
            )
            for start in starts:
                seconds, grid, cost = session(
                    start + timedelta(minutes=p["connection_minutes"]),
                    v["energy_kwh"],
                    needed,
                    cap,
                    power,
                    p["efficiency"],
                    s,
                    doc["depots"][s["depot_id"]].get("tariffs", []),
                    p["taper"],
                )
                end = start + timedelta(
                    seconds=seconds,
                    minutes=p["connection_minutes"] + p["release_minutes"],
                )
                future = deepcopy(doc)
                future["clock"] = end.isoformat()
                future["vehicles"][vin].update(energy_kwh=needed, service_until=None)
                plan = direct_plan(future, vin, 0, reserve, scope)
                if not plan:
                    continue
                op = dict(
                    stop_id=str(uuid4()),
                    kind="CHARGE",
                    name=s["name"],
                    node=s.get("node"),
                    lat=s["lat"],
                    lon=s["lon"],
                    charger_id=s["charger_id"],
                    port=port,
                    depart=departure,
                    arrival=departure,
                    start=start,
                    end=end,
                    energy_arrival=v["energy_kwh"],
                    energy_end=needed,
                    power_kw=power,
                    grid_kwh=grid,
                    cost=cost,
                    target_soc=needed / cap * 100,
                    status="PLANNED",
                )
                plan.update(
                    created_at=now,
                    valid_until=departure,
                    fingerprint=fingerprint(doc, vin),
                    total_cost=cost,
                    reason="Validated station-first fallback after solver time limit",
                    solver=dict(
                        status="FEASIBLE",
                        scope=scope,
                        method="validated fallback",
                        solver_status="LIMIT_NO_INCUMBENT",
                        elapsed_seconds=0,
                    ),
                    operations=[op] + plan["operations"],
                )
                if not validate(doc, plan):
                    options.append(plan)
                    break
    return (
        min(
            options,
            key=lambda plan: (plan["total_cost"], dt(plan["operations"][-1]["end"])),
        )
        if options
        else None
    )
