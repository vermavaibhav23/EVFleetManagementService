from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi import status as http_status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.dependencies import get_database
from app.models.charger import Charger
from app.models.charging import ChargingPlan, ChargingPlanStatus, ChargingRecommendation
from app.models.reservation import ACTIVE_RESERVATION_STATUSES, Reservation
from app.models.tariff import Tariff
from app.models.telemetry import TelemetryEvent
from app.models.vehicle import Vehicle
from app.services.coordination import serialized
from app.services.fleet_readiness import evaluate_vehicle_readiness, find_next_trip
from app.services.scheduler import create_recommendation

router = APIRouter()


async def _load_recommendation(
    vin: str, ignore_plan_id: str | None = None
) -> ChargingRecommendation:
    db = get_database()
    vehicle_doc = await db.vehicles.find_one({"vin": vin})
    telemetry_doc = await db.telemetry.find_one(
        {"vin": vin}, sort=[("ts", -1), ("seq", -1)]
    )
    if vehicle_doc is None or telemetry_doc is None:
        raise HTTPException(status_code=404, detail="Vehicle or telemetry not found")
    vehicle_doc.pop("_id", None)
    telemetry_doc.pop("_id", None)
    telemetry_doc.pop("ingested_at", None)
    vehicle = Vehicle(**vehicle_doc)
    telemetry = TelemetryEvent(**telemetry_doc)
    trip = await find_next_trip(db, vin, telemetry.ts)
    readiness = await evaluate_vehicle_readiness(db, vin, telemetry)
    if readiness is None:
        raise HTTPException(
            status_code=404, detail="Unable to evaluate vehicle readiness"
        )

    chargers: list[Charger] = []
    charger_query = (
        {"depot_id": vehicle.depot_id, "charger_id": {"$regex": "^SIM-CHARGER-"}}
        if vin.startswith("SIM")
        else {}
    )
    async for doc in db.chargers.find(charger_query):
        doc.pop("_id", None)
        chargers.append(Charger(**doc))
    reservations: list[Reservation] = []
    async for doc in db.reservations.find(
        {"status": {"$in": [status.value for status in ACTIVE_RESERVATION_STATUSES]}}
    ):
        doc.pop("_id", None)
        if doc.get("plan_id") != ignore_plan_id or ignore_plan_id is None:
            reservations.append(Reservation(**doc))
    tariffs: list[Tariff] = []
    async for doc in db.tariffs.find(
        {"tariff_id": {"$regex": "^SIM-"}} if vin.startswith("SIM") else {}
    ):
        doc.pop("_id", None)
        tariffs.append(Tariff(**doc))
    depot_power_limits = {
        doc["depot_id"]: float(doc["power_limit_kw"])
        async for doc in db.depots.find({})
    }

    return create_recommendation(
        vehicle,
        telemetry,
        trip,
        readiness,
        chargers,
        reservations,
        tariffs,
        depot_power_limits=depot_power_limits,
        reserve_range_km=settings.reserve_range_km,
        charge_soon_margin_km=settings.charge_soon_margin_km,
        deadline_buffer_minutes=settings.charging_deadline_buffer_minutes,
        slot_minutes=settings.scheduler_slot_minutes,
        charging_efficiency=settings.charging_efficiency,
        now=telemetry.ts,
    )


@router.get("/recommendations/{vin}", response_model=ChargingRecommendation)
async def recommend_charger(vin: str) -> ChargingRecommendation:
    return await _load_recommendation(vin)


@router.post("/plans/{vin}", response_model=ChargingPlan, status_code=201)
@serialized
async def create_plan(vin: str, response: Response) -> ChargingPlan:
    db = get_database()
    existing = await db.charging_plans.find_one(
        {"vin": vin, "status": {"$in": ["PROPOSED", "APPROVED", "CHARGING"]}}
    )
    if existing:
        existing.pop("_id", None)
        response.status_code = http_status.HTTP_200_OK
        return ChargingPlan(**existing)
    recommendation = await _load_recommendation(vin)
    if recommendation.plan is None:
        raise HTTPException(status_code=422, detail=recommendation.reason)
    try:
        await db.charging_plans.insert_one(
            recommendation.plan.model_dump(mode="python")
        )
    except DuplicateKeyError:
        existing = await db.charging_plans.find_one(
            {"vin": vin, "active": True}, sort=[("created_at", -1)]
        )
        if existing is None:
            raise
        existing.pop("_id", None)
        response.status_code = http_status.HTTP_200_OK
        return ChargingPlan(**existing)
    return recommendation.plan


@router.get("/plans", response_model=list[ChargingPlan])
async def list_plans(
    vin: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[ChargingPlan]:
    db = get_database()
    query = {"vin": vin} if vin else {}
    plans: list[ChargingPlan] = []
    async for doc in db.charging_plans.find(query).sort("created_at", -1).limit(limit):
        doc.pop("_id", None)
        plans.append(ChargingPlan(**doc))
    return plans


@router.post("/plans/{plan_id}/approve", response_model=ChargingPlan)
@serialized
async def approve_plan(plan_id: str) -> ChargingPlan:
    db = get_database()
    plan_doc = await db.charging_plans.find_one({"plan_id": plan_id})
    if plan_doc is None:
        raise HTTPException(status_code=404, detail="Charging plan not found")
    if plan_doc.get("status") in {"APPROVED", "CHARGING", "COMPLETED"}:
        return ChargingPlan(**plan_doc)
    if plan_doc.get("status") != "PROPOSED":
        raise HTTPException(422, "This plan is closed. Generate a new plan.")
    recommendation = await _load_recommendation(plan_doc["vin"], ignore_plan_id=plan_id)
    if recommendation.plan is None:
        raise HTTPException(422, recommendation.reason)
    refreshed = recommendation.plan.model_dump(mode="python")
    reviewed_fields = (
        "charger_id",
        "port_number",
        "start_time",
        "end_time",
        "target_soc_pct",
        "estimated_cost",
        "simulation_run_id",
        "trip_id",
        "delivery_deadline",
    )

    def normalized(value):
        # MongoDB stores datetimes at millisecond precision.
        if isinstance(value, datetime):
            return value.replace(microsecond=value.microsecond // 1000 * 1000)
        return value

    if any(
        normalized(plan_doc.get(key)) != normalized(refreshed.get(key))
        for key in reviewed_fields
    ):
        raise HTTPException(
            409,
            "This recommendation is stale. Pause the simulation, reject this proposal, and compare fresh options. No reservation was made and no alternative was substituted.",
        )
    refreshed["plan_id"] = plan_id
    refreshed["created_at"] = plan_doc["created_at"]
    plan_doc = refreshed
    start_time, end_time = plan_doc["start_time"], plan_doc["end_time"]
    travel_distance_km = plan_doc["travel_distance_km"]
    estimated_arrival_time = plan_doc["estimated_arrival_time"]
    reservation = Reservation(
        charger_id=plan_doc["charger_id"],
        port_number=plan_doc["port_number"],
        vin=plan_doc["vin"],
        plan_id=plan_id,
        start_time=plan_doc["start_time"],
        end_time=plan_doc["end_time"],
        reserved_power_kw=plan_doc["allocated_power_kw"],
    )
    await db.reservations.update_one(
        {"plan_id": plan_id},
        {"$set": reservation.model_dump(mode="python")},
        upsert=True,
    )
    now = datetime.now(UTC)
    await db.charging_plans.update_one(
        {"plan_id": plan_id},
        {
            "$set": {
                **plan_doc,
                "status": ChargingPlanStatus.APPROVED.value,
                "start_time": start_time,
                "end_time": end_time,
                "predicted_ready_time": end_time,
                "travel_distance_km": travel_distance_km,
                "estimated_arrival_time": estimated_arrival_time,
                "updated_at": now,
            }
        },
    )
    await db.alerts.update_one(
        {"dedupe_key": f"{plan_doc['vin']}:TRIP_READINESS"},
        {"$set": {"status": "ACTION_SCHEDULED", "updated_at": now}},
    )
    plan_doc["status"] = ChargingPlanStatus.APPROVED.value
    plan_doc.pop("_id", None)
    return ChargingPlan(**plan_doc)


@serialized
async def _set_plan_status(plan_id: str, status: ChargingPlanStatus) -> ChargingPlan:
    db = get_database()
    now = datetime.now(UTC)
    existing = await db.charging_plans.find_one({"plan_id": plan_id})
    if existing is None:
        raise HTTPException(404, "Charging plan not found")
    if existing["status"] == status.value:
        return ChargingPlan(**existing)
    allowed = (
        {"PROPOSED"}
        if status == ChargingPlanStatus.REJECTED
        else {"PROPOSED", "APPROVED", "CHARGING"}
    )
    if existing["status"] not in allowed:
        raise HTTPException(
            422, "This plan is already closed; generate a new plan if needed"
        )
    doc = await db.charging_plans.find_one_and_update(
        {"plan_id": plan_id},
        {"$set": {"status": status.value, "active": False, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    await db.reservations.update_many(
        {"plan_id": plan_id}, {"$set": {"status": "CANCELLED", "updated_at": now}}
    )
    doc.pop("_id", None)
    return ChargingPlan(**doc)


@router.post("/plans/{plan_id}/reject", response_model=ChargingPlan)
async def reject_plan(plan_id: str) -> ChargingPlan:
    return await _set_plan_status(plan_id, ChargingPlanStatus.REJECTED)


@router.post("/plans/{plan_id}/cancel", response_model=ChargingPlan)
async def cancel_plan(plan_id: str) -> ChargingPlan:
    return await _set_plan_status(plan_id, ChargingPlanStatus.CANCELLED)
