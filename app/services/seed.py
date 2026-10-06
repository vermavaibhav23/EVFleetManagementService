"""Reproducible scenarios; events change facts, never prescribe solver answers."""

from datetime import datetime, timedelta, timezone
from random import Random
from uuid import uuid4

from app.domain import Delivery, Policy, Station, Vehicle
from app.services.geometry import point


def seed(request):
    if str(request.scenario) not in ("NORMAL_DAY", "EDGE_CASE_DAY"):
        from app.services.scenario_groups import grouped_seed

        return grouped_seed(request)
    start = request.start_time or datetime(
        2026, 10, 4, 8, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    rng = Random(request.seed)
    depot = dict(
        depot_id="SIM-DEPOT",
        name="Bengaluru depot",
        lat=12.9716,
        lon=77.5946,
        power_limit_kw=160,
        tariffs=[],
    )
    stations = {}
    for i, (km, bearing, power, price, ports) in enumerate(
        [
            (0, 0, 60, 20, 2),
            (9, 40, 30, 10, 2),
            (15, 130, 50, 14, 2),
            (18, 250, 40, 12, 1),
        ]
    ):
        s = Station(
            charger_id=f"SIM-C{i + 1}",
            name=["Depot rapid", "North economy", "East hub", "West hub"][i],
            depot_id=depot["depot_id"],
            **point(depot["lat"], depot["lon"], km, bearing),
            power_kw=power,
            price=price,
            port_count=ports,
            tariffs=[
                dict(
                    id=f"peak-{i}",
                    start_minute=600,
                    end_minute=660,
                    price=price + 5,
                    priority=1,
                ),
                dict(
                    id=f"evening-{i}",
                    start_minute=1020,
                    end_minute=1140,
                    price=price + 8,
                    priority=1,
                ),
            ],
        )
        stations[s.charger_id] = s.model_dump(mode="json", by_alias=True)
    cases = [
        "DEPOT_STRANDING",
        "INITIAL_RESERVE",
        "IMPOSSIBLE_DEADLINE",
        "POWER_REDUCTION",
        "CHARGER_FAILURE",
        "QUEUE_CONTENTION",
        "EXTRA_CONSUMPTION",
        "LONG_UNLOAD",
        "STALE_APPROVAL",
        "HEALTH_FAULT",
        "EN_ROUTE_STRANDING",
        "RECOVERABLE_DELAY",
    ]
    vehicles = {}
    events = []
    for i in range(request.vehicle_count):
        vin = f"SIM-{i + 1:03d}"
        ready = start + timedelta(minutes=(i // 4) * 10)
        deliveries = []
        for j in range(3):
            location = point(
                depot["lat"],
                depot["lon"],
                12 + j * 5 + rng.random() * 3,
                25 + i * 29 + j * 12,
            )
            deliveries.append(
                Delivery(
                    trip_id=f"{vin}-D{j + 1}",
                    sequence=j + 1,
                    name=f"Customer {j + 1}",
                    **location,
                    ready_at=ready,
                    accepts_at=ready,
                    deadline=ready + timedelta(minutes=100 + j * 95),
                    service_minutes=6 + rng.randrange(5),
                )
            )
        v = Vehicle(
            vin=vin,
            name=f"Van {i + 1:02d}",
            depot_id=depot["depot_id"],
            lat=depot["lat"],
            lon=depot["lon"],
            capacity_kwh=45,
            energy_kwh=10 + rng.random() * 9,
            deliveries=deliveries,
        )
        if str(request.scenario) == "EDGE_CASE_DAY":
            v.case = cases[i] if i < len(cases) else "ADDITIONAL_FLEET"
            if i == 0:
                v.energy_kwh = 0
                v.connector = "CHADEMO"
                v.incident = "No compatible depot connector; assistance required"
            if i == 1:
                v.energy_kwh = 1
            if i == 2:
                v.deliveries[0].deadline = start + timedelta(minutes=5)
            if i == 5:
                v.energy_kwh = 3
            if i == 7:
                v.deliveries[0].service_minutes = 60
            if i == 9:
                v.health_fault = True
            if i == 11:
                v.deliveries[1].deadline = start + timedelta(minutes=100)
            event_type = {
                3: "DEPOT_POWER",
                4: "CHARGER_STATUS",
                6: "CONSUMPTION",
                7: "SERVICE_DELAY",
                8: "CONSUMPTION",
                10: "ENERGY_LOSS",
            }.get(i)
            if event_type:
                events.append(
                    dict(
                        event_id=f"event-{i}",
                        at=(
                            start
                            + timedelta(
                                minutes={3: 55, 4: 75, 6: 45, 7: 65, 8: 8, 10: 20}[i]
                            )
                        ).isoformat(),
                        kind=event_type,
                        trigger="TRAVELLING" if i == 10 else None,
                        vin=vin,
                        status="PENDING",
                        value={3: 35, 4: "FAILED", 6: 0.5, 7: 30, 8: 0.4, 10: 0}[i],
                        charger_id="SIM-C2",
                        depot_id=depot["depot_id"],
                    )
                )
        vehicles[vin] = v.model_dump(mode="json")
    return dict(
        schema_version=2,
        run_id=str(uuid4()),
        revision=0,
        scenario=str(request.scenario),
        seed=request.seed,
        start_time=start.isoformat(),
        clock=start.isoformat(),
        running=False,
        speed=30,
        # Legacy bulk fixtures keep their historical one-charge search so their
        # simultaneous scripted incidents remain deterministic and inexpensive.
        # The four current dashboard scenarios explicitly enable charger chains.
        policy=Policy(max_charging_stops_per_gap=1).model_dump(),
        vehicles=vehicles,
        stations=stations,
        depots={depot["depot_id"]: depot},
        plans={},
        events=events,
        history=[],
        jobs={},
        lease=None,
        external_bookings=[
            dict(
                plan_id=f"external-{port}",
                vin=f"SITE-{port}",
                charger_id="SIM-C1",
                port=port,
                start=start.isoformat(),
                end=(start + timedelta(minutes=20 if port == 1 else 35)).isoformat(),
                power_kw=60,
                cost=0,
                status="ACTIVE",
                plan_status="APPROVED",
            )
            for port in (1, 2)
        ]
        if str(request.scenario) == "EDGE_CASE_DAY"
        else [],
    )
