"""All operational writers use the same run/revision compare-and-swap boundary."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from app.domain import dt
from app.services.execution import interrupt, record
from app.services.optimizer import fingerprint
from app.services.validation import validate


def approve(doc, plan_id, request):
    plan = doc["plans"].get(plan_id)
    if not plan:
        raise HTTPException(404, "Unknown journey")
    if request.run_id != doc["run_id"] or request.version != plan["version"]:
        raise HTTPException(409, "Run or reviewed version changed")
    if plan["status"] in ("APPROVED", "EXECUTING", "COMPLETED"):
        return {"plan_id": plan_id, "status": plan["status"], "idempotent": True}
    if plan["status"] != "PROPOSED":
        raise HTTPException(409, "Proposal is no longer available")
    if plan["recovery"] and not request.acknowledge_recovery:
        raise HTTPException(
            422, "Acknowledge the displayed lateness and reserve consequences"
        )
    if fingerprint(doc, plan["vin"]) != plan["fingerprint"] or dt(doc["clock"]) > dt(
        plan["valid_until"]
    ):
        raise HTTPException(409, "Proposal is stale; request a fresh journey")
    errors = validate(doc, plan)
    if errors:
        raise HTTPException(409, {"status": "VALIDATION_REJECTED", "errors": errors})
    vehicle = doc["vehicles"][plan["vin"]]
    old = doc["plans"].get(plan.get("replaces"))
    if old:
        if any(
            o["kind"] == "CHARGE" and o["status"] in ("ACTIVE", "RELEASING")
            for o in old["operations"]
        ):
            raise HTTPException(
                409,
                "Vehicle must physically release its current port before replacing its journey",
            )
        old["status"] = "SUPERSEDED"
        for o in old["operations"]:
            if o["status"] == "PLANNED":
                o["status"] = "CANCELLED"
    # Nothing becomes visible until the caller's single MongoDB CAS succeeds.
    plan.update(
        status="APPROVED",
        approved_at=doc["clock"],
        approved_wall_at=datetime.now(UTC),
        acknowledged_recovery=plan["recovery"],
    )
    vehicle.update(plan_id=plan_id, operation_index=0, incident=None)
    if vehicle["state"] != "SERVICING":
        vehicle["state"] = "READY"
    for other in doc["plans"].values():
        if (
            other["vin"] == plan["vin"]
            and other["status"] == "PROPOSED"
            and other["plan_id"] != plan_id
        ):
            other["status"] = "SUPERSEDED"
    record(
        doc,
        "APPROVED",
        "Complete journey and all port reservations committed",
        plan["vin"],
    )
    return {"plan_id": plan_id, "status": "APPROVED", "idempotent": False}


def request_job(doc, vin, request):
    if vin is not None and vin not in doc["vehicles"]:
        raise HTTPException(404, "Unknown vehicle")
    if any(j["status"] in ("QUEUED", "RUNNING") for j in doc["jobs"].values()):
        raise HTTPException(409, "A planner job is already active for this run")
    job_id = str(uuid4())
    doc["jobs"] = {k: v for k, v in list(doc["jobs"].items())[-19:]}
    doc["jobs"][job_id] = dict(
        job_id=job_id,
        run_id=doc["run_id"],
        vin=vin,
        request=request.model_dump(),
        status="QUEUED",
        created_at=datetime.now(UTC),
    )
    return {"job_id": job_id, "status": "QUEUED"}


def telemetry(doc, request):
    if request["run_id"] != doc["run_id"]:
        raise HTTPException(409, "Old-run telemetry rejected")
    v = doc["vehicles"].get(request["vin"])
    if not v:
        raise HTTPException(404, "Unknown vehicle")
    if request["sequence"] <= v["sequence"]:
        return {"accepted": False, "reason": "Duplicate or out-of-order telemetry"}
    changed = any(
        v[k] != request[k] for k in ("energy_kwh", "temperature_c", "health_fault")
    )
    v.update(
        {
            k: request[k]
            for k in ("sequence", "energy_kwh", "temperature_c", "health_fault")
        }
    )
    if changed:
        interrupt(doc, v["vin"], "Fresh telemetry changed the reviewed state")
    return {"accepted": True}
