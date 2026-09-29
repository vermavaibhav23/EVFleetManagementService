from fastapi import APIRouter

from app.core.dependencies import get_database
from app.models.charger import Charger, ChargerRecommendation

router = APIRouter()


@router.post("/chargers", response_model=Charger)
async def upsert_charger(charger: Charger) -> Charger:
    db = get_database()
    await db.chargers.update_one(
        {"charger_id": charger.charger_id},
        {"$set": charger.model_dump(mode="json")},
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


@router.get("/charging/recommendations/{vin}", response_model=ChargerRecommendation)
async def recommend_charger(vin: str) -> ChargerRecommendation:
    db = get_database()
    telemetry = await db.telemetry.find_one({"vin": vin}, sort=[("ts", -1)])
    charger = await db.chargers.find_one({"status": "available"}, sort=[("available_kw", -1)])

    if not telemetry or not charger:
        return ChargerRecommendation(vin=vin, recommendation="insufficient_data")

    charger.pop("_id", None)
    soc_pct = float(telemetry.get("soc_pct", 0))
    priority = "urgent" if soc_pct <= 15 else "normal" if soc_pct <= 40 else "defer"

    return ChargerRecommendation(
        vin=vin,
        recommendation=priority,
        charger=Charger(**charger),
        reason=f"Latest state of charge is {soc_pct:g}%.",
    )

