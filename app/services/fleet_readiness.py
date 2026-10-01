from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.models.alert import AlertStatus
from app.models.charging import ReadinessAssessment, ReadinessStatus
from app.models.telemetry import TelemetryEvent
from app.models.trip import Trip, TripStatus
from app.models.vehicle import Vehicle
from app.services.readiness import assess_readiness


async def find_next_trip(db: Any, vin: str, now: datetime | None = None) -> Trip | None:
    now = now or datetime.now(UTC)
    doc = await db.trips.find_one(
        {"vin": vin, "status": TripStatus.IN_PROGRESS.value},
        sort=[("departure_time", 1)],
    )
    if doc is None:
        doc = await db.trips.find_one(
            {
                "vin": vin,
                "status": TripStatus.PLANNED.value,
                "departure_time": {"$gte": now},
            },
            sort=[("departure_time", 1)],
        )
    if doc is None:
        return None
    doc.pop("_id", None)
    return Trip(**doc)


async def evaluate_vehicle_readiness(
    db: Any,
    vin: str,
    telemetry_event: TelemetryEvent | None = None,
) -> ReadinessAssessment | None:
    vehicle_doc = await db.vehicles.find_one({"vin": vin})
    if vehicle_doc is None:
        return None
    vehicle_doc.pop("_id", None)
    vehicle = Vehicle(**vehicle_doc)

    if telemetry_event is None:
        telemetry_doc = await db.telemetry.find_one({"vin": vin}, sort=[("ts", -1)])
        if telemetry_doc is None:
            return None
        telemetry_doc.pop("_id", None)
        telemetry_doc.pop("ingested_at", None)
        telemetry_event = TelemetryEvent(**telemetry_doc)

    trip = await find_next_trip(db, vin, telemetry_event.ts)
    return assess_readiness(
        vehicle,
        telemetry_event,
        trip,
        reserve_range_km=settings.reserve_range_km,
        charge_soon_margin_km=settings.charge_soon_margin_km,
    )


async def synchronize_readiness_alert(
    db: Any,
    assessment: ReadinessAssessment,
    kafka: Any | None = None,
) -> None:
    now = datetime.now(UTC)
    dedupe_key = f"{assessment.vin}:TRIP_READINESS"
    existing = await db.alerts.find_one({"dedupe_key": dedupe_key})

    if assessment.status in {ReadinessStatus.CRITICAL, ReadinessStatus.CHARGE_SOON}:
        severity = (
            "critical" if assessment.status == ReadinessStatus.CRITICAL else "warning"
        )
        existing_status = existing.get("status") if existing else None
        status = (
            existing_status
            if existing_status
            in {AlertStatus.ACKNOWLEDGED.value, AlertStatus.ACTION_SCHEDULED.value}
            else AlertStatus.OPEN.value
        )
        message = assessment.explanation
        alert = {
            "dedupe_key": dedupe_key,
            "vin": assessment.vin,
            "type": "TRIP_READINESS",
            "severity": severity,
            "status": status,
            "message": message,
            "readiness": assessment.model_dump(mode="json"),
            "updated_at": now,
            "resolved_at": None,
        }
        await db.alerts.update_one(
            {"dedupe_key": dedupe_key},
            {"$set": alert, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        changed = (
            existing is None
            or existing.get("status") == AlertStatus.RESOLVED.value
            or existing.get("severity") != severity
        )
        if changed and kafka is not None:
            await kafka.publish(settings.kafka_alerts_topic, alert, key=assessment.vin)
        return

    if existing and existing.get("status") != AlertStatus.RESOLVED.value:
        await db.alerts.update_one(
            {"dedupe_key": dedupe_key},
            {
                "$set": {
                    "status": AlertStatus.RESOLVED.value,
                    "message": f"Readiness recovered: {assessment.explanation}",
                    "readiness": assessment.model_dump(mode="json"),
                    "updated_at": now,
                    "resolved_at": now,
                }
            },
        )


async def update_charging_lifecycle(db: Any, event: TelemetryEvent) -> None:
    plan = await db.charging_plans.find_one(
        {"vin": event.vin, "status": {"$in": ["APPROVED", "CHARGING"]}},
        sort=[("created_at", -1)],
    )
    if plan is None:
        return

    if event.is_plugged_in and event.charger_id == plan.get("charger_id"):
        await db.charging_plans.update_one(
            {"plan_id": plan["plan_id"]},
            {"$set": {"status": "CHARGING", "updated_at": datetime.now(UTC)}},
        )
        await db.reservations.update_one(
            {"plan_id": plan["plan_id"]},
            {"$set": {"status": "OCCUPIED", "updated_at": datetime.now(UTC)}},
        )

    if event.soc_pct >= float(plan.get("target_soc_pct", 101)):
        now = datetime.now(UTC)
        await db.charging_plans.update_one(
            {"plan_id": plan["plan_id"]},
            {
                "$set": {
                    "status": "COMPLETED",
                    "active": False,
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        await db.reservations.update_one(
            {"plan_id": plan["plan_id"]},
            {"$set": {"status": "COMPLETED", "completed_at": now, "updated_at": now}},
        )


async def process_telemetry_for_operations(
    db: Any, event: TelemetryEvent, kafka: Any | None = None
) -> None:
    latest = await db.telemetry.find_one(
        {"vin": event.vin},
        {"event_id": 1},
        sort=[("ts", -1)],
    )
    if latest is None or latest.get("event_id") != event.event_id:
        return

    await update_charging_lifecycle(db, event)
    assessment = await evaluate_vehicle_readiness(db, event.vin, event)
    if assessment is not None:
        await synchronize_readiness_alert(db, assessment, kafka)
