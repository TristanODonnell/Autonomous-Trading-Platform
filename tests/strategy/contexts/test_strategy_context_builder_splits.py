"""Strategy history is split-adjusted on read and read across daily dataset versions
(plan 5d-D): bars before an ex-date are scaled, bars after are untouched, a future
split is never applied, two splits compose, versions merge by timestamp, and recent
closes for volatility scaling are adjusted the same way."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pyarrow as pa
import pytest

from autonomous_trading_platform.accounting.corporate_actions import StaticSplitSource
from autonomous_trading_platform.contracts.common.enums import CorporateActionType, PriceBasis
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)

_EX = date(2024, 6, 10)


def _split(
    action_id: str, ratio: str, ex_date: date = _EX, symbol: str = "NVDA"
) -> CorporateAction:
    return CorporateAction(
        action_id=action_id,
        symbol=symbol,
        action_type=CorporateActionType.SPLIT_FORWARD,
        effective_date=ex_date,
        split_ratio=Decimal(ratio),
        cash_amount=None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


def _table(rows: list[tuple[datetime, float, int]], symbol: str = "NVDA") -> pa.Table:
    """A bar table in the shape the Parquet reader returns."""
    if not rows:
        return pa.table({})
    return pa.table(
        {
            "bar_id": [f"{symbol}-{ts.isoformat()}" for ts, _, _ in rows],
            "timestamp": pa.array([ts for ts, _, _ in rows], type=pa.timestamp("us", tz="UTC")),
            "end_timestamp": pa.array(
                [ts + timedelta(minutes=5) for ts, _, _ in rows], type=pa.timestamp("us", tz="UTC")
            ),
            "interval": ["5m"] * len(rows),
            "symbol": [symbol] * len(rows),
            "open": [price for _, price, _ in rows],
            "high": [price + 1 for _, price, _ in rows],
            "low": [price - 1 for _, price, _ in rows],
            "close": [price for _, price, _ in rows],
            "volume": [vol for _, _, vol in rows],
            "vwap": [price for _, price, _ in rows],
            "trade_count": [10] * len(rows),
            "price_basis": ["raw"] * len(rows),
            "adjustment_factor": [1.0] * len(rows),
            "source": ["test"] * len(rows),
            "ingested_at": pa.array([ts for ts, _, _ in rows], type=pa.timestamp("us", tz="UTC")),
            "quality_flags": [[] for _ in rows],
            "session": ["regular"] * len(rows),
        }
    )


class _Reader:
    """Serves bar tables per (dataset_version, symbol) inside the requested window."""

    def __init__(self, tables: dict[str, dict[str, list[tuple[datetime, float, int]]]]) -> None:
        self._tables = tables
        self.calls: list[tuple[str, str, date, date]] = []

    def read(
        self,
        *,
        dataset: Any,
        dataset_version: str,
        symbol: str,
        start_date: date,
        end_date: date,
        **_: Any,
    ) -> pa.Table:
        self.calls.append((dataset_version, symbol, start_date, end_date))
        rows = [
            r
            for r in self._tables.get(dataset_version, {}).get(symbol, [])
            if start_date <= r[0].date() <= end_date
        ]
        return _table(rows, symbol)


def _bars(
    day: date, prices: list[float], *, volume: int = 100
) -> list[tuple[datetime, float, int]]:
    base = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    return [(base + timedelta(minutes=5 * i), p, volume) for i, p in enumerate(prices)]


@pytest.fixture
def pre_and_post_split_bars() -> dict[str, list[tuple[datetime, float, int]]]:
    return {
        "NVDA": _bars(date(2024, 6, 7), [1200.0, 1210.0, 1205.0])
        + _bars(date(2024, 6, 10), [120.5, 121.0, 121.5])
    }


class TestAdjustOnRead:
    def test_bars_before_the_ex_date_are_scaled_after_it_untouched(
        self, pre_and_post_split_bars
    ) -> None:
        reader = _Reader({"v": pre_and_post_split_bars})
        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            lookback_bars=5,
            dataset_version="v",
            split_source=StaticSplitSource([_split("s1", "10")]),
        )
        at = datetime(2024, 6, 10, 13, 45, tzinfo=UTC)

        context = builder.build(
            run_id=uuid4(),
            strategy_id="x",
            symbol="NVDA",
            bar_timestamp=at,
            evaluation_timestamp=at,
        )

        assert context is not None
        # bars strictly before 13:45: June 7 ×3 then June 10 13:30/13:35/13:40 → last 5
        closes = [b.close for b in context.bars]
        assert closes == [
            Decimal("121.0"),
            Decimal("120.5"),
            Decimal("120.5"),
            Decimal("121.0"),
            Decimal("121.5"),
        ]
        assert [b.volume for b in context.bars] == [1000, 1000, 100, 100, 100]
        assert context.bars[0].price_basis is PriceBasis.ADJUSTED
        assert context.bars[0].adjustment_factor == Decimal("0.1")
        assert context.bars[2].price_basis is PriceBasis.RAW

    def test_a_future_split_is_never_applied(self, pre_and_post_split_bars) -> None:
        """Evaluating on June 7 must not see the June 10 split (no lookahead)."""
        reader = _Reader({"v": pre_and_post_split_bars})
        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            lookback_bars=2,
            dataset_version="v",
            split_source=StaticSplitSource([_split("s1", "10")]),
        )
        at = datetime(2024, 6, 7, 13, 45, tzinfo=UTC)
        context = builder.build(
            run_id=uuid4(),
            strategy_id="x",
            symbol="NVDA",
            bar_timestamp=at,
            evaluation_timestamp=at,
        )
        assert context is not None
        assert [b.close for b in context.bars] == [Decimal("1210.0"), Decimal("1205.0")]
        assert context.bars[0].price_basis is PriceBasis.RAW

    def test_two_splits_compose(self) -> None:
        bars = {
            "NVDA": _bars(date(2024, 6, 3), [4000.0])
            + _bars(date(2024, 6, 5), [1000.0])
            + _bars(date(2024, 6, 10), [100.0, 101.0])
        }
        reader = _Reader({"v": bars})
        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            lookback_bars=4,
            dataset_version="v",
            split_source=StaticSplitSource(
                [
                    _split("s1", "4", ex_date=date(2024, 6, 4)),
                    _split("s2", "10", ex_date=date(2024, 6, 10)),
                ]
            ),
        )
        at = datetime(2024, 6, 10, 13, 40, tzinfo=UTC)
        context = builder.build(
            run_id=uuid4(),
            strategy_id="x",
            symbol="NVDA",
            bar_timestamp=at,
            evaluation_timestamp=at,
        )
        assert context is not None
        # June 3 bar: both splits (÷40); June 5 bar: only the June 10 split (÷10)
        assert [b.close for b in context.bars] == [
            Decimal("100.0"),
            Decimal("100.0"),
            Decimal("100.0"),
            Decimal("101.0"),
        ]

    def test_without_a_split_source_bars_are_raw(self, pre_and_post_split_bars) -> None:
        reader = _Reader({"v": pre_and_post_split_bars})
        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            lookback_bars=4,
            dataset_version="v",
        )
        at = datetime(2024, 6, 10, 13, 40, tzinfo=UTC)
        context = builder.build(
            run_id=uuid4(),
            strategy_id="x",
            symbol="NVDA",
            bar_timestamp=at,
            evaluation_timestamp=at,
        )
        assert context is not None
        assert context.bars[0].close == Decimal("1210.0")


class TestDailyVersions:
    def test_reads_every_version_in_the_window_and_merges_by_timestamp(self) -> None:
        day1 = _bars(date(2024, 6, 7), [1200.0, 1210.0])
        day2 = _bars(date(2024, 6, 10), [120.5, 121.0])
        # the second version re-ingested the last bar of day 1 with a corrected close
        day1_fixed = [day1[1][:1] + (1211.0, 100)]
        reader = _Reader({"d1": {"NVDA": day1}, "d1b": {"NVDA": day1_fixed}, "d2": {"NVDA": day2}})
        resolver_calls: list[tuple[date, date]] = []

        def resolver(start: date, end: date) -> list[str]:
            resolver_calls.append((start, end))
            return ["d1", "d1b", "d2"]

        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            lookback_bars=4,
            dataset_version="unused",
            dataset_version_resolver=resolver,
            split_source=StaticSplitSource([_split("s1", "10")]),
        )
        at = datetime(2024, 6, 10, 13, 45, tzinfo=UTC)

        context = builder.build(
            run_id=uuid4(),
            strategy_id="x",
            symbol="NVDA",
            bar_timestamp=at,
            evaluation_timestamp=at,
        )

        assert context is not None
        assert [c[0] for c in reader.calls] == ["d1", "d1b", "d2"]
        assert resolver_calls and resolver_calls[0][1] == date(2024, 6, 10)
        # merged, de-duplicated (later version wins), ordered, split-adjusted
        assert [b.close for b in context.bars] == [
            Decimal("120.0"),
            Decimal("121.1"),
            Decimal("120.5"),
            Decimal("121.0"),
        ]

    def test_resolver_returning_nothing_falls_back_to_the_named_version(self) -> None:
        reader = _Reader({"v": {"NVDA": _bars(date(2024, 6, 10), [1.0, 2.0])}})
        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            lookback_bars=1,
            dataset_version="v",
            dataset_version_resolver=lambda s, e: [],
        )
        at = datetime(2024, 6, 10, 13, 40, tzinfo=UTC)
        context = builder.build(
            run_id=uuid4(),
            strategy_id="x",
            symbol="NVDA",
            bar_timestamp=at,
            evaluation_timestamp=at,
        )
        assert context is not None and context.bars[0].close == Decimal("2.0")


class TestRecentCloses:
    def test_recent_closes_are_split_adjusted(self, pre_and_post_split_bars) -> None:
        reader = _Reader({"v": pre_and_post_split_bars})
        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            lookback_bars=5,
            dataset_version="v",
            split_source=StaticSplitSource([_split("s1", "10")]),
        )
        closes = builder.recent_closes(
            symbol="NVDA", before=datetime(2024, 6, 10, 13, 40, tzinfo=UTC), lookback_bars=4
        )
        assert closes == pytest.approx([121.0, 120.5, 120.5, 121.0])

    def test_recent_closes_without_bars_is_empty(self) -> None:
        reader = _Reader({})
        builder = StrategyContextBuilder(
            market_bar_reader=reader,  # type: ignore[arg-type]
            bars_dataset=RAW_BARS_DATASET,
            dataset_version="v",
        )
        assert (
            builder.recent_closes(
                symbol="NVDA", before=datetime(2024, 6, 10, tzinfo=UTC), lookback_bars=3
            )
            == []
        )
