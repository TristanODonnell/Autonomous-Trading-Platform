from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OperationalAlertResponse(BaseModel):
    alert_id: str
    fingerprint: str
    alert_name: str
    category: str
    severity: str
    status: str
    source: str
    environment: str
    job_name: str | None
    strategy_id: str | None
    summary: str
    details: dict[str, Any]
    recommended_action: str | None
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    snoozed_at: datetime | None
    snoozed_by: str | None
    snoozed_until: datetime | None
    notes: list[dict[str, Any]]


class OperationalAlertsResponse(BaseModel):
    alerts: list[OperationalAlertResponse]


class CreateOperationalAlertRequest(BaseModel):
    fingerprint: str
    alert_name: str
    category: str
    severity: str
    source: str = "api"
    environment: str = "unknown"
    job_name: str | None = None
    strategy_id: str | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str | None = None


class AlertActionRequest(BaseModel):
    note: str | None = None


class AlertSnoozeRequest(BaseModel):
    snoozed_until: datetime
    note: str | None = None


class AlertNoteRequest(BaseModel):
    note: str
