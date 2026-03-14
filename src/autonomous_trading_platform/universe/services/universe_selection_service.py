from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.universe.types import UniverseAssetSource


class UniverseSelectionService:
    """
    Build the eligible universe using Alpaca assets and internal liquidity metrics.
    """

    def __init__(self, session: Session, asset_source: UniverseAssetSource) -> None:
        self.session = session
        self.asset_source = asset_source

    def select_symbols(
        self,
        *,
        as_of: datetime,
        min_price: Decimal = Decimal("1.00"),
        min_average_daily_dollar_volume: Decimal = Decimal("5000000"),
        max_symbols: int = 500,
        lookback_days: int = 20,
    ) -> tuple[list[str], dict[str, Any]]:
        as_of = self._ensure_utc(as_of)

        candidates: list[dict[str, Any]] = []

        for asset in self._fetch_candidate_assets():
            symbol = asset["symbol"]
            metrics = self._load_liquidity_metrics(
                symbol=symbol,
                as_of=as_of,
                lookback_days=lookback_days,
            )
            if metrics is None:
                continue

            last_price = metrics["last_price"]
            average_daily_dollar_volume = metrics["average_daily_dollar_volume"]

            if last_price < min_price:
                continue

            if average_daily_dollar_volume < min_average_daily_dollar_volume:
                continue

            candidates.append(
                {
                    "symbol": symbol,
                    "last_price": last_price,
                    "average_daily_dollar_volume": average_daily_dollar_volume,
                }
            )

        candidates.sort(
            key=lambda row: (
                row["average_daily_dollar_volume"],
                row["symbol"],
            ),
            reverse=True,
        )

        selected_symbols = sorted(row["symbol"] for row in candidates[:max_symbols])

        criteria = {
            "min_price": str(min_price),
            "min_average_daily_dollar_volume": str(min_average_daily_dollar_volume),
            "max_symbols": max_symbols,
            "lookback_days": lookback_days,
            "selection_timestamp": as_of.isoformat(),
            "asset_source": "alpaca_assets",
            "liquidity_source": "market_bars",
        }

        return selected_symbols, criteria

    def _fetch_candidate_assets(self) -> list[dict[str, Any]]:
        assets = self.asset_source.list_assets()

        candidates: list[dict[str, Any]] = []
        for asset in assets:
            if not asset.symbol:
                continue
            if not asset.tradable:
                continue
            if asset.status != "active":
                continue
            if asset.asset_class != "us_equity":
                continue

            candidates.append({"symbol": asset.symbol})

        return candidates

    def _load_liquidity_metrics(
        self,
        *,
        symbol: str,
        as_of: datetime,
        lookback_days: int,
    ) -> dict[str, Decimal] | None:
        """
        Load liquidity metrics from stored 5-minute bars by rolling them up to daily values.
        """
        as_of = self._ensure_utc(as_of)
        window_start = as_of - timedelta(days=lookback_days * 3)

        stmt = (
            select(
                MarketBar.timestamp,
                MarketBar.close,
                MarketBar.volume,
            )
            .where(
                MarketBar.symbol == symbol,
                MarketBar.interval == BarInterval.FIVE_MIN,
                MarketBar.timestamp <= as_of,
                MarketBar.timestamp >= window_start,
            )
            .order_by(MarketBar.timestamp.asc())
        )

        rows = self.session.execute(stmt).all()
        if not rows:
            return None

        daily_dollar_volume: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
        daily_last_close: dict[date, Decimal] = {}

        for ts, close, volume in rows:
            if close is None or volume is None:
                continue

            bar_close = Decimal(close)
            bar_volume = Decimal(volume)
            trade_date = ts.date()

            daily_dollar_volume[trade_date] += bar_close * bar_volume
            daily_last_close[trade_date] = bar_close

        available_dates = sorted(daily_last_close.keys())
        if len(available_dates) < lookback_days:
            return None

        selected_dates = available_dates[-lookback_days:]

        last_date = selected_dates[-1]
        last_price = daily_last_close[last_date]
        average_daily_dollar_volume = sum(daily_dollar_volume[d] for d in selected_dates) / Decimal(
            len(selected_dates)
        )

        return {
            "last_price": last_price,
            "average_daily_dollar_volume": average_daily_dollar_volume,
        }

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
