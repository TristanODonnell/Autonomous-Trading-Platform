# autonomous_trading_platform/contracts/governance/portfolio_membership.py
"""
Portfolio membership — which eligible strategies currently hold capital.

Separate from governance: governance says a strategy *may* trade (eligibility),
membership says it *does* (it is in the active set and has a budget).
"""

from __future__ import annotations

import enum
from decimal import Decimal

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class MembershipStatus(enum.StrEnum):
    # In the active set: trades and has a budget.
    ACTIVE = "active"
    # Left the active set but its sleeve still holds positions; trades only to exit.
    WINDING_DOWN = "winding_down"
    # Reserved for the on-deck shadow tier (portfolio rotation step 2).
    ON_DECK = "on_deck"
    # Reserved for the managed bench (portfolio rotation step 3).
    BENCH = "bench"
    # No longer tracked by the portfolio; sleeve is flat.
    INACTIVE = "inactive"


# Statuses the trading cycle must run (ACTIVE trades normally, WINDING_DOWN exits).
TRADING_STATUSES = frozenset({MembershipStatus.ACTIVE, MembershipStatus.WINDING_DOWN})


class PortfolioMember(BaseModel):
    strategy_id: str
    status: MembershipStatus
    since: UTCDateTime
    reason: str | None = None
    quality_score: float | None = None
    updated_by: str
    updated_at: UTCDateTime


class MembershipTransition(BaseModel):
    transition_id: str
    strategy_id: str
    from_status: MembershipStatus | None
    to_status: MembershipStatus
    reason: str
    triggered_by: str
    quality_score: float | None = None
    created_at: UTCDateTime


class StrategyBudget(BaseModel):
    """Share of total capital a trading member may use this cycle."""

    strategy_id: str
    status: MembershipStatus
    # Fraction of total capital (0..1). Always 0 for WINDING_DOWN members.
    pct_of_capital: Decimal
    # Where the pre-normalization weight came from: "override" | "equal_weight" | "winding_down".
    source: str


class ActiveSetRefreshResult(BaseModel):
    timestamp: UTCDateTime
    active: list[str]
    winding_down: list[str]
    added: list[str]
    removed: list[str]
    # WINDING_DOWN members whose sleeves went flat this refresh.
    wind_down_completed: list[str]
    eligible_count: int
    min_active: int
    max_active: int
    transitions: list[MembershipTransition]

    @property
    def below_minimum(self) -> bool:
        return len(self.active) < self.min_active
