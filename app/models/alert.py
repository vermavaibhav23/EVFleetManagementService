from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ACTION_SCHEDULED = "ACTION_SCHEDULED"
    RESOLVED = "RESOLVED"


class AlertAction(BaseModel):
    status: AlertStatus
    note: str | None = Field(default=None, max_length=500)


class FleetAlert(BaseModel):
    dedupe_key: str
    vin: str
    type: str
    severity: str
    status: AlertStatus = AlertStatus.OPEN
    message: str
    readiness: dict[str, object] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
