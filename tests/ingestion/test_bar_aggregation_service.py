from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
)
from autonomous_trading_platform.ingestion.market_data.services.bar_aggregation_service import (
    BarAggregationService,
)
from tests.utilities.factories import make_minute_bar


class TestBarAggregationService:
    def test_add_minute_bar_aggregates_five_consecutive_bars(self) -> None:
        service = BarAggregationService()
        start = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)

        bars = [
            make_minute_bar(
                timestamp=start + timedelta(minutes=0),
                open_price="100.00",
                high_price="101.00",
                low_price="99.50",
                close_price="100.50",
                volume=100,
                trade_count=10,
                vwap="100.25",
            ),
            make_minute_bar(
                timestamp=start + timedelta(minutes=1),
                open_price="100.50",
                high_price="102.00",
                low_price="100.00",
                close_price="101.50",
                volume=200,
                trade_count=20,
                vwap="101.10",
            ),
            make_minute_bar(
                timestamp=start + timedelta(minutes=2),
                open_price="101.50",
                high_price="101.75",
                low_price="98.75",
                close_price="99.25",
                volume=300,
                trade_count=30,
                vwap="100.00",
            ),
            make_minute_bar(
                timestamp=start + timedelta(minutes=3),
                open_price="99.25",
                high_price="100.00",
                low_price="98.50",
                close_price="99.75",
                volume=400,
                trade_count=40,
                vwap="99.20",
            ),
            make_minute_bar(
                timestamp=start + timedelta(minutes=4),
                open_price="99.75",
                high_price="103.00",
                low_price="99.00",
                close_price="102.25",
                volume=500,
                trade_count=50,
                vwap="101.75",
            ),
        ]

        result = None
        for bar in bars:
            result = service.add_minute_bar(bar)

        assert result is not None
        assert result.symbol == "AAPL"
        assert result.interval == BarInterval.FIVE_MIN

        # Aggregation checks
        assert result.open == Decimal("100.00")
        assert result.high == Decimal("103.00")
        assert result.low == Decimal("98.50")
        assert result.close == Decimal("102.25")
        assert result.volume == 1500
        assert result.trade_count == 150

        # Current implementation uses last bar VWAP
        assert result.vwap == Decimal("101.75")

    def test_add_minute_bar_clears_buffer_after_emission(self) -> None:
        service = BarAggregationService()
        start = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)
        key = ("AAPL", start)

        first_batch = [make_minute_bar(timestamp=start + timedelta(minutes=i)) for i in range(5)]

        result = None
        for bar in first_batch:
            result = service.add_minute_bar(bar)

        assert result is not None
        assert key not in service.buffer

        # Feed a new bucket and ensure it starts fresh rather than reusing old bars
        next_bucket_start = start + timedelta(minutes=5)
        assert service.add_minute_bar(make_minute_bar(timestamp=next_bucket_start)) is None
        assert key not in service.buffer
        assert ("AAPL", next_bucket_start) in service.buffer
        assert len(service.buffer[("AAPL", next_bucket_start)]) == 1

    def test_add_minute_bar_uses_first_bar_timestamp_for_aggregated_bar(self) -> None:
        service = BarAggregationService()
        start = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)

        result = None
        for i in range(5):
            result = service.add_minute_bar(make_minute_bar(timestamp=start + timedelta(minutes=i)))

        assert result is not None
        assert result.timestamp == start
        assert result.end_timestamp == start + timedelta(minutes=5)

    def test_add_minute_bar_raises_on_duplicate_bar_in_bucket(self) -> None:
        service = BarAggregationService()
        start = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)

        # Feed 4 bars skipping minute 2
        for i in [0, 1, 3, 4]:
            service.add_minute_bar(make_minute_bar(timestamp=start + timedelta(minutes=i)))

        # Adding a duplicate raises before continuity check even runs
        with pytest.raises(ValueError):
            service.add_minute_bar(make_minute_bar(timestamp=start + timedelta(minutes=0)))

    def test_add_minute_bar_flushes_incomplete_prior_bucket_on_cross_bucket_gap(self) -> None:
        service = BarAggregationService()
        start = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)

        for i in range(4):
            service.add_minute_bar(make_minute_bar(timestamp=start + timedelta(minutes=i)))

        # Cross-bucket bar should flush the incomplete prior bucket silently
        next_bar = make_minute_bar(timestamp=start + timedelta(minutes=5))
        result = service.add_minute_bar(next_bar)

        assert result is None  # new bucket started, not yet complete
        assert ("AAPL", start) not in service.buffer  # prior incomplete bucket flushed
        assert ("AAPL", start + timedelta(minutes=5)) in service.buffer
