# autonomous_trading_platform/contracts/accounting/risk_snapshot.py
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class RiskSnapshot(BaseModel):
    snapshot_id: UUID
    run_id: UUID
    timestamp: datetime
    gross_exposure: float
    net_exposure: float
    leverage: float
    drawdown_pct: float | None = None
    limits: dict[str, Any]
    utilization: dict[str, Any]
    is_blocked: bool
    block_reasons: list[str] | None = None
