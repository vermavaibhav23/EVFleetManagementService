import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi import status as http_status
from pydantic import BaseModel
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.dependencies import get_database
from app.models.charger import Charger
from app.models.charging import ChargingPlan, ChargingPlanStatus, ChargingRecommendation
from app.models.reservation import ACTIVE_RESERVATION_STATUSES, Reservation
from app.models.tariff import Tariff
from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip
from app.models.vehicle import Vehicle
from app.services.coordination import serialized
from app.services.fleet_readiness import evaluate_vehicle_readiness, find_next_trip
from app.services.scheduler import create_recommendation, escape_km, haversine_km

router = APIRouter()


async def _load_recommendation(
    vin: str,
    ignore_plan_id: str | None = None,
    allow_delay: bool = False,
    reviewed_plan=None,
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
    if trip and allow_delay:
        trip = trip.model_copy(update={"accepted_delay": True})
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

    future_trips = [
        Trip(**d)
        async for d in db.trips.find(
            {"vin": vin, "status": "PLANNED", "simulation_enabled": {"$ne": False}}
        ).sort("departure_time", 1)
    ]
    if allow_delay:
        future_trips = [
            t.model_copy(update={"accepted_delay": True}) for t in future_trips
        ]
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
        future_trips=future_trips,
        reviewed_plan=reviewed_plan,
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
    recommendation = await _load_recommendation(
        plan_doc["vin"], ignore_plan_id=plan_id, reviewed_plan=ChargingPlan(**plan_doc)
    )
    if recommendation.plan is None:
        raise HTTPException(
            409,
            "Reviewed plan is no longer feasible. Refresh options. "
            + recommendation.reason,
        )
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
        "follow_up_stops",
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
            "This recommendation is stale. Refresh the options and review the updated timing before approving. No reservation was made.",
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
    for index, stop in enumerate(plan_doc.get("follow_up_stops", [])):
        future_id = f"{plan_id}-next-{index + 1}"
        future = ChargingPlan(
            plan_id=future_id,
            vin=plan_doc["vin"],
            trip_id=stop["trip_id"],
            charger_id=stop["charger_id"],
            port_number=stop["port_number"],
            start_time=stop["start_time"],
            end_time=stop["end_time"],
            starting_soc_pct=stop["starting_soc_pct"],
            target_soc_pct=stop["target_soc_pct"],
            energy_required_kwh=stop["grid_energy_kwh"] * settings.charging_efficiency,
            allocated_power_kw=stop["allocated_power_kw"],
            estimated_cost=stop["electricity_cost"],
            predicted_ready_time=stop["end_time"],
            active=False,
            delivery_deadline=(
                await db.trips.find_one({"trip_id": stop["trip_id"]})
            ).get("delivery_deadline"),
            remaining_delivery_km=stop["remaining_delivery_km"],
            accepted_delay=plan_doc.get("accepted_delay", False),
            parent_plan_id=plan_id,
            status="SCHEDULED",
            simulation_run_id=plan_doc["simulation_run_id"],
            reason="Later charging stop approved with the timetable.",
        )
        await db.charging_plans.update_one(
            {"plan_id": future_id},
            {"$set": future.model_dump(mode="python")},
            upsert=True,
        )
        booking = Reservation(
            plan_id=future_id,
            vin=future.vin,
            charger_id=future.charger_id,
            port_number=future.port_number,
            start_time=future.start_time,
            end_time=future.end_time,
            reserved_power_kw=future.allocated_power_kw,
        )
        await db.reservations.update_one(
            {"plan_id": future_id},
            {"$set": booking.model_dump(mode="python")},
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
    if status == ChargingPlanStatus.CANCELLED:
        from app.services.plan_lifecycle import release_plan_chain

        await release_plan_chain(
            db, plan_id, now, "Manager cancelled the charging plan"
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
    await db.charging_plans.update_many(
        {"plan_id": {"$regex": f"^{plan_id}-next-"}, "status": "SCHEDULED"},
        {"$set": {"status": "CANCELLED", "active": False}},
    )
    await db.reservations.update_many(
        {
            "plan_id": {"$regex": f"^{plan_id}-next-"},
            "status": {"$in": ["CONFIRMED", "VEHICLE_EN_ROUTE"]},
        },
        {"$set": {"status": "CANCELLED"}},
    )
    return ChargingPlan(**doc)


@router.post("/plans/{plan_id}/reject", response_model=ChargingPlan)
async def reject_plan(plan_id: str) -> ChargingPlan:
    return await _set_plan_status(plan_id, ChargingPlanStatus.REJECTED)


@router.post("/plans/{plan_id}/cancel", response_model=ChargingPlan)
async def cancel_plan(plan_id: str) -> ChargingPlan:
    return await _set_plan_status(plan_id, ChargingPlanStatus.CANCELLED)


class ManagerDecisionRequest(BaseModel):
    simulation_run_id: str
    trip_id: str
    telemetry_sequence: int
    decision_token: str | None = None


async def _decision_context(vin):
    db = get_database()
    vdoc = await db.vehicles.find_one({"vin": vin})
    edoc = await db.telemetry.find_one({"vin": vin}, sort=[("ts", -1), ("seq", -1)])
    if not vdoc or not edoc:
        raise HTTPException(404, "Vehicle telemetry unavailable")
    v, e = Vehicle(**vdoc), TelemetryEvent(**edoc)
    t = await find_next_trip(db, vin, e.ts)
    if not t:
        raise HTTPException(422, "No current delivery")
    future = [
        Trip(**d)
        async for d in db.trips.find(
            {
                "vin": vin,
                "status": "PLANNED",
                "trip_id": {"$ne": t.trip_id},
                "simulation_enabled": {"$ne": False},
            }
        ).sort("departure_time", 1)
    ]
    distance = haversine_km(e.lat, e.lon, t.destination_lat, t.destination_lon)
    capacity = (
        v.usable_capacity_kwh * (e.soh_pct if e.soh_pct is not None else 100) / 100
    )
    remaining = capacity * e.soc_pct / 100 - distance * v.consumption_kwh_per_km
    eta = e.ts + timedelta(hours=distance / 35)
    can_deliver = remaining > 0 and (
        not t.delivery_deadline
        or eta + timedelta(minutes=settings.charging_deadline_buffer_minutes)
        <= t.delivery_deadline
    )
    replacement = None
    if can_deliver and future:
        async for vd in db.vehicles.find(
            {"depot_id": v.depot_id, "vin": {"$ne": vin}, "active": True}
        ).sort("vin", 1):
            if await db.trips.find_one(
                {
                    "vin": vd["vin"],
                    "status": {"$in": ["PLANNED", "IN_PROGRESS"]},
                    "simulation_enabled": {"$ne": False},
                }
            ):
                continue
            if await db.charging_plans.find_one({"vin": vd["vin"], "active": True}):
                continue
            ev = await db.telemetry.find_one(
                {"vin": vd["vin"]}, sort=[("ts", -1), ("seq", -1)]
            )
            if not ev or ev.get("simulation_run_id") != e.simulation_run_id:
                continue
            other = Vehicle(**vd)
            if (ev.get("battery_temperature_c") or 31) >= 45 or ev.get("dtc"):
                continue
            collection = haversine_km(
                ev["lat"], ev["lon"], t.destination_lat, t.destination_lon
            )
            # Travel begins when source delivery has completed, then a physical package handover.
            moment = eta + timedelta(
                minutes=t.service_duration_minutes + collection / 35 * 60 + 4
            )
            origin = (t.destination_lat, t.destination_lon)
            km = collection
            works = True
            for leg in future:
                moment = max(moment, leg.departure_time)
                d = haversine_km(*origin, leg.destination_lat, leg.destination_lon)
                km += d
                moment += timedelta(hours=d / 35)
                if (
                    leg.delivery_deadline
                    and moment
                    + timedelta(minutes=settings.charging_deadline_buffer_minutes)
                    > leg.delivery_deadline
                ):
                    works = False
                    break
                moment += timedelta(minutes=leg.service_duration_minutes)
                origin = (leg.destination_lat, leg.destination_lon)
            energy = (
                other.usable_capacity_kwh
                * (ev.get("soh_pct") or 100)
                / 100
                * ev["soc_pct"]
                / 100
            )
            if (
                works
                and energy
                >= (km + settings.reserve_range_km) * other.consumption_kwh_per_km
            ):
                replacement = {
                    "vin": other.vin,
                    "name": other.name,
                    "collection_km": round(collection, 2),
                    "lat": ev["lat"],
                    "lon": ev["lon"],
                }
                break
    delay = await _load_recommendation(vin, allow_delay=True)
    compatible = [
        Charger(**d) async for d in db.chargers.find({"depot_id": v.depot_id})
    ]
    needed_after = settings.reserve_range_km + escape_km(v, t, future, compatible)
    allowed_direct = (
        can_deliver
        and (not future or replacement is not None)
        and remaining < needed_after * v.consumption_kwh_per_km
    )
    original = await _load_recommendation(vin)
    token = hashlib.sha256(
        json.dumps(
            {
                "run": e.simulation_run_id,
                "trip": t.trip_id,
                "soc": e.soc_pct,
                "lat": e.lat,
                "lon": e.lon,
                "state": e.operating_state,
                "replacement": replacement and replacement["vin"],
                "direct": allowed_direct,
                "delay": delay.plan is not None,
                "deadline": str(t.delivery_deadline),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "decision_token": token,
        "simulation_run_id": e.simulation_run_id,
        "trip_id": t.trip_id,
        "telemetry_sequence": e.seq,
        "is_final": not future,
        "normal_plan_feasible": original.plan is not None,
        "arrival_soc_pct": round(remaining / capacity * 100, 1),
        "delivery_eta": eta.isoformat(),
        "deliver_now_available": allowed_direct,
        "replacement": replacement,
        "remaining_deliveries": len(future),
        "delay_available": delay.plan is not None,
        "delay_scope": len(future) + 1,
        "delay_plan": delay.plan.model_dump(mode="json") if delay.plan else None,
        "reason": "Direct delivery leaves insufficient reserve or no safe continuation. Recovery will be requested."
        if allowed_direct
        else "Direct delivery cannot satisfy the arrival or reassignment checks.",
    }, (v, e, t, future)


@router.get("/manager-decisions/{vin}")
async def manager_decision_preview(vin: str):
    preview, _ = await _decision_context(vin)
    return preview


@router.post("/manager-decisions/{vin}/{choice}")
@serialized
async def manager_decision(vin: str, choice: str, request: ManagerDecisionRequest):
    if choice not in {"accept-delay", "deliver-now"}:
        raise HTTPException(422, "Unknown manager decision")
    preview, (v, e, t, future) = await _decision_context(vin)
    stale = (request.simulation_run_id, request.trip_id) != (
        e.simulation_run_id,
        t.trip_id,
    )
    stale = stale or (
        request.decision_token != preview["decision_token"]
        if request.decision_token
        else request.telemetry_sequence != e.seq
    )
    if stale:
        raise HTTPException(
            409,
            "Vehicle conditions changed. Refresh the manager choices before deciding.",
        )
    db = get_database()
    existing = await db.manager_decisions.find_one(
        {"vin": vin, "trip_id": t.trip_id, "simulation_run_id": e.simulation_run_id},
        sort=[("created_at", -1)],
    )
    if existing:
        previous_plan = await db.charging_plans.find_one(
            {"plan_id": existing.get("plan_id")}
        )
        if (
            existing["choice"] != "accept-delay"
            or not previous_plan
            or previous_plan["status"] != "CANCELLED"
        ):
            raise HTTPException(
                409, "A manager decision has already been recorded for this delivery."
            )
    if await db.charging_plans.find_one(
        {"vin": vin, "status": {"$in": ["APPROVED", "CHARGING"]}}
    ):
        raise HTTPException(
            409,
            "Cancel the active charging plan before changing the delivery decision.",
        )
    if choice == "accept-delay":
        if not preview["delay_available"]:
            raise HTTPException(
                422, "No feasible charging continuation exists even with this delay."
            )
        await db.trips.update_many(
            {"vin": vin, "status": {"$in": ["PLANNED", "IN_PROGRESS"]}},
            {"$set": {"accepted_delay": True}},
        )
    else:
        if not preview["deliver_now_available"]:
            raise HTTPException(422, preview["reason"])
        replacement = preview["replacement"]
        if future:
            pickup_id = f"SIM-HANDOVER-{uuid4()}"
            pickup = Trip(
                trip_id=pickup_id,
                vin=replacement["vin"],
                origin="Standby location",
                destination=f"Package handover at {t.destination}",
                origin_lat=replacement["lat"],
                origin_lon=replacement["lon"],
                destination_lat=t.destination_lat,
                destination_lon=t.destination_lon,
                departure_time=e.ts,
                delivery_deadline=future[0].delivery_deadline,
                distance_km=max(0.001, replacement["collection_km"]),
                service_duration_minutes=4,
                handover_trip_id=t.trip_id,
                transfer_from_vin=vin,
            )
            await db.trips.insert_one(pickup.model_dump(mode="python"))
            for i, leg in enumerate(future):
                await db.trips.update_one(
                    {"trip_id": leg.trip_id},
                    {
                        "$set": {
                            "vin": replacement["vin"],
                            "transfer_from_vin": vin,
                            "handover_trip_id": pickup_id,
                            "sequence": i + 2,
                        }
                    },
                )
        await db.trips.update_one(
            {"trip_id": t.trip_id},
            {"$set": {"reserve_exception": True, "recovery_requested": True}},
        )
    await db.charging_plans.update_many(
        {"vin": vin, "status": "PROPOSED"},
        {"$set": {"status": "CANCELLED", "active": False}},
    )
    decision = {
        "decision_id": str(uuid4()),
        "vin": vin,
        "trip_id": t.trip_id,
        "simulation_run_id": e.simulation_run_id,
        "choice": choice,
        "created_at": e.ts,
        "arrival_soc_pct": preview["arrival_soc_pct"],
        "replacement": preview["replacement"] if choice == "deliver-now" else None,
        "recovery_status": "REQUESTED" if choice == "deliver-now" else None,
        "reason": "Original deadline retained; charging delay accepted."
        if choice == "accept-delay"
        else "Priority delivery approved; recovery requested and remaining work reassigned where applicable.",
    }
    if choice == "accept-delay":
        try:
            charge_plan = await create_plan(vin, Response())
            approved = await approve_plan(charge_plan.plan_id)
            decision["plan_id"] = approved.plan_id
        except Exception:
            for previous in [t] + future:
                await db.trips.update_one(
                    {"trip_id": previous.trip_id},
                    {"$set": {"accepted_delay": previous.accepted_delay}},
                )
            if "charge_plan" in locals():
                from app.services.plan_lifecycle import release_plan_chain

                await release_plan_chain(
                    db,
                    charge_plan.plan_id,
                    e.ts,
                    "Manager approval could not be completed",
                )
            raise
    await db.manager_decisions.insert_one(dict(decision))
    return decision
