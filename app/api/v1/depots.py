from fastapi import APIRouter

from app.core.dependencies import get_database
from app.models.depot import Depot
from app.services.coordination import serialized

router = APIRouter()


@router.post("", response_model=Depot, status_code=201)
@serialized
async def upsert_depot(depot: Depot) -> Depot:
    db = get_database()
    await db.depots.update_one(
        {"depot_id": depot.depot_id},
        {"$set": depot.model_dump(mode="python")},
        upsert=True,
    )
    return depot


@router.get("", response_model=list[Depot])
async def list_depots() -> list[Depot]:
    db = get_database()
    depots: list[Depot] = []
    async for doc in db.depots.find({}).sort("depot_id", 1):
        doc.pop("_id", None)
        depots.append(Depot(**doc))
    return depots
