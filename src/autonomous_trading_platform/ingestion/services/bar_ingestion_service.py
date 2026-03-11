from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alpaca.data.models.bars import Bar

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.market.market_bar import MarketBar

from .bar_aggregation_service import BarAggregationService


class BarIngestionService:
    """
    Handle incoming provider minute bars and convert them into the
    platform's canonical MarketBar contract.
    """

    def __init__(self):
        self.aggregator = BarAggregationService()

    async def handle_minute_bar(self, provider_bar: Bar) -> None:
        minute_bar = self._convert_provider_bar(provider_bar)

        five_min_bar = self.aggregator.add_minute_bar(minute_bar)

        if five_min_bar is None:
            return

        print(five_min_bar)

    @staticmethod
    def _build_bar_id(symbol, timestamp, interval, price_basis) -> str:
        key = f"{symbol}:{interval}:{price_basis}:{timestamp.isoformat()}"
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def _convert_provider_bar(provider_bar: Bar) -> MarketBar:
        """
        Convert a provider-specific minute bar into the platform's
        canonical MarketBar model.
        """

        ts = provider_bar.timestamp

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        timestamp_utc = ts.astimezone(UTC)

        return MarketBar(
            bar_id=BarIngestionService._build_bar_id(
                symbol=provider_bar.symbol,
                timestamp=timestamp_utc,
                interval=BarInterval.ONE_MIN.value,
                price_basis=PriceBasis.RAW.value,
            ),
            timestamp=timestamp_utc,
            end_timestamp=timestamp_utc + timedelta(minutes=1),
            interval=BarInterval.ONE_MIN,
            symbol=provider_bar.symbol,
            open=Decimal(str(provider_bar.open)),
            high=Decimal(str(provider_bar.high)),
            low=Decimal(str(provider_bar.low)),
            close=Decimal(str(provider_bar.close)),
            volume=int(provider_bar.volume),
            vwap=Decimal(str(provider_bar.vwap)) if provider_bar.vwap is not None else None,
            trade_count=int(provider_bar.trade_count)
            if provider_bar.trade_count is not None
            else None,
            price_basis=PriceBasis.RAW,
            adjustment_factor=1.0,
            source="alpaca",
            ingested_at=datetime.now(UTC),
            quality_flags=[],
        )
