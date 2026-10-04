"""Deterministic vehicle devices, separate from the service's observed state.

The executor only changes this checkpoint. Durable readings travel through the
provider's HTTP forwarder and Kafka before the service sees their progress.
"""

from copy import deepcopy
from datetime import UTC, datetime

from fastapi import HTTPException

from app.domain import dt
from app.services.execution import advance
from app.services.ledger import Ledger


def caught_up(doc, simulator):
    return all(
        v["sequence"] >= simulator["vehicles"][vin]["sequence"]
        for vin, v in doc["vehicles"].items()
    )


async def emit(connection, simulator):
    for vin, v in simulator["vehicles"].items():
        v["sequence"] += 1
        event_id = f"{simulator['run_id']}:{vin}:{v['sequence']}"
        reading = dict(
            event_id=event_id,
            run_id=simulator["run_id"],
            vehicle_id=vin,
            sequence=v["sequence"],
            observed_at=simulator["clock"],
            lat=v["lat"],
            lon=v["lon"],
            energy_kwh=v["energy_kwh"],
            activity=v["state"],
            temperature_c=v["temperature_c"],
            health_fault=v["health_fault"],
            control_version=v.get("control_version", 0),
        )
        checkpoint = dict(
            vehicle=deepcopy(v),
            clock=simulator["clock"],
            plans={k: p for k, p in simulator["plans"].items() if p["vin"] == vin},
            events=simulator["events"],
            history=[h for h in simulator["history"] if h.get("vin") == vin],
        )
        await connection.execute(
            "INSERT INTO outbox(id,run_id,kind,message_key,payload,checkpoint) "
            "VALUES($1,$2,'TELEMETRY',$3,$4,$5) ON CONFLICT DO NOTHING",
            event_id,
            simulator["run_id"],
            vin,
            reading,
            checkpoint,
        )
    await connection.execute(
        "INSERT INTO simulator_state(run_id,data) VALUES($1,$2) ON CONFLICT(run_id) DO UPDATE SET data=EXCLUDED.data",
        simulator["run_id"],
        simulator,
    )


async def initialize_simulator(connection, doc):
    simulator = deepcopy(doc)
    simulator["jobs"] = {}
    await emit(connection, simulator)


async def sync_commands(connection, previous, updated):
    """Approval/incident changes are durable device instructions, not observations.

    Telemetry consumers bypass this function. Resource and manager commands retain
        execution already reported; replacing a mission resets only its new pointer.
    """
    simulator = await connection.fetchval(
        "SELECT data FROM simulator_state WHERE run_id=$1", updated["run_id"]
    )
    if not simulator:
        return
    new_jobs = updated["jobs"].keys() - previous["jobs"].keys()
    approvals = any(
        p["status"] == "APPROVED"
        and previous["plans"].get(key, {}).get("status") != "APPROVED"
        for key, p in updated["plans"].items()
    )
    if (new_jobs or approvals) and not caught_up(previous, simulator):
        raise HTTPException(
            409,
            "Waiting for telemetry processing. Retry after the live state catches up.",
        )
    changed = False
    for key in ("stations", "depots", "policy", "events", "scenario_actions"):
        if previous.get(key) != updated.get(key):
            simulator[key] = deepcopy(updated.get(key))
            changed = True
    for vin, v in updated["vehicles"].items():
        old = previous["vehicles"][vin]
        # Ignore display, planning and telemetry metadata when issuing commands.
        fields = (
            "plan_id",
            "state",
            "energy_kwh",
            "lat",
            "lon",
            "service_until",
            "release_until",
            "incident",
            "health_fault",
            "temperature_c",
            "consumption_kwh_km",
            "deliveries",
        )
        if any(old.get(k) != v.get(k) for k in fields):
            if simulator["vehicles"][vin]["sequence"] > old["sequence"]:
                raise HTTPException(
                    409,
                    "Vehicle has unread telemetry. Wait for its latest state before changing instructions.",
                )
            v["control_version"] = old.get("control_version", 0) + 1
            v["sequence"] = max(v["sequence"], simulator["vehicles"][vin]["sequence"])
            simulator["vehicles"][vin] = deepcopy(v)
            changed = True
    for key, plan in updated["plans"].items():
        if plan["status"] != "PROPOSED" and plan != previous["plans"].get(key):
            simulator["plans"][key] = deepcopy(plan)
            changed = True
    if changed:
        simulator["clock"] = updated["clock"]
        await emit(connection, simulator)


class Simulator:
    def __init__(self, pool):
        self.store = Ledger(pool)

    async def step(self, run_id, seconds, manual=False):
        async with self.store.pool.acquire() as connection, connection.transaction():
            doc = await self.store.read_on(connection, lock=True)
            if doc["run_id"] != run_id:
                raise HTTPException(409, "This action belongs to an old simulation run")
            if manual and doc["running"]:
                raise HTTPException(409, "Pause the automatic clock before stepping")
            simulator = await connection.fetchval(
                "SELECT data FROM simulator_state WHERE run_id=$1", run_id
            )
            if not caught_up(doc, simulator):
                raise HTTPException(
                    409,
                    "Waiting for telemetry processing. Retry after the live state catches up.",
                )
            # Backpressure prevents a bounded demo from racing ahead during an outage.
            advance(simulator, seconds)
            await emit(connection, simulator)
            return {"clock": simulator["clock"], "status": "TELEMETRY_PENDING"}

    async def heartbeat(self):
        async with self.store.pool.acquire() as connection, connection.transaction():
            doc = await self.store.read_on(connection, lock=True)
            simulator = await connection.fetchval(
                "SELECT data FROM simulator_state WHERE run_id=$1", doc["run_id"]
            )
            if caught_up(doc, simulator):
                await emit(connection, simulator)


def fresh(doc, vehicle, max_age):
    if not vehicle.get("observed_at") or not vehicle.get("received_at"):
        return False
    # Scenario clocks are intentionally in simulated time, including accelerated runs.
    simulation_age = (dt(doc["clock"]) - dt(vehicle["observed_at"])).total_seconds()
    wall_age = (datetime.now(UTC) - dt(vehicle["received_at"])).total_seconds()
    return -1 <= simulation_age <= max_age and wall_age <= max_age
