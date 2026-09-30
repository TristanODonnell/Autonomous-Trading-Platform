"""
Rotation report (portfolio rotation step 5).

Built from the system of record right after a portfolio-mode backtest:

  equity      starting cash + Σ each real sleeve's latest net P&L (carried forward
              once a strategy leaves), per snapshot day — the Step 1 invariant makes
              this equal to cash + marked holdings
  benchmark   buy-and-hold of one symbol (daily closes passed in by the caller)
  activity    per month, from membership transitions, review decisions and bench
              evaluations: swaps, seats added / dropped, on-deck moves, bench
              admissions, retirements, review promotions / governance rejections
  turnover    Σ |fill notional| ÷ average equity
  contribution  each sleeve's final net P&L, share of the total, days per tier
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.rotation_report import (
    PerformanceMetrics,
    RotationActivity,
    RotationReport,
    StrategyContribution,
)
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipTransitionRow,
)
from autonomous_trading_platform.storage.sor.models.portfolio_reviews import (
    PortfolioReviewDecisionRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    StrategySleeveSnapshotRow,
)

TRADING_DAYS_PER_YEAR = 252
_ACTIVE = MembershipStatus.ACTIVE.value
_ON_DECK = MembershipStatus.ON_DECK.value
_BENCH = MembershipStatus.BENCH.value


def performance_metrics(equity: pd.Series) -> PerformanceMetrics | None:
    """Metrics of a daily equity curve (index = dates, values = portfolio value)."""
    equity = equity.dropna().astype(float)
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return None
    returns = equity.pct_change().dropna()
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    total = end / start - 1
    days = len(returns)
    annual = (1 + total) ** (TRADING_DAYS_PER_YEAR / days) - 1 if days and total > -1 else None
    std = float(returns.std(ddof=1)) if days > 1 else 0.0
    vol = std * math.sqrt(TRADING_DAYS_PER_YEAR) if days > 1 else None
    sharpe = float(returns.mean()) / std * math.sqrt(TRADING_DAYS_PER_YEAR) if std > 0 else None
    drawdown = float((1 - equity / equity.cummax()).max())
    return PerformanceMetrics(
        start_value=start,
        end_value=end,
        total_return=total,
        annualized_return=annual,
        annualized_volatility=vol,
        sharpe=sharpe,
        max_drawdown=drawdown,
        trading_days=days,
    )


class RotationReportService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build(
        self,
        *,
        start_date: date,
        end_date: date,
        starting_cash: float,
        benchmark_symbol: str | None = None,
        benchmark_closes: pd.Series | None = None,
    ) -> RotationReport:
        report = RotationReport(
            start_date=start_date, end_date=end_date, starting_cash=float(starting_cash)
        )
        equity, final_pnl = self._equity_curve(starting_cash=float(starting_cash))
        if equity.empty:
            report.warnings.append("no_sleeve_snapshots")
        report.portfolio = performance_metrics(equity)

        if benchmark_closes is not None and not benchmark_closes.empty:
            closes = benchmark_closes.sort_index()
            closes = closes[(closes.index >= start_date) & (closes.index <= end_date)]
            report.benchmark_symbol = benchmark_symbol
            report.benchmark = performance_metrics(
                closes / float(closes.iloc[0]) * float(starting_cash) if len(closes) else closes
            )
        elif benchmark_symbol:
            report.warnings.append(f"no_benchmark_bars:{benchmark_symbol}")

        report.activity = self._activity()
        report.total_swaps = sum(a.swaps for a in report.activity)
        months = max(_months_between(start_date, end_date), 1)
        report.swaps_per_month = round(report.total_swaps / months, 4)
        report.turnover = self._turnover(equity)
        report.contributions = self._contributions(final_pnl, end_date=end_date)
        return report

    # ------------------------------------------------------------------ equity

    def _equity_curve(self, *, starting_cash: float) -> tuple[pd.Series, dict[str, float]]:
        rows = self._session.execute(
            select(
                StrategySleeveSnapshotRow.timestamp,
                StrategySleeveSnapshotRow.strategy_id,
                StrategySleeveSnapshotRow.net_pnl,
            ).order_by(StrategySleeveSnapshotRow.timestamp)
        ).all()
        if not rows:
            return pd.Series(dtype=float), {}
        frame = pd.DataFrame(rows, columns=["timestamp", "strategy_id", "net_pnl"])
        frame["day"] = pd.to_datetime(frame["timestamp"], utc=True).dt.date
        frame["net_pnl"] = frame["net_pnl"].astype(float)
        # Last snapshot per strategy per day, carried forward after it stops.
        wide = frame.groupby(["day", "strategy_id"])["net_pnl"].last().unstack().ffill()
        equity = starting_cash + wide.fillna(0.0).sum(axis=1)
        final = {str(k): float(v) for k, v in wide.iloc[-1].dropna().items()}
        return equity, final

    # ------------------------------------------------------------------ activity

    def _activity(self) -> list[RotationActivity]:
        by_month: dict[str, RotationActivity] = {}

        def month(ts: datetime) -> RotationActivity:
            key = f"{ts:%Y-%m}"
            if key not in by_month:
                by_month[key] = RotationActivity(month=key)
            return by_month[key]

        transitions = self._session.scalars(
            select(PortfolioMembershipTransitionRow).order_by(
                PortfolioMembershipTransitionRow.created_at
            )
        ).all()
        first_day = transitions[0].created_at.date() if transitions else None
        for t in transitions:
            m = month(t.created_at)
            if t.to_status == _ACTIVE and t.from_status != _ACTIVE:
                if t.reason == "review_swap":
                    m.swaps += 1
                elif t.created_at.date() != first_day:
                    m.seats_added += 1
            elif t.from_status == _ACTIVE and t.reason not in ("review_swap",):
                m.seats_dropped += 1
            if t.to_status == _ON_DECK and t.from_status == _BENCH:
                m.on_deck_promotions += 1
            if t.from_status == _ON_DECK and t.to_status in (
                _BENCH,
                MembershipStatus.INACTIVE.value,
            ):
                m.on_deck_demotions += 1

        for ev in self._session.scalars(select(BenchEvaluationRow)).all():
            if ev.decision == "admit":
                month(ev.reviewed_at).bench_admissions += 1
            elif ev.decision == "retire":
                month(ev.reviewed_at).retirements += 1

        for d in self._session.scalars(select(PortfolioReviewDecisionRow)).all():
            if d.decision_type not in ("swap", "add_seat"):
                continue
            governance = (d.guardrails or {}).get("governance")
            if governance is True and d.applied:
                month(d.reviewed_at).governance_promotions += 1
            elif governance is False:
                month(d.reviewed_at).governance_rejections += 1

        return [by_month[k] for k in sorted(by_month)]

    # ------------------------------------------------------------------ turnover

    def _turnover(self, equity: pd.Series) -> float | None:
        if equity.empty or float(equity.mean()) <= 0:
            return None
        notional = sum(
            abs(float(q) * float(p))
            for q, p in self._session.execute(select(Fill.quantity, Fill.price)).all()
        )
        return round(notional / float(equity.mean()), 6)

    # ------------------------------------------------------------------ contributions

    def _contributions(
        self, final_pnl: dict[str, float], *, end_date: date
    ) -> list[StrategyContribution]:
        total = sum(final_pnl.values())
        days: dict[str, dict[str, int]] = defaultdict(dict)
        final_tier: dict[str, str] = {}
        transitions = self._session.scalars(
            select(PortfolioMembershipTransitionRow).order_by(
                PortfolioMembershipTransitionRow.strategy_id,
                PortfolioMembershipTransitionRow.created_at,
            )
        ).all()
        by_strategy: dict[str, list[PortfolioMembershipTransitionRow]] = defaultdict(list)
        for t in transitions:
            by_strategy[t.strategy_id].append(t)
        for sid, rows in by_strategy.items():
            for current, following in zip(rows, rows[1:] + [None], strict=False):
                stop = following.created_at.date() if following is not None else end_date
                span = max((stop - current.created_at.date()).days, 0)
                days[sid][current.to_status] = days[sid].get(current.to_status, 0) + span
            final_tier[sid] = rows[-1].to_status

        ids = sorted(set(final_pnl) | set(by_strategy))
        contributions = [
            StrategyContribution(
                strategy_id=sid,
                net_pnl=round(final_pnl.get(sid, 0.0), 6),
                pnl_share=round(final_pnl.get(sid, 0.0) / total, 6) if total else None,
                days_by_tier=days.get(sid, {}),
                final_tier=final_tier.get(sid),
            )
            for sid in ids
        ]
        return sorted(contributions, key=lambda c: (-c.net_pnl, c.strategy_id))


def _months_between(start: date, end: date) -> float:
    return ((end - start).days + 1) / 30.4375
