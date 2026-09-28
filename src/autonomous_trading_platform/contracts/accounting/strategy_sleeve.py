# autonomous_trading_platform/contracts/accounting/strategy_sleeve.py
"""
Strategy sleeve contracts — per-strategy books inside the shared broker account.

Every active strategy owns a sleeve: its own positions, cost basis and P&L.
Invariant: for each symbol, the sleeve quantities summed across strategies equal
the broker account quantity. Any gap is recorded in the explicit
``UNATTRIBUTED_SLEEVE_ID`` sleeve, never silently absorbed.
"""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

UNATTRIBUTED_SLEEVE_ID = "__unattributed__"


class SleeveEntrySource(enum.StrEnum):
    # A broker fill for an order owned by this strategy.
    BROKER_FILL = "broker_fill"
    # Quantity transferred between two sleeves at a mark price, with no broker order.
    INTERNAL_CROSS = "internal_cross"
    # Account holdings assigned to a sleeve without a fill (cutover / reconciliation).
    ADOPTION = "adoption"


class SleevePosition(BaseModel):
    strategy_id: str
    symbol: str
    quantity: Quantity
    avg_cost: Money
    updated_at: UTCDateTime


class SleeveLedgerEntry(BaseModel):
    """One append-only accounting event against a sleeve."""

    entry_id: str
    strategy_id: str
    symbol: str
    side: Side
    quantity: Quantity
    price: Money
    fees: Money
    realized_pnl: Money
    source: SleeveEntrySource
    fill_id: str | None = None
    cross_id: str | None = None
    intent_id: UUID | None = None
    run_id: UUID | None = None
    timestamp: UTCDateTime


class SleeveSnapshot(BaseModel):
    """Point-in-time valuation of one sleeve; cumulative P&L since inception."""

    snapshot_id: UUID
    strategy_id: str
    run_id: UUID | None = None
    timestamp: UTCDateTime
    allocated_capital: Money | None = None
    market_value: Money
    cost_basis: Money
    realized_pnl: Money
    unrealized_pnl: Money
    fees: Money
    net_pnl: Money
    position_count: int
    # Symbols held by the sleeve with no price supplied; valued at cost.
    unpriced_symbols: list[str] = []


class SleeveMismatch(BaseModel):
    symbol: str
    account_quantity: Quantity
    sleeve_quantity: Quantity

    @property
    def difference(self) -> Quantity:
        """Positive: the account holds shares no sleeve owns. Negative: sleeves over-claim."""
        return self.account_quantity - self.sleeve_quantity


class SleeveReconciliationReport(BaseModel):
    timestamp: UTCDateTime
    mismatches: list[SleeveMismatch]
    # Symbols whose unowned account quantity was assigned to the unattributed sleeve.
    adopted_symbols: list[str] = []

    @property
    def is_balanced(self) -> bool:
        return not self.mismatches
