from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pymongo import ReturnDocument

from app.core.dependencies import get_database
from app.models.reservation import (
    ACTIVE_RESERVATION_STATUSES,
    Reservation,
    ReservationUpdate,
)
from app.services.coordination import serialized

router = APIRouter()


async def _conflicting_reservation(
    charger_id: str,
    port_number: int,
    start_time: datetime,
    end_time: datetime,
    ignore_id: str | None = None,
) -> dict | None:
    db = get_database()
    query: dict[str, object] = {
        "charger_id": charger_id,
        "port_number": port_number,
        "status": {"$in": [status.value for status in ACTIVE_RESERVATION_STATUSES]},
        "start_time": {"$lt": end_time},
        "end_time": {"$gt": start_time},
    }
    if ignore_id:
        query["reservation_id"] = {"$ne": ignore_id}
    return await db.reservations.find_one(query)


@router.post("", response_model=Reservation, status_code=201)
@serialized
async def create_reservation(reservation: Reservation) -> Reservation:
    db = get_database()
    charger = await db.chargers.find_one({"charger_id": reservation.charger_id})
    if charger is None:
        raise HTTPException(status_code=404, detail="Charger not found")
    existing = await db.reservations.find_one(
        {"reservation_id": reservation.reservation_id}
    )
    if existing:
        return Reservation(**existing)
    if charger["status"] != "AVAILABLE":
        raise HTTPException(422, "Charger is not available")
    if reservation.port_number > int(charger.get("port_count", 1)):
        raise HTTPException(status_code=400, detail="Charger port does not exist")
    if await _conflicting_reservation(
        reservation.charger_id,
        reservation.port_number,
        reservation.start_time,
        reservation.end_time,
    ):
        raise HTTPException(
            status_code=409,
            detail="The charger port is already reserved in this interval",
        )
    await db.reservations.insert_one(reservation.model_dump(mode="python"))
    return reservation


@router.get("", response_model=list[Reservation])
async def list_reservations(
    charger_id: str | None = None,
    vin: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[Reservation]:
    query: dict[str, object] = {}
    if charger_id:
        query["charger_id"] = charger_id
    if vin:
        query["vin"] = vin
    db = get_database()
    reservations: list[Reservation] = []
    async for doc in db.reservations.find(query).sort("start_time", 1).limit(limit):
        doc.pop("_id", None)
        reservations.append(Reservation(**doc))
    return reservations


@router.patch("/{reservation_id}", response_model=Reservation)
@serialized
async def update_reservation(
    reservation_id: str, update: ReservationUpdate
) -> Reservation:
    db = get_database()
    now = datetime.now(UTC)
    existing = await db.reservations.find_one({"reservation_id": reservation_id})
    if existing and existing.get("plan_id"):
        raise HTTPException(
            422, "Use the charging plan controls for a linked reservation"
        )
    if update.status.value not in {"CANCELLED", "NO_SHOW", "COMPLETED"}:
        raise HTTPException(
            422, "Reservation occupancy is controlled by physical vehicle arrival"
        )
    doc = await db.reservations.find_one_and_update(
        {"reservation_id": reservation_id},
        {"$set": {"status": update.status.value, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    doc.pop("_id", None)
    return Reservation(**doc)
