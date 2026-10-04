import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import get_database
from app.domain import (
    Approval,
    ClockAction,
    LoadRequest,
    PlanRequest,
    ResourceChange,
    Telemetry,
    dt,
    effective_capacity,
)
from app.services.control import approve_or_refresh, reject, request_job, telemetry
from app.services.execution import advance, apply_event, interrupt
from app.services.ledger import Ledger, bookings
from app.services.plan_review import result_message, review
from app.services.pricing import intervals
from app.services.scenario_groups import action_block, trigger_action
from app.services.seed import seed

router = APIRouter()


def ledger(db=Depends(get_database)):
    return Ledger(db)


@router.get("/health/live")
async def live():
    return {"status": "ok", "schema_version": 2}


@router.get("/health/ready")
async def ready(db=Depends(get_database)):
    await db.command("ping")
    return {"status": "ready", "authority": "MongoDB atomic run ledger"}


@router.get("/fleet")
@router.get("/simulator/state")
async def state(store: Ledger = Depends(ledger)):
    doc = await store.read()
    doc.pop("_id", None)
    doc["reservations"] = bookings(doc) + doc.get("external_bookings", [])
    for item in doc.get("scenario_actions", []):
        item["blocked_reason"] = action_block(doc, item)
    for plan in doc["plans"].values():
        plan["review"] = review(doc, plan)
    for job in doc["jobs"].values():
        for row in job.get("results", []):
            row["message"] = result_message(doc, row)
    return doc


@router.post("/simulator/load")
async def load(request: LoadRequest, store: Ledger = Depends(ledger)):
    doc = await store.replace_run(await asyncio.to_thread(seed, request))
    return {
        "run_id": doc["run_id"],
        "scenario": doc["scenario"],
        "clock": doc["clock"],
        "vehicle_count": len(doc["vehicles"]),
    }


@router.post("/simulator/actions/{action_id}")
async def scenario_action(
    action_id: str, request: Approval, store: Ledger = Depends(ledger)
):
    return await store.mutate(
        lambda doc: trigger_action(doc, action_id), request.run_id
    )


@router.post("/simulator/start")
async def start(request: ClockAction, store: Ledger = Depends(ledger)):
    def action(doc):
        doc.update(running=True, speed=request.speed)
        return {"running": True, "speed": request.speed}

    return await store.mutate(action, request.run_id)


@router.post("/simulator/pause")
async def pause(request: ClockAction, store: Ledger = Depends(ledger)):
    def action(doc):
        doc["running"] = False
        return {"running": False}

    return await store.mutate(action, request.run_id)


@router.post("/simulator/tick")
async def tick(request: ClockAction, store: Ledger = Depends(ledger)):
    def action(doc):
        if doc["running"]:
            raise HTTPException(409, "Pause the automatic clock before stepping")
        advance(doc, request.seconds)
        return {"clock": doc["clock"]}

    return await store.mutate(action, request.run_id)


@router.post("/journeys/plan")
async def plan_fleet(request: PlanRequest, store: Ledger = Depends(ledger)):
    return await store.mutate(lambda d: request_job(d, None, request))


@router.post("/vehicles/{vin}/journeys/plan")
async def plan_vehicle(vin: str, request: PlanRequest, store: Ledger = Depends(ledger)):
    return await store.mutate(lambda d: request_job(d, vin, request))


@router.get("/jobs/{job_id}")
async def job(job_id: str, store: Ledger = Depends(ledger)):
    doc = await store.read()
    if job_id not in doc["jobs"]:
        raise HTTPException(404, "Job not found in this run")
    return doc["jobs"][job_id]


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Approval, store: Ledger = Depends(ledger)):
    def action(doc):
        if job_id not in doc["jobs"]:
            raise HTTPException(404, "Unknown job")
        doc["jobs"][job_id]["status"] = "CANCELLED"
        return {"status": "CANCELLED"}

    return await store.mutate(action, request.run_id)


@router.post("/journeys/{plan_id}/approve")
async def approve_plan(
    plan_id: str, request: Approval, store: Ledger = Depends(ledger)
):
    return await store.mutate(
        lambda d: approve_or_refresh(d, plan_id, request), request.run_id
    )


@router.post("/journeys/{plan_id}/reject")
async def reject_plan(plan_id: str, request: Approval, store: Ledger = Depends(ledger)):
    return await store.mutate(lambda d: reject(d, plan_id, request), request.run_id)


@router.post("/journeys/{plan_id}/cancel")
async def cancel_plan(plan_id: str, request: Approval, store: Ledger = Depends(ledger)):
    def action(doc):
        plan = doc["plans"].get(plan_id)
        if not plan:
            raise HTTPException(404, "Unknown journey")
        if plan["version"] != request.version:
            raise HTTPException(409, "Reviewed version changed")
        if plan["status"] == "PROPOSED":
            plan["status"] = "CANCELLED"
        elif doc["vehicles"][plan["vin"]].get("plan_id") == plan_id:
            interrupt(
                doc,
                plan["vin"],
                "Manager cancelled journey; physical release is retained",
            )
        return {"status": plan["status"]}

    return await store.mutate(action, request.run_id)


@router.post("/telemetry")
async def receive_telemetry(request: Telemetry, store: Ledger = Depends(ledger)):
    def action(doc):
        v = doc["vehicles"].get(request.vin)
        if not v:
            raise HTTPException(404, "Unknown vehicle")
        if request.energy_kwh > effective_capacity(v):
            raise HTTPException(422, "Telemetry energy exceeds effective capacity")
        return telemetry(doc, request.model_dump())

    return await store.mutate(action, request.run_id)


@router.put("/resources/{resource_id}")
async def resource(
    resource_id: str, request: ResourceChange, store: Ledger = Depends(ledger)
):
    def action(doc):
        if request.power_limit_kw is not None:
            if resource_id not in doc["depots"]:
                raise HTTPException(404, "Unknown depot")
            apply_event(
                doc,
                dict(
                    kind="DEPOT_POWER",
                    depot_id=resource_id,
                    value=request.power_limit_kw,
                ),
            )
        elif request.station:
            if (
                resource_id not in doc["stations"]
                or request.station.charger_id != resource_id
            ):
                raise HTTPException(404, "Unknown charger")
            if request.station.depot_id not in doc["depots"]:
                raise HTTPException(422, "Unknown depot")
            if any(
                r["charger_id"] == resource_id
                and r["port"] > request.station.port_count
                and dt(r["start"]) <= dt(doc["clock"]) < dt(r["end"])
                for r in bookings(doc) + doc.get("external_bookings", [])
            ):
                raise HTTPException(
                    409, "An occupied port must be physically released before removal"
                )
            for v in doc["vehicles"].values():
                if v.get("plan_id") and any(
                    o.get("charger_id") == resource_id
                    and o["status"] in ("ACTIVE", "PLANNED")
                    for o in doc["plans"][v["plan_id"]]["operations"]
                ):
                    interrupt(doc, v["vin"], "Charger configuration changed")
            doc["stations"][resource_id] = request.station.model_dump(
                mode="json", by_alias=True
            )
        else:
            raise HTTPException(422, "Supply station or depot power limit")
        return {"updated": resource_id}

    return await store.mutate(action, request.run_id)


@router.get("/day-view")
async def day_view(
    day: str | None = Query(default=None), store: Ledger = Depends(ledger)
):
    doc = await store.read()
    zone = ZoneInfo("Asia/Kolkata")
    try:
        date = (
            datetime.fromisoformat(day).date()
            if day
            else dt(doc["clock"]).astimezone(zone).date()
        )
    except ValueError as exc:
        raise HTTPException(422, "Use YYYY-MM-DD") from exc
    start = datetime.combine(date, datetime.min.time(), zone)
    end = start + timedelta(days=1)
    rates = {
        sid: [
            {"start": a, "end": b, "price": p}
            for a, b, p in intervals(
                start, end, s, doc["depots"][s["depot_id"]].get("tariffs", [])
            )
        ]
        for sid, s in doc["stations"].items()
    }
    rows = []
    for booking in doc.get("external_bookings", []):
        if dt(booking["end"]) > start and dt(booking["start"]) < end:
            row = deepcopy(booking)
            row["status"] = (
                "COMPLETED"
                if dt(row["end"]) <= dt(doc["clock"])
                else "ACTIVE"
                if dt(row["start"]) <= dt(doc["clock"])
                else "PLANNED"
            )
            rows.append(row)
    for plan in doc["plans"].values():
        for op in plan["operations"]:
            if (
                op["kind"] == "CHARGE"
                and dt(op["end"]) > start
                and dt(op["start"]) < end
            ):
                rows.append(
                    {
                        **deepcopy(op),
                        "plan_id": plan["plan_id"],
                        "vin": plan["vin"],
                        "plan_status": plan["status"],
                        "was_approved": bool(plan.get("approved_at")),
                    }
                )
    return {
        "day": date.isoformat(),
        "timezone": "Asia/Kolkata",
        "start": start,
        "end": end,
        "clock": doc["clock"],
        "tariffs": rates,
        "bookings": rows,
        "stations": doc["stations"],
    }
