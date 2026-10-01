from fastapi import APIRouter, HTTPException
from pymongo import ReturnDocument

from app.core.dependencies import get_database
from app.models.trip import Trip, TripUpdate
from app.services.coordination import serialized

router = APIRouter()


@router.post("", response_model=Trip, status_code=201)
@serialized
async def upsert_trip(trip: Trip) -> Trip:
    db = get_database()
    if await db.vehicles.find_one({"vin": trip.vin}) is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    await db.trips.update_one(
        {"trip_id": trip.trip_id},
        {"$set": trip.model_dump(mode="python")},
        upsert=True,
    )
    return trip


@router.get("/vehicle/{vin}", response_model=list[Trip])
async def list_vehicle_trips(vin: str) -> list[Trip]:
    db = get_database()
    trips: list[Trip] = []
    async for doc in db.trips.find({"vin": vin}).sort("departure_time", 1):
        doc.pop("_id", None)
        trips.append(Trip(**doc))
    return trips


@router.patch("/{trip_id}", response_model=Trip)
@serialized
async def update_trip(trip_id: str, update: TripUpdate) -> Trip:
    db = get_database()
    changes = update.model_dump(mode="python", exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No changes supplied")
    result = await db.trips.find_one_and_update(
        {"trip_id": trip_id},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    result.pop("_id", None)
    return Trip(**result)
