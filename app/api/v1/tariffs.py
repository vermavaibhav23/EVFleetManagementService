from fastapi import APIRouter

from app.core.dependencies import get_database
from app.models.tariff import Tariff
from app.services.coordination import serialized

router = APIRouter()


@router.post("", response_model=Tariff, status_code=201)
@serialized
async def upsert_tariff(tariff: Tariff) -> Tariff:
    db = get_database()
    await db.tariffs.update_one(
        {"tariff_id": tariff.tariff_id},
        {"$set": tariff.model_dump(mode="python")},
        upsert=True,
    )
    return tariff


@router.get("", response_model=list[Tariff])
async def list_tariffs(depot_id: str | None = None) -> list[Tariff]:
    db = get_database()
    query = {"depot_id": depot_id} if depot_id else {}
    tariffs: list[Tariff] = []
    async for doc in db.tariffs.find(query).sort([("depot_id", 1), ("start_time", 1)]):
        doc.pop("_id", None)
        tariffs.append(Tariff(**doc))
    return tariffs
