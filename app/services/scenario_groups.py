"""Four focused demos. Live journeys are staged through the real planner/executor."""

from datetime import timedelta

from fastapi import HTTPException

from app.domain import Approval, Delivery, LoadRequest, dt
from app.services.control import approve
from app.services.execution import advance, apply_event, record
from app.services.geometry import point
from app.services.optimizer import optimize

GROUPS = {
    "EVERYDAY_CHOICES": (
        "1. Everyday charging choices",
        "Small expensive top-up, larger cheap charge; fast versus cheap; charge here; no charge needed.",
    ),
    "SHARED_CHARGERS": (
        "2. Shared chargers & disruptions",
        "Queue, charger failure and site power. Trigger one incident at a time; reload to replay.",
    ),
    "DELIVERY_DELAYS": (
        "3. Delivery delays & replanning",
        "Long unloading, extra consumption, a tight deadline and an unaffected van.",
    ),
    "ASSISTANCE_CASES": (
        "4. Assistance & impossible journeys",
        "Empty battery, road stranding, health fault and an impossible deadline. No assistance is dispatched.",
    ),
}


def grouped_seed(request):
    from app.services.seed import seed

    doc = seed(
        LoadRequest(
            scenario="NORMAL_DAY",
            vehicle_count=4,
            seed=request.seed,
            start_time=request.start_time,
        )
    )
    doc["scenario"] = str(request.scenario)
    doc["scenario_title"], doc["scenario_description"] = GROUPS[doc["scenario"]]
    start = dt(doc["clock"])
    earlier = start - timedelta(minutes=15)
    base = doc["depots"]["SIM-DEPOT"]

    def loc(km, bearing):
        return point(base["lat"], base["lon"], km, bearing)

    # Separate supplies: a failure/power cut at E or W does not affect the control.
    for i, s in enumerate(doc["stations"].values()):
        site = f"SITE-{i + 1}"
        doc["depots"][site] = dict(
            depot_id=site,
            name=s["name"] + " supply",
            lat=s["lat"],
            lon=s["lon"],
            power_limit_kw=120,
            tariffs=[],
        )
        s.update(depot_id=site, tariffs=[], port_count=2)
    doc["policy"].update(solver_seconds=8)
    doc["scenario_actions"] = []
    vs = list(doc["vehicles"].values())
    letter = dict(zip(GROUPS, "ABCD"))[doc["scenario"]]
    for i, v in enumerate(vs):
        v.update(
            **loc(8 + i * 3, 35 + i * 85),
            energy_kwh=18,
            case="",
            starting_context="",
            name=f"Van {letter}{i + 1}",
        )
        v["deliveries"] = [
            Delivery(
                trip_id=f"{v['vin']}-D{j + 1}",
                sequence=j + 1,
                name=f"Customer {chr(65 + i * 3 + j)}",
                **loc(12 + j * 4, 35 + i * 85 + j * 8),
                ready_at=earlier,
                accepts_at=earlier,
                deadline=start + timedelta(minutes=150 + j * 100),
                service_minutes=6,
            ).model_dump(mode="json")
            for j in range(3)
        ]

    def role(i, case, context):
        vs[i].update(case=case, starting_context=context)

    def action(id, label, kind, i, value, states=None, **kwargs):
        doc["scenario_actions"].append(
            dict(
                event_id=id,
                label=label,
                kind=kind,
                vin=vs[i]["vin"],
                value=value,
                allowed_states=states or [],
                status="READY",
                **kwargs,
            )
        )

    staged = []
    group = doc["scenario"]
    if group == "EVERYDAY_CHOICES":
        role(
            0,
            "SPLIT_CHARGING",
            "Customer A completed. Buy a small top-up at Premium, then more at Economy after Customer B.",
        )
        role(
            1,
            "FAST_VS_CHEAP",
            "At Customer D. The next deadline favours a fast charger over the cheaper slow one.",
        )
        role(
            2,
            "CHARGE_HERE",
            "At the west satellite charger, below reserve. Charge here before departure.",
        )
        role(
            3,
            "NO_CHARGE",
            "Customer E completed. Enough battery for remaining deliveries and depot return.",
        )
        a = vs[0]
        # Explicit demo road corridor, measured energy/time, used by planner AND validator.
        nodes = [
            ("A", 0),
            ("SIM-C1", 1),
            ("SIM-001-D2", 5),
            ("SIM-C2", 10),
            ("SIM-001-D3", 20),
            ("HOME-A", 30),
        ]
        places = {n: loc(35 - x, 40) for n, x in nodes}
        a.update(**places["A"], node="A", energy_kwh=5, depot_id="HOME-A")
        doc["depots"]["HOME-A"] = dict(
            base, depot_id="HOME-A", node="HOME-A", **places["HOME-A"]
        )
        doc["roads"] = {
            f"{n}>{m}": dict(energy=abs(x - y), minutes=abs(x - y) * 1.5)
            for n, x in nodes
            for m, y in nodes
        }
        a["deliveries"][0].update(
            **places["A"], status="COMPLETED", completed_at=earlier.isoformat()
        )
        for j in (1, 2):
            a["deliveries"][j].update(
                **places[f"SIM-001-D{j + 1}"], node=f"SIM-001-D{j + 1}"
            )
        for id, name, price in [
            ("SIM-C1", "Premium northeast", 20),
            ("SIM-C2", "Economy northeast", 10),
        ]:
            doc["stations"][id].update(
                **places[id], name=name, price=price, power_kw=60
            )
        fast, slow = doc["stations"]["SIM-C3"], doc["stations"]["SIM-C4"]
        fast.update(
            **loc(15, 135), name="South fast", price=22, power_kw=60, connector="TYPE2"
        )
        slow.update(
            **loc(15, 145),
            name="South slow economy",
            price=8,
            power_kw=7,
            connector="TYPE2",
        )
        b = vs[1]
        b.update(**loc(14, 135), energy_kwh=4, connector="TYPE2")
        b["deliveries"][0].update(
            **loc(25, 135), deadline=(start + timedelta(minutes=43)).isoformat()
        )
        west = dict(
            slow,
            charger_id="SIM-C5",
            depot_id="SITE-5",
            name="West satellite economy",
            power_kw=30,
        )
        west.update(**loc(18, 250))
        doc["stations"]["SIM-C5"] = west
        doc["depots"]["SITE-5"] = dict(
            base,
            depot_id="SITE-5",
            name="West satellite depot",
            lat=west["lat"],
            lon=west["lon"],
        )
        vs[2].update(lat=west["lat"], lon=west["lon"], energy_kwh=1, connector="TYPE2")
        vs[3].update(energy_kwh=40, connector="TYPE2")
        vs[3]["deliveries"][0].update(
            name="Customer E",
            lat=vs[3]["lat"],
            lon=vs[3]["lon"],
            status="COMPLETED",
            completed_at=earlier.isoformat(),
        )
        completed = dict(
            b["deliveries"][0],
            trip_id="SIM-002-D0",
            sequence=1,
            name="Customer D",
            lat=b["lat"],
            lon=b["lon"],
            status="COMPLETED",
            completed_at=earlier.isoformat(),
        )
        for i, delivery in enumerate(b["deliveries"]):
            delivery.update(sequence=i + 2, name=f"Customer {chr(69 + i)}")
        b["deliveries"].insert(0, completed)
    elif group == "SHARED_CHARGERS":
        role(
            0,
            "QUEUE_CONTENTION",
            "North approach. A confirmed customer occupies North economy; compare waiting and diverting.",
        )
        role(
            1,
            "CHARGER_FAILURE",
            "Already charging at East hub. Fail it now to test preserved energy and unplug time.",
        )
        role(
            2,
            "SITE_POWER",
            "Customer F near West hub. Another customer is charging on this site's supply.",
        )
        role(
            3,
            "UNAFFECTED_CONTROL",
            "Southwest route with enough battery. Independent of the disrupted sites.",
        )
        north, east, west = [doc["stations"][x] for x in ("SIM-C2", "SIM-C3", "SIM-C4")]
        north["port_count"] = 1
        vs[0].update(**point(north["lat"], north["lon"], 1, 0), energy_kwh=4)
        vs[1].update(lat=east["lat"], lon=east["lon"], energy_kwh=1)
        vs[1]["deliveries"][0].update(**loc(38, 130))
        vs[2].update(**point(west["lat"], west["lon"], 1, 40), energy_kwh=4)
        vs[3].update(energy_kwh=40)
        for station, minutes in ((north, 45), (west, 90)):
            doc["external_bookings"].append(
                dict(
                    plan_id="visitor-" + station["charger_id"],
                    vin="VISITOR",
                    charger_id=station["charger_id"],
                    port=1,
                    start=earlier.isoformat(),
                    end=(start + timedelta(minutes=minutes)).isoformat(),
                    power_kw=station["power_kw"],
                    cost=0,
                    status="ACTIVE",
                    plan_status="APPROVED",
                )
            )
        action(
            "fail-east",
            "Fail East charger while charging",
            "CHARGER_STATUS",
            1,
            "FAILED",
            ["CHARGING"],
            charger_id=east["charger_id"],
        )
        action(
            "fail-north",
            "Fail North charger before arrival",
            "CHARGER_STATUS",
            0,
            "FAILED",
            ["PARKED", "READY", "TRAVELLING", "WAITING_REVIEW"],
            charger_id=north["charger_id"],
        )
        action(
            "power-west",
            "Reduce West site power to 40 kW",
            "DEPOT_POWER",
            2,
            40,
            depot_id=west["depot_id"],
        )
        staged = [1, 3]
    elif group == "DELIVERY_DELAYS":
        role(
            0,
            "LONG_UNLOAD",
            "Unloading at Customer H. Extend this active service by 30 minutes.",
        )
        role(
            1,
            "EXTRA_CONSUMPTION",
            "Driving east. Increase consumption and review the remaining journey after stopping.",
        )
        role(
            2,
            "TIGHT_DEADLINE",
            "Customer K completed. The next customer is too far away for the tight deadline.",
        )
        role(
            3,
            "UNAFFECTED_CONTROL",
            "Central route with spare battery and time; unaffected by the other vans' incidents.",
        )
        vs[0]["deliveries"][0].update(
            lat=vs[0]["lat"], lon=vs[0]["lon"], name="Customer H", service_minutes=40
        )
        vs[1].update(energy_kwh=40)
        vs[1]["deliveries"][0].update(**loc(30, 100), name="Customer J")
        vs[2]["deliveries"][0].update(
            lat=vs[2]["lat"],
            lon=vs[2]["lon"],
            name="Customer K",
            status="COMPLETED",
            completed_at=earlier.isoformat(),
        )
        vs[2]["deliveries"][1].update(
            deadline=(start + timedelta(minutes=2)).isoformat()
        )
        vs[3].update(energy_kwh=40)
        action(
            "unload",
            "Extend unloading by 30 minutes",
            "SERVICE_DELAY",
            0,
            30,
            ["SERVICING"],
        )
        action(
            "consumption",
            "Increase driving consumption",
            "CONSUMPTION",
            1,
            0.5,
            ["TRAVELLING"],
        )
        staged = [0, 1, 3]
    else:
        role(
            0,
            "DEPOT_STRANDING",
            "Empty at the west satellite depot with no compatible connector. No assistance dispatched.",
        )
        role(
            1,
            "ROAD_STRANDING",
            "Driving northeast. Inject severe energy loss while moving to test stranding at the actual position.",
        )
        role(
            2,
            "HEALTH_FAULT",
            "Safely parked at Customer Q. Raise a health fault to block departure and charging.",
        )
        role(
            3,
            "IMPOSSIBLE_DEADLINE",
            "At Customer R with sufficient battery. Only the next delivery deadline is impossible.",
        )
        vs[0].update(
            lat=doc["stations"]["SIM-C4"]["lat"],
            lon=doc["stations"]["SIM-C4"]["lon"],
            depot_id="SITE-4",
            energy_kwh=0,
            connector="CHADEMO",
            incident="Battery empty; no compatible charger here. Arrange assistance. Nobody has been dispatched.",
        )
        vs[1].update(energy_kwh=40)
        vs[1]["deliveries"][0].update(**loc(32, 35), name="Customer P")
        vs[3].update(energy_kwh=40)
        vs[3]["deliveries"][0].update(
            deadline=(start + timedelta(minutes=2)).isoformat()
        )
        action(
            "strand",
            "Simulate severe battery loss",
            "ENERGY_LOSS",
            1,
            0,
            ["TRAVELLING"],
        )
        action(
            "health",
            "Raise a vehicle health fault",
            "HEALTH",
            2,
            True,
            ["PARKED", "WAITING_REVIEW", "READY"],
        )
        staged = [1]
    # Keep each site's map position aligned with its charger.
    for s in doc["stations"].values():
        doc["depots"][s["depot_id"]].update(lat=s["lat"], lon=s["lon"])
    if staged:
        doc["clock"] = earlier.isoformat()
        for i in staged:
            result = optimize(doc, vs[i]["vin"], fastest=True)
            if "plan" not in result:
                raise ValueError(
                    f"Could not stage demo vehicle {vs[i]['vin']}: {result['reason']}"
                )
            plan = result["plan"]
            doc["plans"][plan["plan_id"]] = plan
            approve(doc, plan["plan_id"], Approval(run_id=doc["run_id"]))
            plan["seeded_journey"] = True
        advance(doc, 900)
    record(
        doc,
        "DEMO_SETUP",
        "Four-vehicle snapshot loaded paused. Existing journeys are simulated prior approvals; new options still need approval.",
    )
    return doc


def action_block(doc, action):
    if action["status"] == "APPLIED":
        return "Already applied; reload this scenario to replay."
    v = doc["vehicles"][action["vin"]]
    if action["allowed_states"] and v["state"] not in action["allowed_states"]:
        return (
            "Available when vehicle is "
            + " / ".join(s.lower().replace("_", " ") for s in action["allowed_states"])
            + "."
        )
    if action["kind"] == "CHARGER_STATUS" and action["allowed_states"] == ["CHARGING"]:
        plan = doc["plans"].get(v.get("plan_id"), {})
        if not any(
            o.get("charger_id") == action["charger_id"] and o["status"] == "ACTIVE"
            for o in plan.get("operations", [])
        ):
            return "Vehicle must be charging at this station."
    return None


def trigger_action(doc, action_id):
    action = next(
        (a for a in doc.get("scenario_actions", []) if a["event_id"] == action_id), None
    )
    if not action:
        raise HTTPException(404, "Unknown scenario action")
    reason = action_block(doc, action)
    if reason:
        raise HTTPException(409, reason)
    apply_event(doc, action)
    doc["running"] = False
    return {
        "status": "APPLIED",
        "message": "Incident applied. Simulation paused for review; no assistance has been dispatched.",
    }
