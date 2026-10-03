"""The feature pipeline reads raw bars and split-adjusts history on read (plan 5d-D),
with the same no-lookahead rule as the strategy context: a split whose ex-date is after
the window end is never applied."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pytest

from autonomous_trading_platform.accounting.corporate_actions import StaticSplitSource
from autonomous_trading_platform.contracts.common.enums import CorporateActionType, PriceBasis
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.feature_engineering.services.feature_dataset_resolver_service import (
    FeatureDatasetResolverService,
)

_EX = date(2024, 6, 10)


def _split(ratio: str, ex_date: date = _EX) -> CorporateAction:
    return CorporateAction(
        action_id=f"nvda-{ex_date.isoformat()}",
        symbol="NVDA",
        action_type=CorporateActionType.SPLIT_FORWARD,
        effective_date=ex_date,
        split_ratio=Decimal(ratio),
        cash_amount=None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


def _table(rows: list[tuple[datetime, float, int]]) -> pa.Table:
    return pa.table(
        {
            "timestamp": pa.array([ts for ts, _, _ in rows], type=pa.timestamp("us", tz="UTC")),
            "symbol": ["NVDA"] * len(rows),
            "open": [p for _, p, _ in rows],
            "high": [p + 1 for _, p, _ in rows],
            "low": [p - 1 for _, p, _ in rows],
            "close": [p for _, p, _ in rows],
            "volume": [v for _, _, v in rows],
            "vwap": [p for _, p, _ in rows],
            "price_basis": ["raw"] * len(rows),
            "adjustment_factor": [1.0] * len(rows),
        }
    )


class _Reader:
    def __init__(self, rows: list[tuple[datetime, float, int]]) -> None:
        self._rows = rows

    def read(self, *, start_date: date, end_date: date, **_: Any) -> pa.Table:
        return _table([r for r in self._rows if start_date <= r[0].date() <= end_date])


def _bar(day: date, price: float) -> tuple[datetime, float, int]:
    return (datetime(day.year, day.month, day.day, 20, tzinfo=UTC), price, 100)


@pytest.fixture
def rows() -> list[tuple[datetime, float, int]]:
    return [_bar(date(2024, 6, 6), 1200.0), _bar(date(2024, 6, 7), 1210.0), _bar(_EX, 121.0)]


def _service(rows, splits) -> FeatureDatasetResolverService:
    return FeatureDatasetResolverService(
        dataset_registration_service=object(),  # type: ignore[arg-type]
        parquet_reader=_Reader(rows),
        split_source=StaticSplitSource(splits) if splits is not None else None,
    )


def test_bars_before_the_ex_date_are_scaled_in_the_loaded_frame(rows) -> None:
    frame = _service(rows, [_split("10")]).load_bars_frame(
        dataset_version_id="v",
        price_basis=PriceBasis.RAW,
        symbols=["NVDA"],
        start_date=date(2024, 6, 1),
        end_date=_EX,
    )
    assert frame["close"].tolist() == [120.0, 121.0, 121.0]
    assert frame["volume"].tolist() == [1000, 1000, 100]
    assert frame["adjustment_factor"].tolist() == pytest.approx([0.1, 0.1, 1.0])
    assert frame["price_basis"].tolist() == ["adjusted", "adjusted", "raw"]


def test_a_split_after_the_window_end_is_not_applied(rows) -> None:
    frame = _service(rows, [_split("10")]).load_bars_frame(
        dataset_version_id="v",
        price_basis=PriceBasis.RAW,
        symbols=["NVDA"],
        start_date=date(2024, 6, 1),
        end_date=_EX - timedelta(days=1),
    )
    assert frame["close"].tolist() == [1200.0, 1210.0]
    assert frame["price_basis"].tolist() == ["raw", "raw"]


def test_without_a_split_source_the_frame_is_raw(rows) -> None:
    frame = _service(rows, None).load_bars_frame(
        dataset_version_id="v",
        price_basis=PriceBasis.RAW,
        symbols=["NVDA"],
        start_date=date(2024, 6, 1),
        end_date=_EX,
    )
    assert frame["close"].tolist() == [1200.0, 1210.0, 121.0]
