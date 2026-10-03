"""Record of one corporate action applied to one book (idempotency ledger)."""

from __future__ import annotations

import enum
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime


class CorporateActionBook(enum.StrEnum):
    # The account-level position/cash snapshots (backtests only; live is the broker's).
    ACCOUNT = "account"
    # A real strategy sleeve (scope = strategy_id).
    SLEEVE = "sleeve"
    # An on-deck shadow sleeve (scope = strategy_id).
    SHADOW_SLEEVE = "shadow_sleeve"


ACCOUNT_SCOPE = "__account__"


class CorporateActionApplication(BaseModel):
    """One (action, book, scope) application. Unique per key, so a re-run is a no-op."""

    model_config = ConfigDict(frozen=True)

    application_id: UUID
    action_id: str
    book: CorporateActionBook
    scope: str
    symbol: str
    action_type: CorporateActionType
    effective_date: date
    applied_at: UTCDateTime
    quantity_before: Quantity
    quantity_after: Quantity
    avg_cost_before: Money | None = None
    avg_cost_after: Money | None = None
    cash_delta: Money
    realized_pnl: Money
    run_id: UUID | None = None
    details: dict[str, Any] | None = None
