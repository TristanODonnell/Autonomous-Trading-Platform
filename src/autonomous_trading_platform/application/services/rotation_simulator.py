"""
Offline rotation simulator (portfolio rotation step 5).

Replays the weekly portfolio review over a RotationDataset for any set of review
settings, fast enough to sweep hundreds of configurations. It reuses the real
scorecard (build_scorecards) and decision rules (decide), and recomputes each
strategy's evidence from its full-period re-simulation, because a different config
rotates differently and therefore changes each strategy's evidence:

  re-sim     metrics of the trailing bench window (63 trading days ≈ 89 calendar
             days) of the bar-level equity curve, scored like bench re-sims
             (same metric functions and annualisation); independent of the path,
             so computed once per (strategy, review) and shared by every config
  forward    ACTIVE -> live, ON_DECK -> shadow: the trailing 20 calendar days inside
             the current stint of the strategy's forward record from the recording
             run (real sleeve while active, shadow sleeve while on-deck: daily return
             on allocated capital, i.e. the platform's own trading cadence), daily
             returns and sqrt(252) as LivePerformanceMetricsService; days = stint
             length. Re-sims run on intraday bars and trade very differently from the
             daily trading cycle, so they are never used as forward evidence
  backtest   the stored approval score, fading from the day the strategy became
             available

Tiers follow the decisions: swaps and open seats need governance for candidates
(promotable from the dataset, else the move is refused and recorded), the swapped
out / dropped strategy goes straight to on-deck (wind-down approximated as
immediate), bench members are the available candidates not seated elsewhere.

Portfolio value: weights as budgets() + QualityBasedReallocationService set them.
In auto mode every review re-weights the actives by blended quality
(alpha(days, trades) x forward score + (1 - alpha) x backtest score; water-filled
up to per_strategy_cap; a weight is only rewritten when it moves by at least
min_allocation_change_pct); strategies without a weight get an equal share, and
the set is scaled to the deployable total. The no-rotation baseline never
re-weights (nothing calls the rebalance), so it stays equal-weight. Weights apply
from the day after each review; the portfolio earns each
active's daily forward-record return on its share (the re-sim return only on days
without a forward record, counted as fallback_days); seat changes pay
turnover_cost_bps on the weight moved. mode "off" is the no-rotation baseline (the initial actives keep
their seats; nothing is promoted).

Known approximations (checked against a real run in validation): re-sim windows
are slices of one continuous run, not fresh runs; wind-down is immediate; no
health lifecycle, drawdown ladder, risk blocks or throttles; bench admissions and
retirements follow the recording run.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    compute_alpha,
)
from autonomous_trading_platform.application.services.portfolio_review_decisions import (
    ReviewInputs,
    ReviewSettings,
    decide,
    weekly_review_dates,
)
from autonomous_trading_platform.application.services.portfolio_review_service import (
    STREAK_MIN_GAP_DAYS,
)
from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
    ForwardEvidence,
    StrategyEvidence,
    build_scorecards,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    metrics_quality_score,
)
from autonomous_trading_platform.application.services.rotation_report_service import (
    performance_metrics,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    ForwardSource,
    ReviewDecision,
    ReviewDecisionType,
)
from autonomous_trading_platform.contracts.governance.rotation_dataset import RotationDataset
from autonomous_trading_platform.contracts.governance.rotation_report import PerformanceMetrics
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    return_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    risk_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    trade_metrics,
)

ACTIVE, ON_DECK, BENCH, INACTIVE = "active", "on_deck", "bench", "inactive"
_PAPER, _CANDIDATE = "approved_for_paper_trading", "candidate"
RESIM_WINDOW_CALENDAR_DAYS = math.ceil(63 * 7 / 5)
FORWARD_WINDOW_DAYS = 20
# LivePerformanceMetricsService.DEFAULT_WINDOW_TRADES
LIVE_WINDOW_TRADES = 50
# Bench reviews skip windows shorter than this (bench_hooks._MIN_WINDOW_CALENDAR_DAYS).
MIN_RESIM_CALENDAR_DAYS = 15


@dataclass(frozen=True)
class SimConfig:
    mode: str = "auto"  # "auto" or "off" (no-rotation baseline)
    min_active: int = 3
    max_active: int = 5
    max_on_deck: int = 4
    swap_margin: float = 0.10
    swap_consecutive: int = 4
    min_tenure_days: int = 60
    max_swaps_per_review: int = 1
    swap_interval_days: int = 28
    turnover_cost_bps: float = 20.0
    min_shadow_days: int = 20
    min_shadow_trades: int = 10
    score_floor: float = 1.0
    on_deck_min_tenure_days: int = 21

    @classmethod
    def from_settings(cls, settings: dict[str, Any], **overrides: Any) -> SimConfig:
        def get(key: str, default: Any) -> Any:
            value = settings.get(key)
            return default if value is None else value

        base = cls(
            mode="auto" if get("portfolio_review_mode", "off") == "auto" else "off",
            min_active=int(get("min_active_strategies", 3)),
            max_active=int(get("max_active_strategies", 5)),
            max_on_deck=int(get("max_on_deck_strategies", 4)),
            swap_margin=float(get("review_swap_margin", 0.10)),
            swap_consecutive=int(get("review_swap_consecutive", 4)),
            min_tenure_days=int(get("review_min_tenure_days", 60)),
            max_swaps_per_review=int(get("review_max_swaps_per_review", 1)),
            swap_interval_days=int(get("review_swap_interval_days", 28)),
            turnover_cost_bps=float(get("review_turnover_cost_bps", 20)),
            min_shadow_days=int(get("review_min_shadow_days", 20)),
            min_shadow_trades=int(get("review_min_shadow_trades", 10)),
            score_floor=float(get("review_score_floor", 1.0)),
            on_deck_min_tenure_days=int(get("review_on_deck_min_tenure_days", 21)),
        )
        return replace(base, **overrides)

    def review_settings(self) -> ReviewSettings:
        return ReviewSettings(
            min_active=min(self.min_active, self.max_active),
            max_active=self.max_active,
            max_on_deck=self.max_on_deck,
            swap_margin=Decimal(str(self.swap_margin)),
            swap_consecutive=max(self.swap_consecutive, 1),
            min_tenure_days=self.min_tenure_days,
            max_swaps_per_review=self.max_swaps_per_review,
            swap_interval_days=max(self.swap_interval_days, 1),
            turnover_cost_bps=Decimal(str(self.turnover_cost_bps)),
            min_shadow_days=self.min_shadow_days,
            min_shadow_trades=self.min_shadow_trades,
            score_floor=Decimal(str(self.score_floor)),
            on_deck_min_tenure_days=self.on_deck_min_tenure_days,
        )


@dataclass
class SimResult:
    config: SimConfig
    equity: pd.Series
    metrics: PerformanceMetrics | None
    swaps: int
    swaps_per_month: float
    seat_changes: int
    governance_rejections: int
    decisions: list[dict[str, Any]] = field(default_factory=list)
    final_tiers: dict[str, str] = field(default_factory=dict)
    # Active strategy-days valued with the re-sim return (no forward record that day).
    fallback_days: int = 0

    @property
    def sharpe(self) -> float:
        return self.metrics.sharpe or float("-inf") if self.metrics else float("-inf")

    def summary(self) -> dict[str, Any]:
        m = self.metrics
        return {
            **{k: v for k, v in self.config.__dict__.items()},
            "total_return": m.total_return if m else None,
            "sharpe": m.sharpe if m else None,
            "max_drawdown": m.max_drawdown if m else None,
            "swaps": self.swaps,
            "swaps_per_month": self.swaps_per_month,
            "seat_changes": self.seat_changes,
            "governance_rejections": self.governance_rejections,
            "fallback_days": self.fallback_days,
        }


class RotationSimulator:
    def __init__(
        self,
        dataset: RotationDataset,
        *,
        deployable_pct: float | None = None,
        per_strategy_cap: float | None = None,
    ) -> None:
        self.dataset = dataset
        settings = dataset.settings
        self.deployable = float(
            deployable_pct
            if deployable_pct is not None
            else settings.get("max_total_strategy_allocation_pct") or 1.0
        )
        cap = per_strategy_cap if per_strategy_cap is not None else settings.get("per_strategy_cap")
        self.per_strategy_cap = float(cap) if cap else None
        self.min_allocation_change = float(settings.get("min_allocation_change_pct") or 0.02)
        self.review_dates = sorted(dataset.review_dates)
        self.strategies = {s.strategy_id: s for s in dataset.strategies}
        self.curves: dict[str, pd.DataFrame] = {}
        self.daily: dict[str, pd.Series] = {}
        self.fills: dict[str, pd.DataFrame] = {}
        for sid, s in self.strategies.items():
            curve = pd.DataFrame(s.equity, columns=["timestamp", "equity"])
            if not curve.empty:
                curve["timestamp"] = pd.to_datetime(curve["timestamp"], utc=True)
                curve = curve.sort_values("timestamp").reset_index(drop=True)
            self.curves[sid] = curve
            self.daily[sid] = _daily_closes(curve)
            fills = pd.DataFrame([f.model_dump() for f in s.fills])
            if not fills.empty:
                fills["timestamp"] = pd.to_datetime(fills["timestamp"], utc=True)
            self.fills[sid] = fills
        self.forward: dict[str, pd.Series] = {
            sid: pd.Series(s.forward_returns, dtype=float).sort_index()
            for sid, s in self.strategies.items()
        }
        self.forward_closed = {sid: s.forward_closed for sid, s in self.strategies.items()}
        self.market = (
            pd.Series(dataset.market_returns, dtype=float).sort_index()
            if dataset.market_returns
            else None
        )
        self.calendar = sorted(
            set().union(
                *(set(d.index) for d in self.daily.values()),
                *(set(f.index) for f in self.forward.values()),
            )
        )
        self._resim_cache: dict[tuple[str, datetime], tuple[Decimal, pd.Series, float] | None] = {}

    # ------------------------------------------------------------------ evidence

    def _curve_slice(self, sid: str, start: datetime, end: datetime) -> pd.DataFrame:
        curve = self.curves[sid]
        if curve.empty:
            return curve
        mask = (curve["timestamp"] > pd.Timestamp(start)) & (
            curve["timestamp"] <= pd.Timestamp(end)
        )
        return curve.loc[mask].reset_index(drop=True)

    def _fills_slice(self, sid: str, start: datetime, end: datetime) -> pd.DataFrame:
        fills = self.fills[sid]
        if fills.empty:
            return fills
        mask = (fills["timestamp"] > pd.Timestamp(start)) & (
            fills["timestamp"] <= pd.Timestamp(end)
        )
        return fills.loc[mask]

    def resim_evidence(self, sid: str, at: datetime) -> tuple[Decimal, pd.Series, float] | None:
        """Re-sim score, daily returns and daily turnover over the bench window ending at `at`."""
        key = (sid, at)
        if key not in self._resim_cache:
            start = at - timedelta(days=RESIM_WINDOW_CALENDAR_DAYS)
            window = self._curve_slice(sid, start, at)
            if (
                len(window) < 2
                or (window["timestamp"].iloc[-1] - window["timestamp"].iloc[0]).days
                < MIN_RESIM_CALENDAR_DAYS - 1
            ):
                self._resim_cache[key] = None
            else:
                rm = return_metrics(window)
                rk = risk_metrics(window)
                tm = trade_metrics(self._fills_slice(sid, start, at), window)
                score = metrics_quality_score(
                    sharpe=rk.sharpe_ratio,
                    total_return=rm.total_return,
                    max_drawdown=rk.max_drawdown,
                    win_rate=tm.win_rate,
                    trade_count=tm.total_trades,
                )
                self._resim_cache[key] = (
                    score,
                    _daily_closes(window).pct_change().dropna(),
                    tm.daily_turnover,
                )
        return self._resim_cache[key]

    def forward_evidence(
        self, sid: str, tier: str, since: datetime, at: datetime
    ) -> ForwardEvidence | None:
        """Forward evidence as LivePerformanceMetricsService computes it from a sleeve:
        return / Sharpe / drawdown over the trailing 20 calendar days of the equity
        curve, trade count and win rate over the last 50 closed trades, days live from
        the first forward record. None until the strategy has spent a day in its tier."""
        if (at - since).days < 1:
            return None
        forward = self.forward[sid]
        history = forward[forward.index <= at.date()]
        if history.empty:
            return None
        cutoff = at.date() - timedelta(days=FORWARD_WINDOW_DAYS)
        returns = history[history.index > cutoff]
        if len(returns) < 2:
            return None
        std = float(returns.std(ddof=1))
        sharpe = float(returns.mean()) / std * math.sqrt(252) if std > 0 else None
        equity = pd.concat([pd.Series([1.0]), (1 + returns).cumprod()], ignore_index=True)
        drawdown = float((equity / equity.cummax() - 1).min())
        trades = wins = 0
        for day in sorted((d for d in self.forward_closed[sid] if d <= at.date()), reverse=True):
            closed, won = self.forward_closed[sid][day]
            take = min(closed, LIVE_WINDOW_TRADES - trades)
            trades += take
            wins += round(won * take / closed) if closed else 0
            if trades >= LIVE_WINDOW_TRADES:
                break
        score = metrics_quality_score(
            sharpe=sharpe,
            total_return=float(equity.iloc[-1] - 1),
            max_drawdown=drawdown if drawdown < 0 else None,
            win_rate=wins / trades if trades else None,
            trade_count=trades,
        )
        days_live = (at.date() - history.index[0]).days
        source = ForwardSource.LIVE if tier == ACTIVE else ForwardSource.SHADOW
        return ForwardEvidence(source, score, days=max(days_live, 1), trades=trades)

    # ------------------------------------------------------------------ run

    def run(self, config: SimConfig) -> SimResult:
        start = datetime.combine(self.dataset.start_date, datetime.min.time()).replace(
            tzinfo=self.review_dates[0].tzinfo if self.review_dates else None
        )
        # Bootstrap like ActivePortfolioService.refresh before the first review: the
        # highest blended-quality seeds up to max_active (ties by id; seeds without a
        # backtest score 1.0), the rest on-deck.
        seeds = sorted(
            (sid for sid in self.dataset.initial_active if sid in self.strategies),
            key=lambda sid: (-(self.strategies[sid].approval_score or 1.0), sid),
        )
        tiers: dict[str, tuple[str, datetime]] = {
            sid: (ACTIVE if i < config.max_active else ON_DECK, start)
            for i, sid in enumerate(seeds)
        }
        approved = {sid for sid, s in self.strategies.items() if s.seeded_approved}
        history: list[tuple[datetime, set[str], set[str]]] = []
        swap_reviews: list[datetime] = []
        log: list[dict[str, Any]] = []
        weights_by_day: dict[date, dict[str, float]] = {}
        swaps = seat_changes = rejections = 0
        costs: dict[date, float] = {}
        settings = config.review_settings()

        overrides: dict[str, float] = {}

        def set_weights(effective: date) -> None:
            actives = sorted(sid for sid, (t, _) in tiers.items() if t == ACTIVE)
            equal = self.deployable / len(actives) if actives else 0.0
            raw = {}
            for sid in actives:
                weight = overrides.get(sid, equal)
                if self.per_strategy_cap:
                    weight = min(weight, self.per_strategy_cap)
                raw[sid] = weight
            total = sum(raw.values())
            scale = self.deployable / total if total > self.deployable else 1.0
            weights_by_day[effective] = {sid: w * scale for sid, w in raw.items()}

        set_weights(self.dataset.start_date)

        for at in self.review_dates:
            today = at.date()
            self._sync_bench(tiers, at)
            if config.mode != "auto":
                continue
            evidence = []
            for sid, (tier, since) in sorted(tiers.items()):
                if tier not in (ACTIVE, ON_DECK, BENCH):
                    continue
                s = self.strategies[sid]
                ev = StrategyEvidence(
                    strategy_id=sid,
                    tier=tier,
                    strategy_type=s.strategy_type,
                    backtest_score=(
                        Decimal(str(s.approval_score)) if s.approval_score is not None else None
                    ),
                    backtest_age_days=(today - s.available_from).days,
                )
                resim = self.resim_evidence(sid, at)
                if resim is not None:
                    ev.resim_score, ev.resim_returns, ev.resim_turnover = resim
                if tier in (ACTIVE, ON_DECK):
                    ev.forward = self.forward_evidence(sid, tier, since, at)
                evidence.append(ev)
            market = None
            if self.market is not None:
                window_start = today - timedelta(days=RESIM_WINDOW_CALENDAR_DAYS)
                market = self.market[
                    (self.market.index > window_start) & (self.market.index <= today)
                ]
            scorecards = build_scorecards(
                evidence, review_id=f"sim_{at:%Y%m%d}", now=at, market_returns=market
            )
            last_swap = swap_reviews[-1] if swap_reviews else None
            swap_eligible = (
                last_swap is None or (at - last_swap).days >= settings.swap_interval_days
            )
            if swap_eligible:
                swap_reviews.append(at)
            kept = set(
                weekly_review_dates(
                    [h[0] for h in history],
                    now=at,
                    depth=settings.swap_consecutive,
                    min_gap_days=STREAK_MIN_GAP_DAYS,
                )
            )
            prior = [h for h in reversed(history) if h[0] in kept]
            decisions = decide(
                ReviewInputs(
                    review_id=f"sim_{at:%Y%m%d}",
                    now=at,
                    swap_eligible=swap_eligible,
                    scorecards=scorecards,
                    members={
                        sid: v for sid, v in tiers.items() if v[0] in (ACTIVE, ON_DECK, BENCH)
                    },
                    governance={sid: _PAPER if sid in approved else _CANDIDATE for sid in tiers},
                    prior_challengers=[p[1] for p in prior],
                    prior_below_floor=[p[2] for p in prior],
                ),
                settings,
            )
            changed = False
            for d in decisions:
                applied, note = self._apply(d, tiers, approved, at)
                if d.decision_type in (ReviewDecisionType.SWAP, ReviewDecisionType.ADD_SEAT):
                    if note == "governance_rejected":
                        rejections += 1
                    elif applied:
                        seat_changes += 1
                        swaps += d.decision_type == ReviewDecisionType.SWAP
                        changed = True
                elif d.decision_type == ReviewDecisionType.DROP_SEAT and applied:
                    seat_changes += 1
                    changed = True
                log.append(
                    {
                        "at": at.isoformat(),
                        "type": d.decision_type.value,
                        "strategy_id": d.strategy_id,
                        "counterpart_id": d.counterpart_id,
                        "streak": d.streak,
                        "applied": applied,
                        "reason": d.reason + (f":{note}" if note else ""),
                    }
                )
            history.append(
                (
                    at,
                    {
                        d.strategy_id
                        for d in decisions
                        if d.decision_type
                        in (ReviewDecisionType.CHALLENGE, ReviewDecisionType.SWAP)
                    },
                    {
                        d.strategy_id
                        for d in decisions
                        if d.decision_type
                        in (ReviewDecisionType.KEEP, ReviewDecisionType.DROP_SEAT)
                    },
                )
            )
            reweighted = self._reweight(tiers, overrides, at)
            if changed or reweighted:
                before = weights_by_day[max(weights_by_day)]
                effective = self._next_day(today)
                set_weights(effective)
                after = weights_by_day[effective]
                moved = sum(
                    abs(after.get(sid, 0.0) - before.get(sid, 0.0))
                    for sid in set(before) | set(after)
                )
                costs[effective] = (
                    costs.get(effective, 0.0) + moved * config.turnover_cost_bps / 1e4
                )

        equity, fallback_days = self._portfolio_equity(weights_by_day, costs)
        metrics = performance_metrics(equity)
        months = max(((self.dataset.end_date - self.dataset.start_date).days + 1) / 30.4375, 1.0)
        return SimResult(
            config=config,
            equity=equity,
            metrics=metrics,
            swaps=swaps,
            swaps_per_month=round(swaps / months, 4),
            seat_changes=seat_changes,
            governance_rejections=rejections,
            decisions=log,
            final_tiers={sid: t for sid, (t, _) in tiers.items()},
            fallback_days=fallback_days,
        )

    # ------------------------------------------------------------------ weights

    def _reweight(
        self, tiers: dict[str, tuple[str, datetime]], overrides: dict[str, float], at: datetime
    ) -> bool:
        """QualityBasedReallocationService.rebalance for the actives; True if a weight moved."""
        actives = sorted(sid for sid, (t, _) in tiers.items() if t == ACTIVE)
        if not actives:
            return False
        scores: dict[str, float] = {}
        for sid in actives:
            tier, since = tiers[sid]
            s = self.strategies[sid]
            backtest = float(s.approval_score) if s.approval_score is not None else 1.0
            forward = self.forward_evidence(sid, ACTIVE, datetime.min.replace(tzinfo=at.tzinfo), at)
            if forward is None:
                scores[sid] = max(backtest, 0.01)
                continue
            alpha = compute_alpha(forward.days, forward.trades)
            scores[sid] = max(alpha * float(forward.score) + (1 - alpha) * backtest, 0.01)
        cap = self.per_strategy_cap or 1.0
        proposed = _water_fill(scores, total=1.0, cap=cap)
        moved = False
        for sid, weight in proposed.items():
            before = overrides.get(sid, 0.0)
            if abs(weight - before) >= self.min_allocation_change:
                overrides[sid] = weight
                moved = True
        return moved

    # ------------------------------------------------------------------ helpers

    def _available(self, sid: str, today: date) -> bool:
        s = self.strategies[sid]
        return s.available_from <= today and (s.retired_on is None or today < s.retired_on)

    def _sync_bench(self, tiers: dict[str, tuple[str, datetime]], at: datetime) -> None:
        """Bench = available candidates not seated elsewhere (admissions follow the recording)."""
        today = at.date()
        for sid, s in self.strategies.items():
            tier = tiers.get(sid, (None, at))[0]
            if tier in (ACTIVE, ON_DECK):
                continue  # protected tiers are never retired by the bench review
            if s.seeded_approved:
                continue
            if self._available(sid, today):
                if tier is None:
                    tiers[sid] = (BENCH, at)
            elif tier == BENCH:
                tiers[sid] = (INACTIVE, at)

    def _apply(
        self,
        d: ReviewDecision,
        tiers: dict[str, tuple[str, datetime]],
        approved: set[str],
        at: datetime,
    ) -> tuple[bool, str | None]:
        sid = d.strategy_id
        kind = d.decision_type
        if kind in (ReviewDecisionType.SWAP, ReviewDecisionType.ADD_SEAT):
            if sid not in approved:
                if not self.strategies[sid].promotable:
                    return False, "governance_rejected"
                approved.add(sid)
            if kind == ReviewDecisionType.SWAP and d.counterpart_id:
                tiers[d.counterpart_id] = (ON_DECK, at)
            tiers[sid] = (ACTIVE, at)
            return True, None
        if kind == ReviewDecisionType.DROP_SEAT:
            tiers[sid] = (ON_DECK, at)
            return True, None
        if kind == ReviewDecisionType.PROMOTE_ON_DECK:
            tiers[sid] = (ON_DECK, at)
            return True, None
        if kind == ReviewDecisionType.DEMOTE_ON_DECK:
            target = BENCH if sid not in approved else INACTIVE
            tiers[sid] = (target, at)
            return True, None
        return False, None

    def _next_day(self, today: date) -> date:
        later = [d for d in self.calendar if d > today]
        return later[0] if later else today + timedelta(days=1)

    def _portfolio_equity(
        self, weights_by_day: dict[date, dict[str, float]], costs: dict[date, float]
    ) -> tuple[pd.Series, int]:
        resim_returns = {sid: series.pct_change() for sid, series in self.daily.items()}
        changes = sorted(weights_by_day)
        value = float(self.dataset.starting_cash)
        points: dict[date, float] = {}
        fallback_days = 0
        for day in self.calendar:
            if day < self.dataset.start_date or day > self.dataset.end_date:
                continue
            current = [c for c in changes if c <= day]
            weights = weights_by_day[current[-1]] if current else {}
            r = 0.0
            for sid, w in weights.items():
                daily_r = self.forward[sid].get(day)
                if daily_r is None or pd.isna(daily_r):
                    daily_r = resim_returns[sid].get(day)
                    if daily_r is None or pd.isna(daily_r):
                        continue
                    fallback_days += 1
                r += w * float(daily_r)
            value *= 1 + r - costs.get(day, 0.0)
            points[day] = value
        return pd.Series(points, dtype=float), fallback_days


def _water_fill(scores: dict[str, float], *, total: float, cap: float) -> dict[str, float]:
    """Weights proportional to score, capped; capped excess re-spread over the rest
    (QualityBasedReallocationService._allocate_weighted)."""
    allocations = {sid: 0.0 for sid in scores}
    candidates = list(scores)
    budget = total
    while candidates and budget > 0:
        weight_sum = sum(max(scores[s], 0.0) for s in candidates)
        shares = {
            s: budget * max(scores[s], 0.0) / weight_sum
            if weight_sum > 0
            else budget / len(candidates)
            for s in candidates
        }
        capped, rest = [], []
        for s in candidates:
            proposed = allocations[s] + shares[s]
            if proposed >= cap:
                allocations[s] = cap
                capped.append(s)
            else:
                allocations[s] = proposed
                rest.append(s)
        if not capped:
            break
        budget = max(total - sum(allocations.values()), 0.0)
        candidates = rest
    return allocations


def _daily_closes(curve: pd.DataFrame) -> pd.Series:
    if curve.empty:
        return pd.Series(dtype=float)
    days = curve["timestamp"].dt.date
    return curve.groupby(days)["equity"].last().astype(float)


# ---------------------------------------------------------------------- sweep

DEFAULT_GRID: dict[str, list[Any]] = {
    "swap_margin": [0.05, 0.10, 0.20],
    "swap_consecutive": [2, 3, 4],
    "min_tenure_days": [14, 30, 60],
    "swap_interval_days": [14, 28],
    "score_floor": [0.9, 1.0, 1.1],
    "seats": ["fixed_3", "dynamic_3_5"],
}


def grid_configs(base: SimConfig, grid: dict[str, list[Any]] | None = None) -> list[SimConfig]:
    grid = grid or DEFAULT_GRID
    keys = list(grid)
    configs = []
    for values in itertools.product(*(grid[k] for k in keys)):
        overrides = dict(zip(keys, values, strict=True))
        seats = overrides.pop("seats", None)
        if seats == "fixed_3":
            overrides.update(min_active=3, max_active=3)
        elif seats == "dynamic_3_5":
            overrides.update(min_active=3, max_active=5)
        configs.append(replace(base, mode="auto", **overrides))
    return configs


def rank(
    results: list[SimResult], *, max_drawdown: float = 0.15, max_swaps_per_month: float = 1.0
) -> list[SimResult]:
    """Feasible first (drawdown and churn caps), then by Sharpe."""

    def key(r: SimResult) -> tuple[int, float]:
        m = r.metrics
        feasible = (
            m is not None
            and m.max_drawdown <= max_drawdown
            and r.swaps_per_month <= max_swaps_per_month
        )
        return (0 if feasible else 1, -(m.sharpe if m and m.sharpe is not None else -1e9))

    return sorted(results, key=key)
