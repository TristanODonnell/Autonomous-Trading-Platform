from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.runtime.live_performance_metrics import (
    LivePerformanceMetrics,
)
from autonomous_trading_platform.contracts.runtime.metric_lineage import MetricLineageType
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_live_metrics_computed_total,
    ratp_live_strategy_drawdown,
    ratp_live_strategy_sharpe,
    ratp_strategy_runtime_maturity,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.live_performance_snapshot_repository import (
    LivePerformanceSnapshotRepository,
)

logger = get_logger(__name__)

_ANNUALIZATION = math.sqrt(252)
# How far back to fetch raw data when building rolling windows.
_LOOKBACK_MULTIPLIER = 2


@dataclass
class _Lot:
    price: Decimal
    quantity: Decimal


def compute_alpha(days_live: int, trade_count: int) -> float:
    """
    Compute the live-vs-backtest blending weight (alpha).

    Alpha increases as the strategy accumulates live history.  Both days-live
    and trade-count must be sufficient; the minimum of the two signals is used
    so a strategy with many days but few trades (or vice versa) is not
    over-weighted toward live metrics.

    Schedule:
        days 0–5  (or trades  0–9)  → alpha ≈ 0.10
        days  ~30 (or trades ~30)   → alpha ≈ 0.50
        days  90+ (or trades 50+)   → alpha ≈ 0.90
    """
    if days_live < 5:
        days_factor = 0.10
    elif days_live < 30:
        days_factor = 0.10 + (0.50 - 0.10) * (days_live - 5) / 25.0
    elif days_live < 90:
        days_factor = 0.50 + (0.80 - 0.50) * (days_live - 30) / 60.0
    else:
        days_factor = 0.90

    if trade_count < 10:
        trades_factor = 0.10
    elif trade_count < 50:
        trades_factor = 0.10 + (0.80 - 0.10) * (trade_count - 10) / 40.0
    else:
        trades_factor = 0.80

    return min(days_factor, trades_factor)


_CALCULATION_VERSION = "1.0"


class LivePerformanceMetricsService:
    DEFAULT_WINDOW_DAYS = 20
    DEFAULT_WINDOW_TRADES = 50

    def __init__(self, session: Session, *, environment: str | None = None) -> None:
        self._session = session
        self._snapshot_repo = LivePerformanceSnapshotRepository(session)
        self._environment = environment or os.getenv("APP_ENV")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_for_strategy(
        self,
        strategy_id: str,
        *,
        run_id: str | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
        window_trades: int = DEFAULT_WINDOW_TRADES,
        now: datetime | None = None,
    ) -> LivePerformanceMetrics:
        """Compute live performance metrics for a strategy from runtime data.

        Uses fills (joined via order_intents) for trade-based metrics and
        cash_snapshots for equity-curve-based metrics.  If run_id is not
        supplied the service attempts to discover it from the most recent
        order_intent for the strategy.
        """
        if now is None:
            now = datetime.now(UTC)

        effective_run_id = run_id or self._find_active_run_id(strategy_id)
        lookback = timedelta(days=max(window_days, 90) * _LOOKBACK_MULTIPLIER)
        since = now - lookback

        # Fill-based metrics: join fills → order_intents for strategy-level attribution.
        fills = self._fetch_fills_for_strategy(strategy_id=strategy_id, since=since)
        all_trades = self._compute_round_trip_trades(fills)
        windowed_trades = (
            all_trades[-window_trades:] if len(all_trades) > window_trades else all_trades
        )
        trade_count = len(windowed_trades)
        winning_trade_count = sum(1 for pnl in windowed_trades if pnl > 0)
        live_win_rate = winning_trade_count / trade_count if trade_count > 0 else None

        # Equity-curve metrics: require run_id for cash_snapshot lookups.
        raw_equity: list[tuple[datetime, float]] = []
        if effective_run_id:
            raw_equity = self._build_equity_curve(run_id=effective_run_id, since=since)
        windowed_equity = self._window_equity_curve(raw_equity, window_days=window_days)
        daily_returns = self._compute_daily_returns(windowed_equity)

        realized_return = self._compute_realized_return(windowed_equity)
        rolling_sharpe = self._compute_rolling_sharpe(daily_returns)
        realized_drawdown = self._compute_drawdown(windowed_equity)
        realized_volatility = self._compute_volatility(daily_returns)
        days_since_profitable = self._compute_days_since_profitable_day(windowed_equity, now=now)
        days_live = self._compute_days_live(fills=fills, equity_curve=raw_equity, now=now)

        has_data = trade_count > 0 or bool(raw_equity)
        log_level = "LIVE_METRICS_COMPUTED" if has_data else "LIVE_HISTORY_INSUFFICIENT"
        logger.info(
            log_level,
            extra={
                "strategy_id": strategy_id,
                "run_id": effective_run_id,
                "trade_count": trade_count,
                "days_live": days_live,
                "rolling_sharpe": rolling_sharpe,
                "realized_drawdown": realized_drawdown,
                "live_win_rate": live_win_rate,
                "window_days": window_days,
                "window_trades": window_trades,
                "metric_lineage_type": MetricLineageType.LIVE.value,
                "environment": self._environment,
            },
        )

        attrs = {"strategy_id": strategy_id}
        ratp_live_metrics_computed_total.add(1, attrs)
        if rolling_sharpe is not None:
            ratp_live_strategy_sharpe.record(rolling_sharpe, attrs)
        if realized_drawdown is not None:
            ratp_live_strategy_drawdown.record(abs(realized_drawdown), attrs)
        if days_live is not None:
            ratp_strategy_runtime_maturity.record(days_live, attrs)

        return LivePerformanceMetrics(
            snapshot_id=str(uuid4()),
            strategy_id=strategy_id,
            run_id=effective_run_id,
            computed_at=now,
            window_days=window_days,
            window_trades=window_trades,
            realized_return=realized_return,
            rolling_sharpe=rolling_sharpe,
            realized_drawdown=realized_drawdown,
            realized_volatility=realized_volatility,
            live_win_rate=live_win_rate,
            trade_count=trade_count,
            winning_trade_count=winning_trade_count,
            days_live=days_live,
            days_since_profitable_day=days_since_profitable,
            metric_lineage_type=MetricLineageType.LIVE,
            environment=self._environment,
            calculation_version=_CALCULATION_VERSION,
        )

    def compute_and_persist(
        self,
        strategy_id: str,
        *,
        run_id: str | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
        window_trades: int = DEFAULT_WINDOW_TRADES,
        now: datetime | None = None,
    ) -> LivePerformanceMetrics:
        """Compute metrics and persist a snapshot to the SOR."""
        metrics = self.compute_for_strategy(
            strategy_id,
            run_id=run_id,
            window_days=window_days,
            window_trades=window_trades,
            now=now,
        )
        self._snapshot_repo.insert(
            StrategyLivePerformanceSnapshot(
                snapshot_id=metrics.snapshot_id,
                strategy_id=metrics.strategy_id,
                run_id=metrics.run_id,
                computed_at=metrics.computed_at,
                window_days=metrics.window_days,
                window_trades=metrics.window_trades,
                realized_return=metrics.realized_return,
                rolling_sharpe=metrics.rolling_sharpe,
                realized_drawdown=metrics.realized_drawdown,
                realized_volatility=metrics.realized_volatility,
                live_win_rate=metrics.live_win_rate,
                trade_count=metrics.trade_count,
                winning_trade_count=metrics.winning_trade_count,
                days_live=metrics.days_live,
                days_since_profitable_day=metrics.days_since_profitable_day,
                metric_lineage_type=metrics.metric_lineage_type.value
                if metrics.metric_lineage_type
                else None,
                environment=metrics.environment,
                calculation_version=metrics.calculation_version,
            )
        )
        return metrics

    def get_latest(self, strategy_id: str) -> LivePerformanceMetrics | None:
        """Return the most-recently persisted snapshot for a strategy, or None."""
        row = self._snapshot_repo.get_latest(strategy_id)
        if row is None:
            return None
        lineage_type: MetricLineageType | None = None
        if row.metric_lineage_type:
            try:
                lineage_type = MetricLineageType(row.metric_lineage_type)
            except ValueError:
                lineage_type = MetricLineageType.LIVE

        return LivePerformanceMetrics(
            snapshot_id=row.snapshot_id,
            strategy_id=row.strategy_id,
            run_id=row.run_id,
            computed_at=row.computed_at,
            window_days=row.window_days,
            window_trades=row.window_trades,
            realized_return=row.realized_return,
            rolling_sharpe=row.rolling_sharpe,
            realized_drawdown=row.realized_drawdown,
            realized_volatility=row.realized_volatility,
            live_win_rate=row.live_win_rate,
            trade_count=row.trade_count,
            winning_trade_count=row.winning_trade_count,
            days_live=row.days_live,
            days_since_profitable_day=row.days_since_profitable_day,
            metric_lineage_type=lineage_type or MetricLineageType.LIVE,
            environment=row.environment,
            calculation_version=row.calculation_version,
        )

    # ------------------------------------------------------------------
    # Private: data retrieval
    # ------------------------------------------------------------------

    def _find_active_run_id(self, strategy_id: str) -> str | None:
        """Return the run_id of the most recent order_intent for this strategy."""
        row = self._session.scalars(
            select(OrderIntents.run_id)
            .where(OrderIntents.strategy_id == strategy_id)
            .order_by(OrderIntents.timestamp.desc())
            .limit(1)
        ).one_or_none()
        return str(row) if row is not None else None

    def _build_equity_curve(self, *, run_id: str, since: datetime) -> list[tuple[datetime, float]]:
        """Return (timestamp, equity) pairs for a run ordered by timestamp."""
        try:
            run_uuid = UUID(run_id)
        except (ValueError, AttributeError):
            return []

        rows = self._session.scalars(
            select(CashSnapshot)
            .where(
                CashSnapshot.run_id == run_uuid,
                CashSnapshot.equity.is_not(None),
                CashSnapshot.timestamp >= since,
            )
            .order_by(CashSnapshot.timestamp.asc())
        ).all()
        return [(row.timestamp, float(row.equity)) for row in rows]

    def _fetch_fills_for_strategy(self, *, strategy_id: str, since: datetime) -> list[Fill]:
        """Return fills for a strategy joined through order_intents."""
        return list(
            self._session.execute(
                select(Fill)
                .join(OrderIntents, Fill.intent_id == OrderIntents.intent_id)
                .where(
                    OrderIntents.strategy_id == strategy_id,
                    Fill.timestamp >= since,
                )
                .order_by(Fill.timestamp.asc())
            )
            .scalars()
            .all()
        )

    # ------------------------------------------------------------------
    # Private: metrics computation
    # ------------------------------------------------------------------

    def _compute_round_trip_trades(self, fills: list[Fill]) -> list[float]:
        """
        FIFO matching of buy→sell fills per symbol.

        Each matched pair contributes one realized-PnL entry to the output.
        Short-side (sell-first) activity is ignored — this models long-only
        strategies.  Partial fills are supported via residual lot tracking.
        """
        lots: dict[str, list[_Lot]] = defaultdict(list)
        pnls: list[float] = []

        for fill in fills:
            qty = fill.quantity
            price = fill.price

            if fill.side == Side.BUY:
                lots[fill.symbol].append(_Lot(price=price, quantity=qty))
            elif fill.side == Side.SELL:
                remaining = qty
                while remaining > Decimal("0") and lots[fill.symbol]:
                    lot = lots[fill.symbol][0]
                    matched = min(remaining, lot.quantity)
                    pnls.append(float((price - lot.price) * matched))
                    remaining -= matched
                    lot.quantity -= matched
                    if lot.quantity <= Decimal("0"):
                        lots[fill.symbol].pop(0)

        return pnls

    def _window_equity_curve(
        self,
        equity_curve: list[tuple[datetime, float]],
        *,
        window_days: int,
    ) -> list[tuple[datetime, float]]:
        if not equity_curve:
            return []
        cutoff = equity_curve[-1][0] - timedelta(days=window_days)
        return [(ts, eq) for ts, eq in equity_curve if ts >= cutoff]

    def _compute_daily_returns(self, equity_curve: list[tuple[datetime, float]]) -> list[float]:
        """Resample to daily (last value per calendar day) and return simple returns."""
        if len(equity_curve) < 2:
            return []
        daily: dict[date, float] = {}
        for ts, equity in equity_curve:
            d = ts.date() if hasattr(ts, "date") else ts
            daily[d] = equity

        sorted_days = sorted(daily.keys())
        if len(sorted_days) < 2:
            return []

        return [
            (daily[sorted_days[i]] - daily[sorted_days[i - 1]]) / daily[sorted_days[i - 1]]
            for i in range(1, len(sorted_days))
            if daily[sorted_days[i - 1]] > 0
        ]

    def _compute_rolling_sharpe(self, daily_returns: list[float]) -> float | None:
        n = len(daily_returns)
        if n < 2:
            return None
        mean_r = sum(daily_returns) / n
        variance = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
        if variance <= 0:
            return None
        return (mean_r / math.sqrt(variance)) * _ANNUALIZATION

    def _compute_drawdown(self, equity_curve: list[tuple[datetime, float]]) -> float | None:
        if len(equity_curve) < 2:
            return None
        peak = equity_curve[0][1]
        max_dd = 0.0
        for _, equity in equity_curve:
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (equity - peak) / peak
                if dd < max_dd:
                    max_dd = dd
        return max_dd if max_dd < 0 else None

    def _compute_volatility(self, daily_returns: list[float]) -> float | None:
        n = len(daily_returns)
        if n < 2:
            return None
        mean_r = sum(daily_returns) / n
        variance = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
        return math.sqrt(variance) * _ANNUALIZATION if variance >= 0 else None

    def _compute_realized_return(self, equity_curve: list[tuple[datetime, float]]) -> float | None:
        if len(equity_curve) < 2:
            return None
        first_eq = equity_curve[0][1]
        if first_eq <= 0:
            return None
        return (equity_curve[-1][1] - first_eq) / first_eq

    def _compute_days_live(
        self,
        *,
        fills: list[Fill],
        equity_curve: list[tuple[datetime, float]],
        now: datetime,
    ) -> int | None:
        earliest: datetime | None = None
        if fills:
            ts = fills[0].timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            earliest = ts
        if equity_curve:
            ts = equity_curve[0][0]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if earliest is None or ts < earliest:
                earliest = ts
        if earliest is None:
            return None
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return max(0, (now - earliest).days)

    def _compute_days_since_profitable_day(
        self,
        equity_curve: list[tuple[datetime, float]],
        *,
        now: datetime,
    ) -> int | None:
        """Count calendar days since the last day with a positive equity return."""
        daily: dict[date, float] = {}
        for ts, equity in equity_curve:
            d = ts.date() if hasattr(ts, "date") else ts
            daily[d] = equity

        sorted_days = sorted(daily.keys())
        if len(sorted_days) < 2:
            return None

        # Find the last calendar day with a positive return
        last_profitable: date | None = None
        for i in range(1, len(sorted_days)):
            prev = daily[sorted_days[i - 1]]
            curr = daily[sorted_days[i]]
            if prev > 0 and curr > prev:
                last_profitable = sorted_days[i]

        today = now.date() if hasattr(now, "date") else now
        if last_profitable is None:
            return max(0, (today - sorted_days[-1]).days)
        return max(0, (today - last_profitable).days)
