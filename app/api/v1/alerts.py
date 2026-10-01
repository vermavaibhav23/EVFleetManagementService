from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pymongo import ReturnDocument

from app.core.dependencies import get_database
from app.models.alert import AlertAction, FleetAlert
from app.services.coordination import serialized

router = APIRouter()


@router.get("", response_model=list[FleetAlert])
async def list_alerts(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[FleetAlert]:
    db = get_database()
    query = {"status": status.upper()} if status else {}
    alerts: list[FleetAlert] = []
    async for doc in db.alerts.find(query).sort("updated_at", -1).limit(limit):
        doc.pop("_id", None)
        alerts.append(FleetAlert(**doc))
    return alerts


@router.patch("/{dedupe_key}", response_model=FleetAlert)
@serialized
async def update_alert(dedupe_key: str, action: AlertAction) -> FleetAlert:
    db = get_database()
    changes: dict[str, object] = {
        "status": action.status.value,
        "updated_at": datetime.now(UTC),
    }
    if action.note:
        changes["operator_note"] = action.note
    if action.status.value == "RESOLVED":
        changes["resolved_at"] = datetime.now(UTC)
    doc = await db.alerts.find_one_and_update(
        {"dedupe_key": dedupe_key},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    doc.pop("_id", None)
    return FleetAlert(**doc)
