"""
Portfolio scorecard (portfolio rotation step 4).

One scorecard per tracked strategy (ACTIVE, ON_DECK, BENCH), same shape for every
tier, so the whole pool ranks on one scale.

Evidence (each source scored with metrics_quality_score, 1.0 = flat):

    forward   live sleeve (ACTIVE) or shadow sleeve (ON_DECK); BENCH has none
    resim     this week's shared-window re-simulation (bench review)
    backtest  the approval backtest, fading with age

    w_f  = compute_alpha(days, trades)                 (shadow × SHADOW_CONFIDENCE)
    w_bt = (1 − w_f) · BACKTEST_SHARE · 0.5^(age / BACKTEST_HALF_LIFE_DAYS)
    w_rs = (1 − w_f) − w_bt
    weights of missing sources are dropped and the rest renormalised.

Lenses (penalties subtracted from the evidence score):

    against itself   decay: forward falls short of the resim/backtest expectation,
                     weighted by w_f; health DEGRADING / CRITICAL (lifecycle state)
    the portfolio    mean re-sim return correlation with the other actives above
                     CORRELATION_FREE; score_for_slot() re-scores a challenger against
                     the actives excluding the incumbent it would replace
    the market       regime label recorded only (scored in step 5)
    (shadow)         blocked-order ratio of the shadow sleeve

The weights below are step 4 starting values; step 5 tunes them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.bench_resimulation_service import (
    ResimOutcome,
)
from autonomous_trading_platform.application.services.bench_review_service import (
    correlation,
    excess_returns_by_strategy,
)
from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
    compute_alpha,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
    metrics_quality_score,
)
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import SleeveBook
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    ForwardSource,
    Scorecard,
)
from autonomous_trading_platform.contracts.governance.strategy_health import StrategyHealthStatus
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_state_repository import (
    StrategyHealthStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_sleeve_repository import (
    ShadowSleeveRepository,
)

SHADOW_CONFIDENCE = Decimal("0.9")
BACKTEST_SHARE = Decimal("0.5")
BACKTEST_HALF_LIFE_DAYS = 90
DECAY_WEIGHT = Decimal("0.25")
HEALTH_PENALTIES = {
    StrategyHealthStatus.DEGRADING.value: Decimal("0.10"),
    StrategyHealthStatus.CRITICAL.value: Decimal("0.25"),
}
CORRELATION_WEIGHT = Decimal("0.5")
# Mean correlation up to this level is free (long-only strategies share market beta).
CORRELATION_FREE = 0.3
BLOCKED_WEIGHT = Decimal("0.25")
SCORE_FLOOR = Decimal("0.01")
_QUANT = Decimal("0.000001")

_SCORED_TIERS = (MembershipStatus.ACTIVE, MembershipStatus.ON_DECK, MembershipStatus.BENCH)
# Ties on score go to the tier with better evidence.
_TIER_RANK = {
    MembershipStatus.ACTIVE.value: 0,
    MembershipStatus.ON_DECK.value: 1,
    MembershipStatus.BENCH.value: 2,
}


@dataclass(frozen=True)
class ForwardEvidence:
    source: ForwardSource
    score: Decimal
    days: int
    trades: int


@dataclass
class StrategyEvidence:
    """Everything the scorecard needs about one strategy, already read from storage."""

    strategy_id: str
    tier: str
    strategy_type: str | None = None
    forward: ForwardEvidence | None = None
    resim_score: Decimal | None = None
    resim_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    backtest_score: Decimal | None = None
    backtest_age_days: int | None = None
    health_status: str | None = None
    blocked_ratio: float | None = None


@dataclass
class ScorecardSet:
    """Scorecards of one review plus what is needed to re-score a challenger for a slot."""

    cards: dict[str, Scorecard]
    returns: dict[str, pd.Series]
    active_ids: list[str]

    def ranked(self) -> list[Scorecard]:
        return sorted(self.cards.values(), key=lambda c: (c.rank or 10**9, c.strategy_id))

    def score_for_slot(self, strategy_id: str, *, replacing: str | None) -> Decimal | None:
        """The strategy's score if it sat in the active set in place of `replacing`.

        Only the correlation lens depends on the slot: it is recomputed against the
        actives excluding the strategy itself and `replacing`.
        """
        card = self.cards[strategy_id]
        if card.score is None or card.evidence_score is None:
            return None
        others = [sid for sid in self.active_ids if sid not in (strategy_id, replacing)]
        _, penalty = correlation_penalty(strategy_id, others, self.returns)
        adjusted = card.score + card.correlation_penalty - penalty
        return max(adjusted, SCORE_FLOOR).quantize(_QUANT)


def evidence_weights(
    *,
    forward: ForwardEvidence | None,
    has_resim: bool,
    has_backtest: bool,
    backtest_age_days: int | None,
) -> tuple[Decimal, Decimal, Decimal]:
    """(forward, resim, backtest) weights, renormalised over the sources present."""
    w_f = Decimal("0")
    if forward is not None:
        w_f = Decimal(str(round(compute_alpha(forward.days, forward.trades), 6)))
        if forward.source == ForwardSource.SHADOW:
            w_f *= SHADOW_CONFIDENCE
    rest = Decimal("1") - w_f
    age = max(backtest_age_days or 0, 0)
    fade = Decimal(str(0.5 ** (age / BACKTEST_HALF_LIFE_DAYS)))
    w_bt = rest * BACKTEST_SHARE * fade
    w_rs = rest - w_bt

    raw = (
        w_f if forward is not None else Decimal("0"),
        w_rs if has_resim else Decimal("0"),
        w_bt if has_backtest else Decimal("0"),
    )
    total = sum(raw, Decimal("0"))
    if total <= 0:
        return Decimal("0"), Decimal("0"), Decimal("0")
    f, r, b = (w / total for w in raw)
    return f.quantize(_QUANT), r.quantize(_QUANT), b.quantize(_QUANT)


def correlation_penalty(
    strategy_id: str, others: list[str], returns: dict[str, pd.Series]
) -> tuple[float | None, Decimal]:
    """(mean correlation with `others`, penalty). Unrelated or unknown pairs are skipped."""
    own = returns.get(strategy_id)
    if own is None or own.empty:
        return None, Decimal("0")
    values = [
        rho
        for other in others
        if other != strategy_id and other in returns
        for rho in [correlation(own, returns[other])]
        if rho is not None
    ]
    if not values:
        return None, Decimal("0")
    mean = sum(values) / len(values)
    penalty = CORRELATION_WEIGHT * Decimal(str(max(mean - CORRELATION_FREE, 0.0)))
    return mean, penalty.quantize(_QUANT)


def build_scorecards(
    evidence: list[StrategyEvidence],
    *,
    review_id: str,
    now: datetime,
    regime_label: str | None = None,
    market_returns: pd.Series | None = None,
) -> ScorecardSet:
    """Score and rank every strategy. Pure: no storage access.

    With market_returns the correlation lens uses market-excess returns.
    """
    returns = excess_returns_by_strategy(
        {e.strategy_id: e.resim_returns for e in evidence if not e.resim_returns.empty},
        market_returns,
    )
    active_ids = sorted(e.strategy_id for e in evidence if e.tier == MembershipStatus.ACTIVE.value)
    cards: dict[str, Scorecard] = {}

    for e in evidence:
        w_f, w_rs, w_bt = evidence_weights(
            forward=e.forward,
            has_resim=e.resim_score is not None,
            has_backtest=e.backtest_score is not None,
            backtest_age_days=e.backtest_age_days,
        )
        card = Scorecard(
            review_id=review_id,
            strategy_id=e.strategy_id,
            reviewed_at=now,
            tier=e.tier,
            strategy_type=e.strategy_type,
            forward_source=e.forward.source if e.forward else None,
            forward_score=e.forward.score if e.forward else None,
            forward_weight=w_f,
            forward_days=e.forward.days if e.forward else None,
            forward_trades=e.forward.trades if e.forward else None,
            resim_score=e.resim_score,
            resim_weight=w_rs,
            backtest_score=e.backtest_score,
            backtest_weight=w_bt,
            backtest_age_days=e.backtest_age_days,
            health_status=e.health_status,
            blocked_ratio=e.blocked_ratio,
            regime_label=regime_label,
        )
        if w_f + w_rs + w_bt > 0:
            evidence_score = (
                w_f * (e.forward.score if e.forward else Decimal("0"))
                + w_rs * (e.resim_score or Decimal("0"))
                + w_bt * (e.backtest_score or Decimal("0"))
            )
            card.evidence_score = evidence_score.quantize(_QUANT)
            card.decay_penalty = _decay_penalty(e, w_f=w_f, w_rs=w_rs, w_bt=w_bt)
            card.health_penalty = HEALTH_PENALTIES.get(e.health_status or "", Decimal("0"))
            card.mean_correlation, card.correlation_penalty = correlation_penalty(
                e.strategy_id, active_ids, returns
            )
            if e.blocked_ratio:
                card.blocked_penalty = (BLOCKED_WEIGHT * Decimal(str(e.blocked_ratio))).quantize(
                    _QUANT
                )
            score = (
                card.evidence_score
                - card.decay_penalty
                - card.health_penalty
                - card.correlation_penalty
                - card.blocked_penalty
            )
            card.score = max(score, SCORE_FLOOR).quantize(_QUANT)
        cards[e.strategy_id] = card

    ordered = sorted(
        cards.values(),
        key=lambda c: (
            c.score is None,
            -(c.score or Decimal("0")),
            _TIER_RANK.get(c.tier, 9),
            c.strategy_id,
        ),
    )
    for index, card in enumerate(ordered, start=1):
        card.rank = index
    return ScorecardSet(cards=cards, returns=returns, active_ids=active_ids)


def _decay_penalty(e: StrategyEvidence, *, w_f: Decimal, w_rs: Decimal, w_bt: Decimal) -> Decimal:
    """Forward results short of what the re-sim / backtest expected, weighted by w_f."""
    if e.forward is None or w_f <= 0:
        return Decimal("0")
    prior_weight = (w_rs if e.resim_score is not None else Decimal("0")) + (
        w_bt if e.backtest_score is not None else Decimal("0")
    )
    if prior_weight <= 0:
        return Decimal("0")
    expected = (
        w_rs * (e.resim_score or Decimal("0")) + w_bt * (e.backtest_score or Decimal("0"))
    ) / prior_weight
    shortfall = max(expected - e.forward.score, Decimal("0"))
    return (DECAY_WEIGHT * w_f * shortfall).quantize(_QUANT)


class PortfolioScorecardService:
    """Reads each strategy's evidence from storage and builds the review's scorecards."""

    def __init__(
        self,
        session: Session,
        *,
        live_metrics: LivePerformanceMetricsService | None = None,
        backtest: QualityBasedReallocationService | None = None,
        regime_label_fn: Callable[[datetime], str | None] | None = None,
    ) -> None:
        self._session = session
        self._live = live_metrics or LivePerformanceMetricsService(session)
        self._backtest = backtest or QualityBasedReallocationService(session=session)
        self._memberships = PortfolioMembershipRepository(session)
        self._health = StrategyHealthStateRepository(session)
        self._shadow = ShadowSleeveRepository(session)
        self._regime_label_fn = regime_label_fn

    def build(
        self,
        *,
        review_id: str,
        now: datetime,
        resim_outcomes: dict[str, ResimOutcome] | None = None,
        market_returns: pd.Series | None = None,
    ) -> ScorecardSet:
        outcomes = resim_outcomes or {}
        rows = sorted(
            self._memberships.get_by_statuses([t.value for t in _SCORED_TIERS]),
            key=lambda row: row.strategy_id,
        )
        evidence = [
            self.gather(
                row.strategy_id,
                row.status,
                row.since,
                now=now,
                resim=outcomes.get(row.strategy_id),
            )
            for row in rows
        ]
        regime = self._regime_label_fn(now) if self._regime_label_fn else None
        return build_scorecards(
            evidence,
            review_id=review_id,
            now=now,
            regime_label=regime,
            market_returns=market_returns,
        )

    def gather(
        self,
        strategy_id: str,
        tier: str,
        since: datetime,
        *,
        now: datetime,
        resim: ResimOutcome | None,
    ) -> StrategyEvidence:
        config = self._session.get(StrategyConfigs, strategy_id)
        evidence = StrategyEvidence(
            strategy_id=strategy_id,
            tier=tier,
            strategy_type=config.strategy_type if config is not None else None,
            backtest_score=self._backtest.backtest_quality_score(strategy_id),
            backtest_age_days=self._backtest_age_days(strategy_id, now=now),
        )
        if resim is not None and resim.ok and resim.score is not None:
            evidence.resim_score = resim.score
            evidence.resim_returns = resim.daily_returns

        if tier == MembershipStatus.ACTIVE.value:
            evidence.forward = self._forward(strategy_id, now=now, book=SleeveBook.REAL)
            health = self._health.get_for_strategy(strategy_id)
            if health is not None:
                evidence.health_status = str(health.health_status)
        elif tier == MembershipStatus.ON_DECK.value:
            evidence.forward = self._forward(strategy_id, now=now, book=SleeveBook.SHADOW)
            evidence.blocked_ratio = self._blocked_ratio(strategy_id, since=since)
        return evidence

    def _forward(
        self, strategy_id: str, *, now: datetime, book: SleeveBook
    ) -> ForwardEvidence | None:
        metrics = self._live.compute_for_strategy(strategy_id, now=now, book=book)
        days = int(metrics.days_live or 0)
        if days < 1:
            return None
        return ForwardEvidence(
            source=ForwardSource.LIVE if book == SleeveBook.REAL else ForwardSource.SHADOW,
            score=metrics_quality_score(
                sharpe=metrics.rolling_sharpe,
                total_return=metrics.realized_return,
                max_drawdown=metrics.realized_drawdown,
                win_rate=metrics.live_win_rate,
                trade_count=metrics.trade_count,
            ),
            days=days,
            trades=int(metrics.trade_count or 0),
        )

    def _backtest_age_days(self, strategy_id: str, *, now: datetime) -> int | None:
        """Days since the strategy entered the portfolio pool (first membership change).

        Approval metrics are written when research or seeding runs (wall clock in
        backtests), so pool entry is the as-of-safe proxy for "time out of the lab".
        """
        transitions = self._memberships.get_transitions(strategy_id)
        if not transitions:
            return None
        entered: datetime = transitions[0].created_at
        return max((now - entered).days, 0)

    def _blocked_ratio(self, strategy_id: str, *, since: datetime) -> float | None:
        """Blocked shadow orders ÷ (blocked + shadow fills) during the current on-deck stint."""
        blocked = sum(
            int(getattr(s, "blocked_order_count", 0) or 0)
            for s in self._shadow.get_snapshots(strategy_id)
            if s.timestamp >= since
        )
        fills = sum(1 for e in self._shadow.get_entries(strategy_id) if e.timestamp >= since)
        if blocked + fills == 0:
            return None
        return blocked / (blocked + fills)
