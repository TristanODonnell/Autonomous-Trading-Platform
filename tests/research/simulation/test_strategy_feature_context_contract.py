from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pyarrow as pa  # type: ignore[import-untyped]

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    MarketSession,
    PriceBasis,
)
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowData,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)


class _UnusedReader:
    pass


def _bar(timestamp: datetime, close: float) -> MarketBar:
    price = Decimal(str(close))
    return MarketBar(
        bar_id=f"AAPL-{timestamp.isoformat()}",
        symbol="AAPL",
        timestamp=timestamp,
        end_timestamp=timestamp + timedelta(minutes=5),
        interval=BarInterval.FIVE_MIN,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1000,
        vwap=price,
        trade_count=1,
        price_basis=PriceBasis.RAW,
        adjustment_factor=Decimal("1.0"),
        source="test",
        ingested_at=timestamp,
        quality_flags=[],
        session=MarketSession.REGULAR,
    )


def test_strategy_context_exposes_loaded_simulation_feature_tables() -> None:
    ts0 = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    ts1 = ts0 + timedelta(minutes=5)
    feature_table = pa.table(
        {
            "symbol": ["AAPL"],
            "timestamp": [ts0],
            "ret_1d": [0.01],
        }
    )
    window = SimulationWindowData(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        dataset_version="bars-v1",
        symbols=["AAPL"],
        timeline=[ts0, ts1],
        bars_by_symbol={"AAPL": [_bar(ts0, 100.0), _bar(ts1, 101.0)]},
        bars_by_timestamp={ts0: {"AAPL": _bar(ts0, 100.0)}, ts1: {"AAPL": _bar(ts1, 101.0)}},
        feature_tables_by_symbol={"AAPL": {"returns": feature_table}},
    )
    builder = StrategyContextBuilder(
        market_bar_reader=cast(HistoricalBarDatasetReader, _UnusedReader()),
        bars_dataset=RAW_BARS_DATASET,
        lookback_bars=1,
    )

    context = builder.build_from_window(
        run_id=uuid4(),
        strategy_id="strategy-1",
        symbol="AAPL",
        timestamp=ts1,
        window=window,
        positions={},
        state={},
    )

    assert context is not None
    assert context.features == {"returns": feature_table}


def test_strategy_context_features_defaults_to_empty_when_absent() -> None:
    ts0 = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    ts1 = ts0 + timedelta(minutes=5)
    window = SimulationWindowData(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        dataset_version="bars-v1",
        symbols=["AAPL"],
        timeline=[ts0, ts1],
        bars_by_symbol={"AAPL": [_bar(ts0, 100.0), _bar(ts1, 101.0)]},
        bars_by_timestamp={ts0: {"AAPL": _bar(ts0, 100.0)}, ts1: {"AAPL": _bar(ts1, 101.0)}},
        feature_tables_by_symbol={},
    )
    builder = StrategyContextBuilder(
        market_bar_reader=cast(HistoricalBarDatasetReader, _UnusedReader()),
        bars_dataset=RAW_BARS_DATASET,
        lookback_bars=1,
    )

    context = builder.build_from_window(
        run_id=uuid4(),
        strategy_id="strategy-1",
        symbol="AAPL",
        timestamp=ts1,
        window=window,
        positions={},
        state={},
    )

    assert context is not None
    assert context.features == {}
