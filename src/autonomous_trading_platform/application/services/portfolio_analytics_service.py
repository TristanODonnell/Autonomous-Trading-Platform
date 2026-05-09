from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev
from typing import Any, cast

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot

_TRADING_DAYS_PER_YEAR = 252
_PERIODS: tuple[tuple[str, timedelta | None], ...] = (
    ("1M", timedelta(days=30)),
    ("3M", timedelta(days=90)),
    ("6M", timedelta(days=180)),
    ("YTD", None),
    ("1Y", timedelta(days=365)),
)


class PortfolioAnalyticsService:
    def __init__(self, *, session: Session) -> None:
        self._session = session

    def get_performance(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, Any]:
        snapshots = self._cash_snapshots_between(from_date=from_date, to_date=to_date)
        values = self._equity_values(snapshots)
        returns = self._returns(values)

        total_return = self._total_return(values)
        return {
            "total_return": total_return,
            "cagr": self._cagr(values, snapshots),
            "sharpe_ratio": self._sharpe_ratio(returns),
            "sortino_ratio": self._sortino_ratio(returns),
            "max_drawdown": self._max_drawdown(values),
            "volatility": self._annualized_volatility(returns),
            "win_rate": self._win_rate(returns),
        }

    def get_holdings(self) -> dict[str, Any]:
        latest_snapshot = self._latest_position_snapshot()
        if latest_snapshot is None:
            return {"holdings": []}

        prior_items_by_symbol = self._prior_position_items_by_symbol(latest_snapshot.timestamp)
        holdings = []
        for item in self._position_items(latest_snapshot.snapshot_id):
            quantity = Decimal(item.quantity)
            if quantity == Decimal("0"):
                continue

            market_value = self._market_value(item)
            current_price = self._current_price(item)
            previous_price = self._current_price(prior_items_by_symbol.get(item.symbol))
            todays_change_absolute = (
                current_price - previous_price if previous_price is not None else Decimal("0")
            )
            todays_change_percent = self._decimal_ratio(
                todays_change_absolute,
                previous_price,
            )

            holdings.append(
                {
                    "symbol": item.symbol,
                    "company_name": self._company_name(item.symbol),
                    "market_value": market_value,
                    "quantity": quantity,
                    "average_entry_price": Decimal(item.avg_cost or 0),
                    "current_price": current_price,
                    "todays_change_percent": todays_change_percent,
                    "todays_change_absolute": todays_change_absolute,
                    "strategy_id": self._strategy_for_symbol(item.symbol),
                }
            )

        return {"holdings": holdings}

    def get_allocation(self) -> dict[str, Any]:
        holdings = self.get_holdings()["holdings"]
        total_market_value = sum(
            (Decimal(holding["market_value"]) for holding in holdings),
            Decimal("0"),
        )
        if total_market_value == Decimal("0"):
            return {
                "by_strategy": [],
                "by_asset": [],
            }

        strategy_totals: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        asset_totals: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for holding in holdings:
            market_value = Decimal(holding["market_value"])
            strategy_totals[str(holding["strategy_id"])] += market_value
            asset_totals[str(holding["symbol"])] += market_value

        return {
            "by_strategy": self._allocation_rows(strategy_totals, total_market_value),
            "by_asset": self._allocation_rows(asset_totals, total_market_value),
        }

    def get_risk(self) -> dict[str, Any]:
        snapshots = self._cash_snapshots_between(from_date=None, to_date=None)
        values = self._equity_values(snapshots)
        returns = self._returns(values)
        current_drawdown = self._current_drawdown(values)

        return {
            "portfolio_volatility": self._annualized_volatility(returns),
            "beta": Decimal("0"),
            "value_at_risk_1d_95": self._value_at_risk_1d_95(returns, values),
            "average_pairwise_correlation": Decimal("0"),
            "current_drawdown": current_drawdown,
        }

    def get_performance_by_period(self) -> dict[str, Any]:
        latest_snapshot = self._latest_cash_snapshot()
        if latest_snapshot is None:
            return {
                "periods": [
                    {"period": period, "return_percent": Decimal("0")} for period, _ in _PERIODS
                ]
            }

        latest_equity = Decimal(latest_snapshot.equity or 0)
        rows = []
        for period, delta in _PERIODS:
            if period == "YTD":
                start = latest_snapshot.timestamp.replace(
                    month=1,
                    day=1,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            else:
                assert delta is not None
                start = latest_snapshot.timestamp - delta

            baseline = self._first_cash_snapshot_at_or_after(start) or latest_snapshot
            baseline_equity = Decimal(baseline.equity or 0)
            rows.append(
                {
                    "period": period,
                    "return_percent": self._decimal_ratio(
                        latest_equity - baseline_equity,
                        baseline_equity,
                    ),
                }
            )

        return {"periods": rows}

    def _cash_snapshots_between(
        self,
        *,
        from_date: date | None,
        to_date: date | None,
    ) -> list[CashSnapshot]:
        stmt = select(CashSnapshot).where(CashSnapshot.equity.is_not(None))

        if from_date is not None:
            start = datetime.combine(from_date, datetime.min.time(), tzinfo=UTC)
            stmt = stmt.where(CashSnapshot.timestamp >= start)
        if to_date is not None:
            end = datetime.combine(to_date, datetime.max.time(), tzinfo=UTC)
            stmt = stmt.where(CashSnapshot.timestamp <= end)

        stmt = stmt.order_by(CashSnapshot.timestamp.asc(), CashSnapshot.snapshot_id.asc())
        return list(self._session.scalars(stmt).all())

    def _latest_cash_snapshot(self) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .where(CashSnapshot.equity.is_not(None))
            .order_by(desc(CashSnapshot.timestamp), desc(CashSnapshot.snapshot_id))
            .limit(1)
        )
        return cast(
            CashSnapshot | None,
            self._session.scalars(stmt).one_or_none(),
        )

    def _first_cash_snapshot_at_or_after(self, timestamp: datetime) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .where(CashSnapshot.equity.is_not(None))
            .where(CashSnapshot.timestamp >= timestamp)
            .order_by(CashSnapshot.timestamp.asc(), CashSnapshot.snapshot_id.asc())
            .limit(1)
        )
        return cast(
            CashSnapshot | None,
            self._session.scalars(stmt).one_or_none(),
        )

    def _latest_position_snapshot(self) -> PositionSnapshot | None:
        stmt = (
            select(PositionSnapshot)
            .order_by(desc(PositionSnapshot.timestamp), desc(PositionSnapshot.snapshot_id))
            .limit(1)
        )
        return cast(
            PositionSnapshot | None,
            self._session.scalars(stmt).one_or_none(),
        )

    def _prior_position_snapshot(self, before_timestamp: datetime) -> PositionSnapshot | None:
        stmt = (
            select(PositionSnapshot)
            .where(PositionSnapshot.timestamp < before_timestamp)
            .order_by(desc(PositionSnapshot.timestamp), desc(PositionSnapshot.snapshot_id))
            .limit(1)
        )
        return cast(
            PositionSnapshot | None,
            self._session.scalars(stmt).one_or_none(),
        )

    def _position_items(self, snapshot_id) -> list[PositionSnapshotItem]:
        stmt = (
            select(PositionSnapshotItem)
            .where(PositionSnapshotItem.snapshot_id == snapshot_id)
            .order_by(PositionSnapshotItem.symbol.asc())
        )
        return list(self._session.scalars(stmt).all())

    def _prior_position_items_by_symbol(
        self,
        before_timestamp: datetime,
    ) -> dict[str, PositionSnapshotItem]:
        prior_snapshot = self._prior_position_snapshot(before_timestamp)
        if prior_snapshot is None:
            return {}

        return {item.symbol: item for item in self._position_items(prior_snapshot.snapshot_id)}

    def _equity_values(self, snapshots: list[CashSnapshot]) -> list[Decimal]:
        return [Decimal(snapshot.equity or 0) for snapshot in snapshots]

    def _returns(self, values: list[Decimal]) -> list[Decimal]:
        returns = []
        for previous, current in zip(values, values[1:], strict=False):
            returns.append(self._decimal_ratio(current - previous, previous))
        return returns

    def _total_return(self, values: list[Decimal]) -> Decimal:
        if len(values) < 2:
            return Decimal("0")
        return self._decimal_ratio(values[-1] - values[0], values[0])

    def _cagr(self, values: list[Decimal], snapshots: list[CashSnapshot]) -> Decimal:
        if len(values) < 2 or values[0] <= 0:
            return Decimal("0")

        elapsed_days = max((snapshots[-1].timestamp - snapshots[0].timestamp).days, 1)
        years = Decimal(elapsed_days) / Decimal("365")
        growth = float(values[-1] / values[0])
        if growth <= 0:
            return Decimal("0")
        return Decimal(str((growth ** (1 / float(years))) - 1))

    def _sharpe_ratio(self, returns: list[Decimal]) -> Decimal:
        if len(returns) < 2:
            return Decimal("0")
        std_dev = pstdev([float(value) for value in returns])
        if std_dev == 0:
            return Decimal("0")
        return Decimal(
            str(
                (mean([float(value) for value in returns]) / std_dev) * sqrt(_TRADING_DAYS_PER_YEAR)
            )
        )

    def _sortino_ratio(self, returns: list[Decimal]) -> Decimal:
        downside = [float(value) for value in returns if value < 0]
        if len(returns) < 2 or not downside:
            return Decimal("0")
        downside_deviation = pstdev(downside)
        if downside_deviation == 0:
            return Decimal("0")
        return Decimal(
            str(
                (mean([float(value) for value in returns]) / downside_deviation)
                * sqrt(_TRADING_DAYS_PER_YEAR)
            )
        )

    def _max_drawdown(self, values: list[Decimal]) -> Decimal:
        if not values:
            return Decimal("0")

        peak = values[0]
        max_drawdown = Decimal("0")
        for value in values:
            if value > peak:
                peak = value
            drawdown = self._decimal_ratio(value - peak, peak)
            if drawdown < max_drawdown:
                max_drawdown = drawdown

        return max_drawdown

    def _current_drawdown(self, values: list[Decimal]) -> Decimal:
        if not values:
            return Decimal("0")
        peak = max(values)
        return self._decimal_ratio(values[-1] - peak, peak)

    def _annualized_volatility(self, returns: list[Decimal]) -> Decimal:
        if len(returns) < 2:
            return Decimal("0")
        return Decimal(
            str(pstdev([float(value) for value in returns]) * sqrt(_TRADING_DAYS_PER_YEAR))
        )

    def _win_rate(self, returns: list[Decimal]) -> Decimal:
        if not returns:
            return Decimal("0")
        wins = sum(1 for value in returns if value > 0)
        return Decimal(wins) / Decimal(len(returns))

    def _value_at_risk_1d_95(
        self,
        returns: list[Decimal],
        values: list[Decimal],
    ) -> Decimal:
        if len(returns) < 2 or not values:
            return Decimal("0")
        daily_volatility = pstdev([float(value) for value in returns])
        var_ratio = Decimal(str(1.65 * daily_volatility))
        return abs(values[-1] * var_ratio)

    def _market_value(self, item: PositionSnapshotItem) -> Decimal:
        if item.market_value is not None:
            return Decimal(item.market_value)
        if item.market_price is not None:
            return Decimal(item.quantity) * Decimal(item.market_price)
        return Decimal("0")

    def _current_price(self, item: PositionSnapshotItem | None) -> Decimal:
        if item is None or item.market_price is None:
            return Decimal("0")
        return Decimal(item.market_price)

    def _company_name(self, symbol: str) -> str:
        return symbol

    def _strategy_for_symbol(self, symbol: str) -> str:
        stmt = (
            select(OrderIntents.strategy_id)
            .join(Fill, Fill.intent_id == OrderIntents.intent_id, isouter=True)
            .where(OrderIntents.symbol == symbol)
            .order_by(desc(OrderIntents.timestamp))
            .limit(1)
        )
        return self._session.scalars(stmt).one_or_none() or "unknown"

    def _allocation_rows(
        self,
        totals: dict[str, Decimal],
        denominator: Decimal,
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "allocated_capital": allocated_capital,
                "percent_of_portfolio": self._decimal_ratio(allocated_capital, denominator)
                * Decimal("100"),
            }
            for name, allocated_capital in sorted(totals.items())
        ]

    def _decimal_ratio(
        self,
        numerator: Decimal,
        denominator: Decimal | None,
    ) -> Decimal:
        if denominator is None or denominator == Decimal("0"):
            return Decimal("0")
        return numerator / denominator
