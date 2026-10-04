"""Kafka observations update service state; they never approve journeys."""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

from fastapi import HTTPException

from app.core.config import settings
from app.domain import dt, effective_capacity
from app.services.execution import interrupt
from app.services.ledger import Ledger


def check_reading(doc, event):
    if event.run_id != doc["run_id"]:
        raise HTTPException(409, "Old-run telemetry rejected")
    v = doc["vehicles"].get(event.vehicle_id)
    if not v:
        raise HTTPException(404, "Unknown vehicle")
    if event.energy_kwh > effective_capacity(v) + 1e-6:
        raise HTTPException(422, "Telemetry energy exceeds effective capacity")
    if (event.observed_at - dt(doc["clock"])).total_seconds() > 86401:
        raise HTTPException(422, "Telemetry timestamp exceeds the demo clock horizon")
    return v


def apply_reading(doc, event, checkpoint=None):
    v = check_reading(doc, event)
    if event.sequence <= v["sequence"]:
        return {"accepted": False, "reason": "Duplicate or out-of-order telemetry"}
    if event.control_version != v.get("control_version", 0):
        return {
            "accepted": False,
            "reason": "Reading precedes replacement instructions",
        }
    if v.get("observed_at") and event.observed_at < dt(v["observed_at"]):
        return {"accepted": False, "reason": "Observation time moved backwards"}
    if checkpoint:
        # Checkpoints come from our durable simulator outbox, never from HTTP JSON.
        old_plan = v.get("plan_id")
        v.update(deepcopy(checkpoint["vehicle"]))
        for key, plan in checkpoint["plans"].items():
            if key in doc["plans"] and doc["plans"][key]["status"] in (
                "APPROVED",
                "EXECUTING",
                "INTERRUPTED",
            ):
                doc["plans"][key] = deepcopy(plan)
        if dt(checkpoint["clock"]) > dt(doc["clock"]):
            doc["clock"] = checkpoint["clock"]
        doc["events"] = deepcopy(checkpoint["events"])
        for item in checkpoint["history"]:
            if item not in doc["history"]:
                doc["history"].append(item)
        doc["history"] = doc["history"][-1000:]
        if old_plan and not v.get("plan_id") and v["state"] != "COMPLETED":
            doc["running"] = False
    else:
        v.update(
            lat=event.lat,
            lon=event.lon,
            energy_kwh=event.energy_kwh,
            temperature_c=event.temperature_c,
            health_fault=event.health_fault,
            reported_activity=event.activity,
        )
        # External observations cannot complete deliveries or release bookings.
        # Those require simulator progress in this demonstration.
        plan = doc["plans"].get(v.get("plan_id"))
        if plan and plan["status"] in ("APPROVED", "EXECUTING"):
            if event.health_fault or event.temperature_c >= 60:
                interrupt(doc, v["vin"], "Vehicle health blocks the approved journey.")
            else:
                remaining = plan["operations"][v["operation_index"] :]
                if remaining:
                    from app.services.optimizer import leg

                    needed, minutes = leg(doc, v, v, remaining[0])
                    if event.energy_kwh - needed < plan["reserve_kwh"] - 0.001:
                        interrupt(
                            doc,
                            v["vin"],
                            "Latest battery reading cannot safely reach the next approved stop.",
                        )
                    elif (
                        event.observed_at - dt(remaining[0]["arrival"])
                    ).total_seconds() + minutes * 60 > 1:
                        interrupt(
                            doc,
                            v["vin"],
                            "Latest position no longer fits the approved arrival time.",
                        )
    v.update(
        sequence=event.sequence,
        observed_at=event.observed_at.isoformat(),
        received_at=datetime.now(UTC).isoformat(),
        last_event_id=event.event_id,
    )
    v["telemetry_status"] = (
        "OUTDATED"
        if (dt(doc["clock"]) - event.observed_at).total_seconds()
        > settings.telemetry_max_age_seconds
        else "LIVE"
    )
    return {"accepted": True}


class TelemetryProcessor:
    def __init__(self, pool):
        self.store = Ledger(pool)

    async def process(self, event):
        async with self.store.pool.acquire() as connection, connection.transaction():
            previous = await self.store.read_on(connection, lock=True)
            check_reading(previous, event)
            body_hash = sha256(event.model_dump_json().encode()).hexdigest()
            receipt = await connection.fetchval(
                "SELECT body_hash FROM telemetry_receipts WHERE event_id=$1",
                event.event_id,
            )
            if receipt and receipt != body_hash:
                raise HTTPException(
                    422, "Event ID was already used for a different reading"
                )
            if receipt:
                return {"accepted": False, "reason": "Duplicate event_id"}
            doc = deepcopy(previous)
            row = await connection.fetchrow(
                "SELECT payload,checkpoint FROM outbox WHERE id=$1 AND kind='TELEMETRY'",
                event.event_id,
            )
            checkpoint = None
            if row:
                # Same ID with a different body is not a simulator reading.
                from app.domain import Telemetry

                if Telemetry.model_validate(row["payload"]) != event:
                    raise HTTPException(
                        422, "Event ID conflicts with a simulator reading"
                    )
                checkpoint = row["checkpoint"]
            result = apply_reading(doc, event, checkpoint)
            if result["accepted"]:
                doc["revision"] += 1
                if not checkpoint:
                    # Feed accepted device corrections back to the deterministic
                    # simulator so its next sample cannot undo a real correction.
                    from app.services.simulator import sync_commands

                    await sync_commands(connection, previous, doc)
                await self.store.write_on(
                    connection, previous, doc, sync_simulator=False
                )
            await connection.execute(
                "UPDATE outbox SET processed_at=now() WHERE id=$1", event.event_id
            )
            await connection.execute(
                "INSERT INTO telemetry_receipts(event_id,run_id,body_hash) VALUES($1,$2,$3)",
                event.event_id,
                event.run_id,
                body_hash,
            )
            return result
