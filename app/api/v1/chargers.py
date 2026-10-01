from fastapi import APIRouter

from app.core.dependencies import get_database
from app.models.charger import Charger

router = APIRouter()


@router.post("/chargers", response_model=Charger)
async def upsert_charger(charger: Charger) -> Charger:
    db = get_database()
    await db.chargers.update_one(
        {"charger_id": charger.charger_id},
        {"$set": charger.model_dump(mode="python")},
        upsert=True,
    )
    return charger


@router.get("/chargers", response_model=list[Charger])
async def list_chargers() -> list[Charger]:
    db = get_database()
    chargers: list[Charger] = []
    async for doc in db.chargers.find({}).sort("available_kw", -1):
        doc.pop("_id", None)
        chargers.append(Charger(**doc))
    return chargers
