"""Synthetic daily bars on disk and the real simulation context that reads them.

For tests that run research stages on real simulations (research plan R7). Each
"shape" is one price path for three symbols, stored as its own dataset version,
so one test can run the same strategies on two different markets and compare.

The bars are daily, and the platform annualises with the five-minute bar count,
so a Sharpe measured here is several times too large. Its sign is right, and
the tests that use this module only lean on which side of a threshold a number
falls.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.metadata import attach_metadata, build_metadata
from autonomous_trading_platform.storage.parquet.paths import partition_file_path
from autonomous_trading_platform.storage.parquet.schemas import BAR_SCHEMA
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

SYMBOLS = ["AAPL", "MSFT", "SPY"]
START = date(2023, 1, 3)
END = date(2023, 5, 31)
# The research tick. The hook looks back 90 days from it: 2023-03-02 to 2023-05-31.
TICK = datetime(2023, 5, 31, 21, 0, tzinfo=UTC)

_DAILY_DRIFT = 0.004
_DAILY_NOISE = 0.01


def _turns(share_rising: float, *, rises_first: bool) -> Callable[[int], np.ndarray]:
    def drift(n_days: int) -> np.ndarray:
        first = np.arange(n_days) < int(n_days * share_rising)
        up_days = first if rises_first else ~first
        return np.where(up_days, _DAILY_DRIFT, -_DAILY_DRIFT)

    return drift


# The drift of every symbol's close, day by day. The trend turns once; where it
# turns decides what the hook's 90-day window sees.
SHAPES: dict[str, Callable[[int], np.ndarray]] = {
    # Turns down in mid March: the window is nearly all falling prices.
    "rises_then_falls": _turns(0.5, rises_first=True),
    # The mirror image: the window is nearly all rising prices.
    "falls_then_rises": _turns(0.5, rises_first=False),
    # Turns down in mid April, inside the window: the early days of the window
    # rise and the late days fall, so what worked in a train window stops
    # working in the test window after it.
    "turns_late": _turns(0.7, rises_first=True),
}


@contextmanager
def short_data_root() -> Iterator[Path]:
    """A temporary folder with a short path, removed afterwards.

    A run's output path is about 190 characters below its data root (experiment,
    run id, stage name, window role), and Windows without long-path support
    stops at 260. pytest's tmp_path is about 95 characters on its own, which is
    too many; this one is about 45.
    """
    with tempfile.TemporaryDirectory(prefix="rr_", ignore_cleanup_errors=True) as name:
        yield Path(name)


def trading_days(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def dataset_version_of(shape: str, *, with_gaps: bool = False) -> str:
    return f"real_run_{shape}_gaps_v1" if with_gaps else f"real_run_{shape}_v1"


def closes(symbol: str, shape: str) -> dict[date, float]:
    """Every day's close of one symbol in one shape, gaps or no gaps."""
    return {bar["date"]: bar["close"] for bar in _bars(symbol, shape)}


def _bars(symbol: str, shape: str) -> list[dict[str, Any]]:
    days = trading_days(START, END)
    # Seeded by the symbol alone: every shape has the same noise, a different trend.
    noise = np.random.default_rng(sum(map(ord, symbol))).normal(0, _DAILY_NOISE, len(days))
    closes = 100.0 * np.cumprod(1 + SHAPES[shape](len(days)) + noise)
    bars = []
    for i, (day, close) in enumerate(zip(days, closes, strict=True)):
        opened = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
        bars.append(
            {
                "bar_id": f"{symbol}_{day.isoformat()}_1d",
                "timestamp": opened,
                "end_timestamp": opened + timedelta(hours=6, minutes=30),
                "interval": BarInterval.ONE_DAY.value,
                "symbol": symbol,
                "open": float(close) * 0.998,
                "high": float(close) * 1.01,
                "low": float(close) * 0.99,
                "close": float(close),
                "volume": 1_000_000 + i * 5_000,
                "vwap": float(close),
                "trade_count": 50_000,
                "price_basis": PriceBasis.RAW.value,
                "adjustment_factor": 1.0,
                "source": "real_run_support",
                "ingested_at": datetime(2023, 1, 1, tzinfo=UTC),
                "quality_flags": None,
                "date": day,
                "year": f"{day.year:04d}",
                "month": f"{day.month:02d}",
            }
        )
    return bars


def seed_dataset(
    data_root: Path,
    session: Any,
    shape: str,
    *,
    no_bar_on: Mapping[str, Collection[date]] | None = None,
) -> str:
    """Write one shape's bars under data_root and register its dataset version.

    ``no_bar_on`` leaves a symbol's bar out on the days given, the way a thinly
    traded symbol has no bar at a timestamp where others do. The other bars are
    unchanged, and the dataset gets a version of its own.
    """
    no_bar_on = no_bar_on or {}
    version = dataset_version_of(shape, with_gaps=bool(no_bar_on))
    for symbol in SYMBOLS:
        left_out = set(no_bar_on.get(symbol, ()))
        by_month: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for bar in _bars(symbol, shape):
            if bar["date"] in left_out:
                continue
            by_month.setdefault((bar["year"], bar["month"]), []).append(bar)
        for (year, month), rows in by_month.items():
            path = partition_file_path(
                base_path=data_root,
                dataset=RAW_BARS_DATASET,
                dataset_version=version,
                partitions={"symbol": symbol, "year": year, "month": month},
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            table = pa.table(
                {field.name: [row.get(field.name) for row in rows] for field in BAR_SCHEMA},
                schema=BAR_SCHEMA,
            )
            meta = build_metadata(
                dataset=RAW_BARS_DATASET,
                dataset_version=version,
                ingestion_timestamp=TICK.isoformat(),
                checksum=f"{version}_checksum",
            )
            pq.write_table(table.cast(attach_metadata(table.schema, meta)), path)
    session.add(
        DatasetVersions(
            dataset_version_id=version,
            dataset_name="raw_bars",
            created_at=TICK,
            source="real_run_support",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.ONE_DAY,
            schema_version=RAW_BARS_DATASET.schema_version,
            symbol_coverage=len(SYMBOLS),
            date_coverage_start=START,
            date_coverage_end=END,
            validation_status="validated",
            checksum=f"{version}_checksum",
        )
    )
    session.flush()
    return version


def build_context(session: Any, data_root: Path) -> Any:
    """The real simulation context (real SimulationRunner), reading and writing under data_root.

    Left alone, the context reads bars from ``data/`` under the working directory
    and writes every run's output files there too.
    """
    from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
        build_simulation_context,
    )

    ctx = build_simulation_context(session=session, universe_size=len(SYMBOLS))
    ctx.simulation_runner.result_recorder.parquet_simulation_repository.base_path = data_root
    ctx.bar_reader.base_path = data_root
    ctx.dataset_resolver.base_path = data_root
    ctx.window_loader.bar_reader.base_path = data_root
    feature_reader = getattr(ctx.window_loader, "feature_reader", None)
    if feature_reader is not None:
        feature_reader.base_path = data_root
    ctx.simulation_runner.dataset_resolver.base_path = data_root
    return ctx


# One fully specified config per family: what one random draw per family gave with the
# default seed when the real-run tests were written, before research plan G2 (the draw)
# and G3 (momentum draws returns) changed what a draw gives. The real-run tests measure
# the pipeline, not the draw, so they run these with ``parameter_space=None`` (no
# generation) and do not move when generation does. The momentum one is also the
# 2026-11-23 baseline's.
PINNED_STRATEGIES: dict[str, dict[str, Any]] = {
    "momentum": {
        "type": "momentum",
        "strategy_id": "momentum__f84afdd49e7e463aa29a211bf0425d17bdf157f2a7cde251b34d00711a34d9bc",
        "parameters": {"lookback": 10, "buy_above": 0.1, "sell_below": -0.1},
    },
    "mean_reversion": {
        "type": "mean_reversion",
        "strategy_id": "mean_reversion__c4865a764b31bd0b87b1f60f05d3b19235fca9dcddbd913e13e8436f55886e09",
        "parameters": {"window": 10, "buy_below_z": -0.5, "sell_above_z": 0.5},
    },
    "moving_average_crossover": {
        "type": "moving_average_crossover",
        "strategy_id": "moving_average_crossover__bf6d39da67fe77ecb6710f02e729c152869aa8d39e1c607f23d64f5440087594",
        "parameters": {"short_window": 10, "long_window": 200},
    },
}


def pinned_strategy_set(*families: str) -> list[dict[str, Any]]:
    """The pinned configs of these families, as a strategy_set (copies)."""
    return [
        {**PINNED_STRATEGIES[f], "parameters": dict(PINNED_STRATEGIES[f]["parameters"])}
        for f in families
    ]


def hook_experiment(session: Any, simulation_runner: Any, dataset_version: str) -> Any:
    """The experiment the research hook builds for TICK: its six stages, its thresholds."""
    import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh

    experiment = rh._build_replay_experiment_definition(
        session=session,
        timestamp=TICK,
        replay_context=PlatformReplayContext.create(
            symbols=SYMBOLS, actor="real_run_support", timestamp=TICK
        ),
        simulation_runner=simulation_runner,
        dataset_version_id_override=dataset_version,
        profile="smoke",
    )
    assert experiment is not None, "the hook should build an experiment from the seeded dataset"
    return experiment
