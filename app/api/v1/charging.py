from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pymongo import ReturnDocument

from app.core.config import settings
from app.core.dependencies import get_database
from app.models.charger import Charger
from app.models.charging import ChargingPlan, ChargingPlanStatus, ChargingRecommendation
from app.models.reservation import ACTIVE_RESERVATION_STATUSES, Reservation
from app.models.tariff import Tariff
from app.models.telemetry import TelemetryEvent
from app.models.vehicle import Vehicle
from app.services.fleet_readiness import evaluate_vehicle_readiness, find_next_trip
from app.services.scheduler import create_recommendation

router = APIRouter()


async def _load_recommendation(vin: str) -> ChargingRecommendation:
    db = get_database()
    vehicle_doc = await db.vehicles.find_one({"vin": vin})
    telemetry_doc = await db.telemetry.find_one({"vin": vin}, sort=[("ts", -1)])
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
    async for doc in db.chargers.find({}):
        doc.pop("_id", None)
        chargers.append(Charger(**doc))
    reservations: list[Reservation] = []
    async for doc in db.reservations.find(
        {"status": {"$in": [status.value for status in ACTIVE_RESERVATION_STATUSES]}}
    ):
        doc.pop("_id", None)
        reservations.append(Reservation(**doc))
    tariffs: list[Tariff] = []
    async for doc in db.tariffs.find({}):
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
    )


@router.get("/recommendations/{vin}", response_model=ChargingRecommendation)
async def recommend_charger(vin: str) -> ChargingRecommendation:
    return await _load_recommendation(vin)


@router.post("/plans/{vin}", response_model=ChargingPlan, status_code=201)
async def create_plan(vin: str) -> ChargingPlan:
    db = get_database()
    existing = await db.charging_plans.find_one(
        {"vin": vin, "status": {"$in": ["PROPOSED", "APPROVED", "CHARGING"]}}
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Vehicle already has an active {existing['status'].lower()} charging plan",
        )
    recommendation = await _load_recommendation(vin)
    if recommendation.plan is None:
        raise HTTPException(status_code=409, detail=recommendation.reason)
    await db.charging_plans.insert_one(recommendation.plan.model_dump(mode="python"))
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
async def approve_plan(plan_id: str) -> ChargingPlan:
    db = get_database()
    plan_doc = await db.charging_plans.find_one({"plan_id": plan_id})
    if plan_doc is None:
        raise HTTPException(status_code=404, detail="Charging plan not found")
    if plan_doc.get("status") != ChargingPlanStatus.PROPOSED.value:
        raise HTTPException(
            status_code=409, detail="Only proposed plans can be approved"
        )

    conflict = await db.reservations.find_one(
        {
            "charger_id": plan_doc["charger_id"],
            "port_number": plan_doc["port_number"],
            "status": {"$in": [status.value for status in ACTIVE_RESERVATION_STATUSES]},
            "start_time": {"$lt": plan_doc["end_time"]},
            "end_time": {"$gt": plan_doc["start_time"]},
        }
    )
    if conflict:
        raise HTTPException(
            status_code=409,
            detail="The selected charger interval is no longer available",
        )

    charger_doc = await db.chargers.find_one({"charger_id": plan_doc["charger_id"]})
    if charger_doc:
        depot = await db.depots.find_one({"depot_id": charger_doc.get("depot_id")})
        if depot:
            charger_ids = [
                doc["charger_id"]
                async for doc in db.chargers.find(
                    {"depot_id": charger_doc.get("depot_id")}, {"charger_id": 1}
                )
            ]
            reserved_power = 0.0
            cursor = db.reservations.find(
                {
                    "charger_id": {"$in": charger_ids},
                    "status": {
                        "$in": [status.value for status in ACTIVE_RESERVATION_STATUSES]
                    },
                    "start_time": {"$lt": plan_doc["end_time"]},
                    "end_time": {"$gt": plan_doc["start_time"]},
                }
            )
            async for existing_reservation in cursor:
                reserved_power += float(existing_reservation["reserved_power_kw"])
            if reserved_power + float(plan_doc["allocated_power_kw"]) > float(
                depot["power_limit_kw"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="The depot power limit is no longer available for this plan",
                )

    reservation = Reservation(
        charger_id=plan_doc["charger_id"],
        port_number=plan_doc["port_number"],
        vin=plan_doc["vin"],
        plan_id=plan_id,
        start_time=plan_doc["start_time"],
        end_time=plan_doc["end_time"],
        reserved_power_kw=plan_doc["allocated_power_kw"],
    )
    await db.reservations.insert_one(reservation.model_dump(mode="python"))
    now = datetime.now(UTC)
    await db.charging_plans.update_one(
        {"plan_id": plan_id},
        {"$set": {"status": ChargingPlanStatus.APPROVED.value, "updated_at": now}},
    )
    await db.alerts.update_one(
        {"dedupe_key": f"{plan_doc['vin']}:TRIP_READINESS"},
        {"$set": {"status": "ACTION_SCHEDULED", "updated_at": now}},
    )
    plan_doc["status"] = ChargingPlanStatus.APPROVED.value
    plan_doc.pop("_id", None)
    return ChargingPlan(**plan_doc)


async def _set_plan_status(plan_id: str, status: ChargingPlanStatus) -> ChargingPlan:
    db = get_database()
    now = datetime.now(UTC)
    doc = await db.charging_plans.find_one_and_update(
        {"plan_id": plan_id, "status": {"$in": ["PROPOSED", "APPROVED"]}},
        {"$set": {"status": status.value, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Mutable charging plan not found")
    if status == ChargingPlanStatus.CANCELLED:
        await db.reservations.update_one(
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
