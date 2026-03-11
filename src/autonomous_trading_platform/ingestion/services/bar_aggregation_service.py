from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autonomous_trading_platform.contracts.common.enums import BarInterval
from autonomous_trading_platform.contracts.market.market_bar import MarketBar


class BarAggregationService:
    def __init__(self):
        self.buffer = {}

    @staticmethod
    def _get_bucket(timestamp: datetime) -> datetime:
        minute = (timestamp.minute // 5) * 5
        return timestamp.replace(minute=minute, second=0, microsecond=0)

    def add_minute_bar(self, bar: MarketBar):
        bucket = BarAggregationService._get_bucket(bar.timestamp)

        self.buffer.setdefault(bucket, []).append(bar)

        if len(self.buffer[bucket]) < 5:
            return None

        bars = self.buffer.pop(bucket)

        return self._aggregate(bars)

    @staticmethod
    def _aggregate(bars: list[MarketBar]) -> MarketBar:
        first = bars[0]
        last = bars[-1]

        open_price = first.open
        close_price = last.close

        high_price = max(bar.high for bar in bars)
        low_price = min(bar.low for bar in bars)

        volume = sum(bar.volume for bar in bars)

        trade_count = sum(bar.trade_count for bar in bars if bar.trade_count is not None)

        vwap = last.vwap

        return MarketBar(
            bar_id="TODO_BUILD_BAR_ID",
            timestamp=first.timestamp,
            end_timestamp=first.timestamp + timedelta(minutes=5),
            interval=BarInterval.FIVE_MIN,
            symbol=first.symbol,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume,
            vwap=vwap,
            trade_count=trade_count,
            price_basis=first.price_basis,
            adjustment_factor=first.adjustment_factor,
            source="aggregation",
            ingested_at=datetime.now(UTC),
            quality_flags=[],
        )
