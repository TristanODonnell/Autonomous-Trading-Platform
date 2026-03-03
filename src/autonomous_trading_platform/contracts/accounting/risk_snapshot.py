# autonomous_trading_platform/contracts/accounting/risk_snapshot.py

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime


class RiskSnapshot(BaseModel):
    snapshot_id: UUID
    run_id: UUID
    timestamp: UTCDateTime

    gross_exposure: Money
    net_exposure: Money

    leverage: float
    drawdown_pct: float | None = None

    limits: dict[str, Any]
    utilization: dict[str, Any]

    is_blocked: bool
    block_reasons: list[str] | None = None
