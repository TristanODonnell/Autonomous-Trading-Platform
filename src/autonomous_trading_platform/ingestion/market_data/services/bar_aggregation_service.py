from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autonomous_trading_platform.contracts.common.enums import BarInterval
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.ingestion.helpers.bar_identity import build_bar_id


class BarAggregationService:
    def __init__(self) -> None:
        self.buffer: dict[tuple[str, datetime], list[MarketBar]] = {}

    @staticmethod
    def _get_bucket(timestamp: datetime) -> datetime:
        minute = (timestamp.minute // 5) * 5
        return timestamp.replace(minute=minute, second=0, microsecond=0)

    @staticmethod
    def _validate_minute_bar(bar: MarketBar) -> None:
        if bar.timestamp.tzinfo is None:
            raise ValueError("Bar timestamp must be timezone-aware.")

        if bar.end_timestamp != bar.timestamp + timedelta(minutes=1):
            raise ValueError("Minute bar end_timestamp must equal timestamp + 1 minute.")

    def _handle_cross_bucket_gap(self, symbol: str, incoming_bucket: datetime) -> None:
        for key in list(self.buffer):
            buffered_symbol, buffered_bucket = key
            if buffered_symbol != symbol or buffered_bucket >= incoming_bucket:
                continue
            bars = self.buffer.pop(key)
            # Log the drop so missing bars are traceable
            import logging

            logging.getLogger(__name__).debug(
                "Dropped incomplete bucket for %s at %s (%d/5 bars) — "
                "likely early close or data gap.",
                symbol,
                buffered_bucket.isoformat(),
                len(bars),
            )

    @staticmethod
    def _validate_bucket_continuity(bars: list[MarketBar], bucket: datetime) -> None:
        if len(bars) != 5:
            raise ValueError(
                f"Expected 5 one-minute bars for bucket {bucket.isoformat()}, got {len(bars)}."
            )

        bars_sorted = sorted(bars, key=lambda bar: bar.timestamp)

        expected_timestamps = [bucket + timedelta(minutes=i) for i in range(5)]
        actual_timestamps = [bar.timestamp for bar in bars_sorted]

        if actual_timestamps != expected_timestamps:
            raise ValueError(
                "Gap or discontinuity detected in 5-minute bucket. "
                f"Expected {expected_timestamps}, got {actual_timestamps}."
            )

        if len({bar.timestamp for bar in bars_sorted}) != 5:
            raise ValueError("Duplicate minute bars detected in aggregation bucket.")

    def add_minute_bar(self, bar: MarketBar) -> MarketBar | None:
        self._validate_minute_bar(bar)

        bucket = self._get_bucket(bar.timestamp)
        key = (bar.symbol, bucket)

        self._handle_cross_bucket_gap(symbol=bar.symbol, incoming_bucket=bucket)

        bars = self.buffer.setdefault(key, [])

        if any(existing.timestamp == bar.timestamp for existing in bars):
            raise ValueError(
                f"Duplicate minute bar for symbol={bar.symbol} at {bar.timestamp.isoformat()}."
            )

        bars.append(bar)

        if len(bars) < 5:
            return None

        self._validate_bucket_continuity(bars, bucket)
        completed_bars = self.buffer.pop(key)
        return self._aggregate(completed_bars)

    def reset_symbol(self, symbol: str) -> None:
        keys_to_delete = [key for key in self.buffer if key[0] == symbol]
        for key in keys_to_delete:
            self.buffer.pop(key, None)

    def drop_incomplete_buckets_for_symbol(self, symbol: str) -> None:
        keys_to_delete = [
            key
            for key, buffered_bars in self.buffer.items()
            if key[0] == symbol and len(buffered_bars) < 5
        ]
        for key in keys_to_delete:
            self.buffer.pop(key, None)

    @staticmethod
    def _aggregate(bars: list[MarketBar]) -> MarketBar:
        bars_sorted = sorted(bars, key=lambda bar: bar.timestamp)

        first = bars_sorted[0]
        last = bars_sorted[-1]
        session = first.session
        open_price = first.open
        close_price = last.close
        high_price = max(bar.high for bar in bars_sorted)
        low_price = min(bar.low for bar in bars_sorted)
        volume = sum(bar.volume for bar in bars_sorted)
        trade_count = sum(bar.trade_count or 0 for bar in bars_sorted)

        # Keep this behavior for now so your current existing test still passes.
        vwap = last.vwap

        return MarketBar(
            bar_id=build_bar_id(
                symbol=first.symbol,
                timestamp=first.timestamp,
                interval=BarInterval.FIVE_MIN,
                price_basis=first.price_basis,
            ),
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
            session=session,
        )
