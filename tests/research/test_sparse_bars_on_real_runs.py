"""A real run on bars with gaps values and fills positions correctly (plans R7 and R10).

Real five-minute data is sparse: a thinly traded symbol often has no bar at a
timestamp where other symbols do. tests/research/simulation/test_missing_bar_marking.py
pins the engine on a hand-built eight-bar timeline. This is the same question
asked of the whole path (bars on disk, window loader, real SimulationRunner,
engine, recorded output) on 30 days of bars where two of three symbols each lack
a bar on one day in five.

One momentum strategy trades all three symbols and holds positions across the
gaps. Every position row it records is then checked against the bars.

Run with:
    python -m pytest tests/research/test_sparse_bars_on_real_runs.py -v
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pyarrow.dataset as ds
import pytest

from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from tests.research.real_run_support import (
    START,
    SYMBOLS,
    build_context,
    closes,
    hook_experiment,
    pinned_strategy_set,
    seed_dataset,
    short_data_root,
    trading_days,
)
from tests.utilities.universe_seeding import seed_universe_version

_SHAPE = "falls_then_rises"
_WINDOW = trading_days(date(2023, 3, 2), date(2023, 5, 31))  # the hook's 90 days
# One day in five without a bar, on different days for the two symbols. SPY has
# a bar every day and keeps the timeline going.
_NO_BAR_ON = {
    "AAPL": frozenset(day for i, day in enumerate(_WINDOW) if i % 5 == 1),
    "MSFT": frozenset(day for i, day in enumerate(_WINDOW) if i % 5 == 3),
}
_ONE_MOMENTUM_STRATEGY = pinned_strategy_set("momentum")  # fixed, not a draw


@dataclass
class _Run:
    positions: pd.DataFrame  # the positions file, one row per held symbol per bar
    equity: pd.DataFrame
    fills: pd.DataFrame


@pytest.fixture()
def run(db_session) -> Iterator[_Run]:
    with short_data_root() as data_root:
        seed_universe_version(
            db_session,
            symbols=SYMBOLS,
            effective_from=datetime.combine(START, datetime.min.time(), tzinfo=UTC),
        )
        version = seed_dataset(data_root, db_session, _SHAPE, no_bar_on=_NO_BAR_ON)
        ctx = build_context(db_session, data_root)
        experiment = hook_experiment(db_session, ctx.simulation_runner, version)
        stage_1 = experiment.staged_pipeline_config.stages[0]
        experiment = replace(
            experiment,
            strategy_set=_ONE_MOMENTUM_STRATEGY,
            parameter_space=None,
            staged_pipeline_config=StagedPipelineConfig(stages=[stage_1], smoke=True),
        )
        result = ctx.experiment_orchestration_service.run_staged_experiment(experiment)
        (simulation,) = result.stage_results[0].simulation_results
        positions = (
            ds.dataset(
                str(data_root / "simulations" / "positions"), format="parquet", partitioning="hive"
            )
            .to_table()
            .to_pandas()
        )
        positions["day"] = positions["timestamp"].dt.date
        yield _Run(
            positions=positions[positions["quantity"] != 0],
            equity=simulation.equity_curve,
            fills=simulation.trade_logs,
        )


def _latest_close(symbol: str, day: date) -> float:
    """The close of the symbol's most recent bar on or before ``day``."""
    with_a_bar = {
        d: close
        for d, close in closes(symbol, _SHAPE).items()
        if d not in _NO_BAR_ON.get(symbol, ())
    }
    return with_a_bar[max(d for d in with_a_bar if d <= day)]


def _on_a_gap(row: Any) -> bool:
    return row.day in _NO_BAR_ON.get(row.symbol, ())


def test_the_run_holds_positions_across_gaps(run: _Run) -> None:
    """Without this the tests below would pass on a run that never met a gap."""
    held_on_a_gap = [row.symbol for row in run.positions.itertuples() if _on_a_gap(row)]

    assert held_on_a_gap.count("AAPL") >= 3
    assert held_on_a_gap.count("MSFT") >= 3
    assert len(run.fills) >= 10


def test_every_position_is_marked_at_its_symbols_latest_close(run: _Run) -> None:
    for row in run.positions.itertuples():
        expected = _latest_close(row.symbol, row.day)
        assert row.market_price == pytest.approx(expected, rel=1e-9), (row.symbol, row.day)
        assert row.market_value == pytest.approx(row.quantity * expected, rel=1e-9)
        assert row.unrealized_pnl == pytest.approx(
            row.quantity * (expected - row.avg_cost), rel=1e-6, abs=1e-6
        )


def test_a_position_on_a_gap_keeps_the_mark_of_the_day_before(run: _Run) -> None:
    """The mark neither falls back to the fill price nor drops to nothing."""
    gap_rows = [row for row in run.positions.itertuples() if _on_a_gap(row)]

    for row in gap_rows:
        earlier = run.positions[
            (run.positions["symbol"] == row.symbol) & (run.positions["day"] < row.day)
        ]
        if earlier.empty:
            continue
        day_before = earlier.sort_values("day").iloc[-1]
        assert row.market_price == day_before["market_price"], (row.symbol, row.day)
        assert row.market_price != pytest.approx(row.avg_cost, rel=1e-6)


def test_equity_is_cash_plus_the_marked_positions(run: _Run) -> None:
    value_at = run.positions.groupby("timestamp")["market_value"].sum()

    for bar in run.equity.itertuples():
        held = float(value_at.get(bar.timestamp, 0.0))
        assert bar.positions_value == pytest.approx(held, rel=1e-9, abs=1e-6), bar.timestamp
        assert bar.equity == pytest.approx(bar.cash + held, rel=1e-9), bar.timestamp


def test_no_order_fills_on_a_day_its_symbol_has_no_bar(run: _Run) -> None:
    for fill in run.fills.itertuples():
        day = fill.timestamp.date()
        assert day not in _NO_BAR_ON.get(fill.symbol, ()), (fill.symbol, day)
        # A fill happens inside the bar it filled on.
        close = closes(fill.symbol, _SHAPE)[day]
        assert close * 0.99 <= fill.price <= close * 1.01, (fill.symbol, day, fill.price)
