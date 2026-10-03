# autonomous_trading_platform/contracts/accounting/strategy_sleeve.py
"""
Strategy sleeve contracts — per-strategy books inside the shared broker account.

Every active strategy owns a sleeve: its own positions, cost basis and P&L.
Invariant: for each symbol, the sleeve quantities summed across strategies equal
the broker account quantity. Any gap is recorded in the explicit
``UNATTRIBUTED_SLEEVE_ID`` sleeve, never silently absorbed.

On-deck strategies keep a separate *shadow* book: the same accounting fed by
simulated fills, with no capital and no broker orders. Shadow sleeves are never
part of the account invariant.
"""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

UNATTRIBUTED_SLEEVE_ID = "__unattributed__"


class SleeveBook(enum.StrEnum):
    # Real capital: broker fills, internal crosses, adoption; sums to the account.
    REAL = "real"
    # On-deck shadow trading: simulated fills only.
    SHADOW = "shadow"


class SleeveEntrySource(enum.StrEnum):
    # A broker fill for an order owned by this strategy.
    BROKER_FILL = "broker_fill"
    # Quantity transferred between two sleeves at a mark price, with no broker order.
    INTERNAL_CROSS = "internal_cross"
    # Account holdings assigned to a sleeve without a fill (cutover / reconciliation).
    ADOPTION = "adoption"
    # Shadow book: a simulated fill for an on-deck strategy's order.
    SHADOW_FILL = "shadow_fill"
    # A split or cash dividend applied to the sleeve by the shared accounting rule
    # (quantity/cost change for splits, realized income for dividends; no fill).
    CORPORATE_ACTION = "corporate_action"
    # Shadow book: positions closed at the mark when the strategy leaves on-deck.
    TIER_EXIT = "tier_exit"


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
    # Shadow book only: orders dropped by risk checks / throttle this cycle.
    blocked_order_count: int = 0


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
