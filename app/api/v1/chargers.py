from fastapi import APIRouter

from app.core.dependencies import get_database
from app.models.charger import Charger, ChargerAvailability
from app.services.coordination import serialized

router = APIRouter()


@router.post("/chargers", response_model=Charger)
@serialized
async def upsert_charger(charger: Charger) -> Charger:
    db = get_database()
    await db.chargers.update_one(
        {"charger_id": charger.charger_id},
        {
            "$set": charger.model_dump(mode="python"),
            "$unset": {"occupied_ports": "", "reserved_ports": "", "free_ports": ""},
        },
        upsert=True,
    )
    return charger


@router.get("/chargers", response_model=list[ChargerAvailability])
async def list_chargers() -> list[ChargerAvailability]:
    db = get_database()
    chargers: list[ChargerAvailability] = []
    async for doc in db.chargers.find({}).sort("available_kw", -1):
        doc.pop("_id", None)
        occupied = await db.reservations.distinct(
            "port_number", {"charger_id": doc["charger_id"], "status": "OCCUPIED"}
        )
        reserved = await db.reservations.distinct(
            "port_number",
            {
                "charger_id": doc["charger_id"],
                "status": {"$in": ["CONFIRMED", "VEHICLE_EN_ROUTE"]},
            },
        )
        doc["occupied_ports"] = len(occupied)
        doc["reserved_ports"] = len(reserved)
        doc["free_ports"] = (
            max(0, doc.get("port_count", 1) - len(set(occupied)))
            if str(doc["status"]).upper() == "AVAILABLE"
            else 0
        )
        chargers.append(ChargerAvailability(**doc))
    return chargers
