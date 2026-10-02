"""Tests that SimulationRunner uses registry-derived warmup bars.

Verifies that the former parameter-name heuristic (long_window * 78) has been
replaced by StrategyDefinition.compute_warmup_bars().
"""

from __future__ import annotations

from datetime import date
from typing import cast
from unittest.mock import MagicMock

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.execution.services.sleeve_sizing import VOL_LOOKBACK_BARS
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
)

_METRICS_PATCH = [
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_return_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_risk_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_trade_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_stability_metrics",
]


@pytest.fixture(autouse=True)
def _patch_metrics():
    from unittest.mock import patch

    fakes = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    fakes[0].total_return = 0.0
    fakes[1].sharpe_ratio = 0.0
    fakes[1].max_drawdown = 0.0
    fakes[1].volatility = 0.0
    fakes[2].total_trades = 0
    fakes[2].win_rate = 0.0
    fakes[3].consistency_score = 0.0
    with (
        patch(_METRICS_PATCH[0], return_value=fakes[0]),
        patch(_METRICS_PATCH[1], return_value=fakes[1]),
        patch(_METRICS_PATCH[2], return_value=fakes[2]),
        patch(_METRICS_PATCH[3], return_value=fakes[3]),
    ):
        yield


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_execution_result() -> MagicMock:
    result = MagicMock()
    result.trade_logs = pd.DataFrame()
    result.equity_curve = pd.DataFrame()
    result.per_bar_metrics = pd.DataFrame()
    result.positions = pd.DataFrame()
    result.signal_log = pd.DataFrame()
    return result


def _build_runner() -> tuple[SimulationRunner, MagicMock]:
    """Return (runner, mock_window_loader) with everything else stubbed."""
    mock_resolved = MagicMock()
    mock_resolved.metadata = {}
    mock_resolved.dataset = MagicMock()

    mock_window = MagicMock()
    mock_window.warmup_timestamps = set()
    mock_window.symbols = ["AAPL"]

    mock_window_loader = MagicMock()
    mock_window_loader.load_window.return_value = mock_window

    mock_sim_run_repo = MagicMock()
    mock_sim_run_repo.get_by_run_id.return_value = MagicMock(
        execution_config={}, metrics_snapshot_id=None
    )

    mock_metrics_repo = MagicMock()
    mock_metrics_repo.to_row.return_value = MagicMock()

    mock_execution_engine = MagicMock()
    mock_execution_engine.execute.return_value = _make_mock_execution_result()

    runner = SimulationRunner(
        dataset_resolver=MagicMock(**{"resolve_bars_dataset.return_value": mock_resolved}),
        window_loader=mock_window_loader,
        result_recorder=MagicMock(),
        execution_engine=mock_execution_engine,
        context_builder=MagicMock(),
        simulated_execution_service=MagicMock(),
        simulation_run_repository=mock_sim_run_repo,
        strategy_config_repository=MagicMock(),
        experiment_repository=None,
        metrics_summary_repository=mock_metrics_repo,
        manifest_service=None,
        strategy_factory=MagicMock(**{"build.return_value": MagicMock()}),
        feature_dependency_resolver=None,
    )

    return runner, mock_window_loader


def _make_request(strategy_type: str, parameters: dict) -> SimulationRunRequest:
    return SimulationRunRequest(
        strategy_id="test-strategy",
        strategy_config={"type": strategy_type, "parameters": parameters},
        dataset_version="bars-v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 3, 29),
    )


# ---------------------------------------------------------------------------
# Tests: registry warmup vs old heuristic
# ---------------------------------------------------------------------------


def test_warmup_for_stub_strategy_is_registry_derived() -> None:
    """stub warmup_bars_fn returns 1, not long_window * 78."""
    runner, mock_loader = _build_runner()

    runner.run(_make_request("stub", {"price_change_threshold": 0.0}))

    # Registry says stub needs 1 warmup bar; old heuristic would give 0. The window
    # also holds the sizer's volatility closes (step 5c-E).
    cast(MagicMock, runner.context_builder).with_lookback.assert_called_once_with(1)
    _, kwargs = mock_loader.load_window.call_args
    assert kwargs["warmup_bars"] == VOL_LOOKBACK_BARS


def test_warmup_for_random_strategy_is_one_bar() -> None:
    """random warmup_bars_fn returns 0; every strategy is handed at least one bar."""
    runner, mock_loader = _build_runner()

    runner.run(
        _make_request(
            "random",
            {"signal_probability": 0.33, "buy_probability": 0.5, "random_seed": None},
        )
    )

    cast(MagicMock, runner.context_builder).with_lookback.assert_called_once_with(1)
    _, kwargs = mock_loader.load_window.call_args
    assert kwargs["warmup_bars"] == VOL_LOOKBACK_BARS


def test_warmup_for_moving_average_crossover_is_long_window_plus_one() -> None:
    """moving_average_crossover warmup = long_window + 1 bars (not * 78)."""
    runner, mock_loader = _build_runner()

    runner.run(
        _make_request(
            "moving_average_crossover",
            {"short_window": 10, "long_window": 50},
        )
    )

    _, kwargs = mock_loader.load_window.call_args
    # Registry: long_window + 1 = 51.  Old heuristic: 50 * 78 = 3900.
    assert kwargs["warmup_bars"] == 51


def test_warmup_for_momentum_is_lookback_plus_one() -> None:
    """momentum warmup = lookback + 1."""
    runner, mock_loader = _build_runner()

    runner.run(
        _make_request(
            "momentum",
            {"lookback": 20, "buy_above": 0.0, "sell_below": 0.0},
        )
    )

    _, kwargs = mock_loader.load_window.call_args
    # Registry: lookback + 1 = 21.
    assert kwargs["warmup_bars"] == 21


def test_warmup_for_mean_reversion_is_window() -> None:
    """mean_reversion warmup = window bars."""
    runner, mock_loader = _build_runner()

    runner.run(
        _make_request(
            "mean_reversion",
            {"window": 30, "buy_below_z": -2.0, "sell_above_z": 2.0},
        )
    )

    _, kwargs = mock_loader.load_window.call_args
    # Registry: window = 30.
    assert kwargs["warmup_bars"] == 30


def test_warmup_uses_default_parameters_when_none_supplied() -> None:
    """compute_warmup_bars fills defaults; registry default for momentum lookback is 5."""
    runner, mock_loader = _build_runner()

    runner.run(
        _make_request("momentum", {}),  # empty parameters → defaults applied
    )

    # momentum default lookback=5, so warmup = 6
    cast(MagicMock, runner.context_builder).with_lookback.assert_called_once_with(6)
    _, kwargs = mock_loader.load_window.call_args
    assert kwargs["warmup_bars"] == VOL_LOOKBACK_BARS


# ---------------------------------------------------------------------------
# Tests: warmup value passed through, not heuristic
# ---------------------------------------------------------------------------


def test_warmup_bars_is_not_multiplied_by_78() -> None:
    """Ensure the old _long_window * 78 pattern is gone."""
    runner, mock_loader = _build_runner()

    runner.run(
        _make_request(
            "moving_average_crossover",
            {"short_window": 10, "long_window": 30},
        )
    )

    _, kwargs = mock_loader.load_window.call_args
    # Old heuristic: 30 * 78 = 2340. Registry: 31.
    assert kwargs["warmup_bars"] != 30 * 78
    assert kwargs["warmup_bars"] == 31


@pytest.mark.parametrize(
    ("strategy_type", "parameters", "expected"),
    [
        ("moving_average_crossover", {"short_window": 10, "long_window": 30}, 31),
        (
            "factor_based",
            {
                "momentum_lookback": 1,
                "mean_reversion_window": 100,
                "volatility_window": 100,
                "volume_window": 100,
            },
            100,
        ),
        ("momentum", {"lookback": 5}, 6),
    ],
)
def test_strategy_is_handed_its_registry_warmup_in_bars(
    strategy_type: str, parameters: dict, expected: int
) -> None:
    """Rotation step 5c (F1): research used a fixed 20-bar context for every strategy,
    so a 10/30 crossover or 100-bar factor strategy never had enough bars to trade.
    The engine now gets a context builder gated on the same count the trading cycle
    uses."""
    runner, mock_loader = _build_runner()

    runner.run(_make_request(strategy_type, parameters))

    cast(MagicMock, runner.context_builder).with_lookback.assert_called_once_with(expected)
    _, engine_kwargs = cast(MagicMock, runner.execution_engine).execute.call_args
    assert (
        engine_kwargs["context_builder"]
        is cast(MagicMock, runner.context_builder).with_lookback.return_value
    )
    # The window also holds the sizer's volatility closes (step 5c-E).
    _, loader_kwargs = mock_loader.load_window.call_args
    assert loader_kwargs["warmup_bars"] == max(expected, VOL_LOOKBACK_BARS)
