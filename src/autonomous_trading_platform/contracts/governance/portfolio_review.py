# autonomous_trading_platform/contracts/governance/portfolio_review.py
"""
Portfolio review (portfolio rotation step 4).

A review scores every tracked strategy (ACTIVE, ON_DECK, BENCH) on one scorecard,
weighting evidence by quality — live fills > shadow forward results > recent
re-simulation > the original approval backtest, which fades with age — and applies
four lenses (against itself, the portfolio, the bench, the market). It then decides
who sits in each tier: swaps between on-deck and active behind guardrails (margin
over consecutive reviews, minimum tenure, swap cap, turnover cost), bench <-> on-deck
exchanges, and the size of the active set. Weights still come from the existing
allocation code.
"""

from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class PortfolioReviewMode(enum.StrEnum):
    # No review; the Step 1-3 placeholder selection runs.
    OFF = "off"
    # Scorecards and decisions are recorded but nothing is applied.
    ADVISORY = "advisory"
    # Decisions are applied (governance promotion, membership changes, re-weight).
    AUTO = "auto"


class ForwardSource(enum.StrEnum):
    # Real (paper) fills in the strategy's own sleeve — ACTIVE members.
    LIVE = "live"
    # Simulated fills in a shadow sleeve — ON_DECK members.
    SHADOW = "shadow"


class ReviewDecisionType(enum.StrEnum):
    # On-deck challenger replaces an active incumbent.
    SWAP = "swap"
    # On-deck strategy takes an open active seat (set below max).
    ADD_SEAT = "add_seat"
    # Active strategy below the score floor leaves (set above min).
    DROP_SEAT = "drop_seat"
    # Bench member moves to on-deck (open slot or replaces the weakest on-deck).
    PROMOTE_ON_DECK = "promote_on_deck"
    # On-deck member returns to the bench.
    DEMOTE_ON_DECK = "demote_on_deck"
    # A challenger beat an incumbent by the margin this review, but a guardrail
    # (streak, tenure, cap, not a swap review, shadow record) held the swap back.
    # Consecutive challenge rows form the streak.
    CHALLENGE = "challenge"
    # Active strategy below the score floor kept this review (streak, tenure or the
    # minimum set size held it). Consecutive keep rows form the drop streak.
    KEEP = "keep"
    # Weekly re-weight of the active set via the existing allocation service.
    REWEIGHT = "reweight"


class Scorecard(BaseModel):
    """One strategy's standing in one review; same shape for every tier."""

    review_id: str
    strategy_id: str
    reviewed_at: UTCDateTime
    # Membership status going into the review.
    tier: str
    strategy_type: str | None = None

    # Evidence components. Weights sum to 1 over the sources present.
    forward_source: ForwardSource | None = None
    forward_score: Decimal | None = None
    forward_weight: Decimal = Decimal("0")
    forward_days: int | None = None
    forward_trades: int | None = None
    resim_score: Decimal | None = None
    resim_weight: Decimal = Decimal("0")
    backtest_score: Decimal | None = None
    backtest_weight: Decimal = Decimal("0")
    backtest_age_days: int | None = None
    evidence_score: Decimal | None = None

    # Lens penalties (subtracted from the evidence score).
    decay_penalty: Decimal = Decimal("0")
    health_status: str | None = None
    health_penalty: Decimal = Decimal("0")
    # Mean correlation of shared-window re-sim returns with the other actives.
    mean_correlation: float | None = None
    correlation_penalty: Decimal = Decimal("0")
    # Shadow orders blocked by risk / throttles ÷ (blocked + shadow trades).
    blocked_ratio: float | None = None
    blocked_penalty: Decimal = Decimal("0")
    # Against the market: recorded only in step 4.
    regime_label: str | None = None

    # evidence − penalties; None when there is no evidence at all.
    score: Decimal | None = None
    # 1 = best across all tiers.
    rank: int | None = None


class ReviewDecision(BaseModel):
    review_id: str
    reviewed_at: UTCDateTime
    decision_type: ReviewDecisionType
    strategy_id: str
    # The other side of a swap / exchange (the incumbent being replaced).
    counterpart_id: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    strategy_score: Decimal | None = None
    counterpart_score: Decimal | None = None
    # Relative edge over the counterpart after turnover cost.
    margin: float | None = None
    # Consecutive reviews this challenger has beaten this incumbent, including this one.
    streak: int = 0
    # Guardrail name -> passed (plus the values checked).
    guardrails: dict[str, Any] = {}
    applied: bool = False
    reason: str


class PortfolioReviewResult(BaseModel):
    review_id: str
    reviewed_at: UTCDateTime
    mode: PortfolioReviewMode
    # Monthly review: active swaps allowed.
    swap_eligible: bool
    window_start: date | None = None
    window_end: date | None = None
    bench_review_id: str | None = None
    scorecards: list[Scorecard] = []
    decisions: list[ReviewDecision] = []
