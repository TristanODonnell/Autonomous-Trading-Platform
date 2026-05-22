"""Tests that SimulationRunner wires the FeatureDependencyResolverService correctly.

Covers:
- Runner passes resolved feature requests to SimulationWindowLoader.
- Runner records resolved_feature_dataset_ids in execution_config.
- Strategies with no persisted features behave unchanged (no feature requests).
- Manual feature loading (resolver=None) remains backward compatible.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.simulation.services.feature_dependency_resolver_service import (
    FeatureDependencyResolverService,
    ResolvedFeatureDependencies,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationFeatureDatasetRequest,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
)
from autonomous_trading_platform.storage.parquet.datasets import FEATURE_RETURNS_DATASET

_METRICS_PATCH = [
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_return_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_risk_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_trade_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_stability_metrics",
]


@pytest.fixture(autouse=True)
def _patch_metrics():
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


def _make_empty_execution_result() -> MagicMock:
    result = MagicMock()
    result.trade_logs = pd.DataFrame()
    result.equity_curve = pd.DataFrame()
    result.per_bar_metrics = pd.DataFrame()
    result.positions = pd.DataFrame()
    result.signal_log = pd.DataFrame()
    return result


def _build_runner(
    *,
    feature_dependency_resolver: FeatureDependencyResolverService | None = None,
) -> tuple[SimulationRunner, MagicMock, MagicMock]:
    """Return (runner, mock_window_loader, mock_sim_run_repo)."""
    mock_resolved = MagicMock()
    mock_resolved.metadata = {}
    mock_resolved.dataset = MagicMock()

    mock_window = MagicMock()
    mock_window.warmup_timestamps = set()
    mock_window.symbols = ["AAPL"]

    mock_window_loader = MagicMock()
    mock_window_loader.load_window.return_value = mock_window

    mock_sim_run_repo = MagicMock()
    mock_run_row = MagicMock()
    mock_run_row.execution_config = {}
    mock_run_row.metrics_snapshot_id = None
    mock_sim_run_repo.get_by_run_id.return_value = mock_run_row

    mock_metrics_repo = MagicMock()
    mock_metrics_repo.to_row.return_value = MagicMock()

    mock_execution_engine = MagicMock()
    mock_execution_engine.execute.return_value = _make_empty_execution_result()

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
        feature_dependency_resolver=feature_dependency_resolver,
    )

    return runner, mock_window_loader, mock_sim_run_repo


def _make_stub_request() -> SimulationRunRequest:
    return SimulationRunRequest(
        strategy_id="strat-1",
        strategy_config={"type": "stub", "parameters": {"price_change_threshold": 0.0}},
        dataset_version="bars-v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 3, 29),
    )


def _make_feature_request() -> SimulationFeatureDatasetRequest:
    return SimulationFeatureDatasetRequest(
        feature_name="returns",
        dataset=FEATURE_RETURNS_DATASET,
        dataset_version="feat-returns-001",
    )


# ---------------------------------------------------------------------------
# Tests: resolver=None (backward compatibility)
# ---------------------------------------------------------------------------


def test_runner_without_resolver_passes_no_feature_datasets() -> None:
    runner, mock_loader, _ = _build_runner(feature_dependency_resolver=None)

    runner.run(_make_stub_request())

    _, kwargs = mock_loader.load_window.call_args
    # feature_datasets must be None or falsy when no resolver is wired
    assert not kwargs.get("feature_datasets")


def test_runner_without_resolver_does_not_break_existing_strategies() -> None:
    runner, _, _ = _build_runner(feature_dependency_resolver=None)

    result = runner.run(_make_stub_request())

    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Tests: resolver wired, strategy has no persisted features
# ---------------------------------------------------------------------------


def test_runner_with_resolver_passes_no_feature_datasets_for_no_feature_strategy() -> None:
    mock_resolver = MagicMock(spec=FeatureDependencyResolverService)
    mock_resolver.resolve.return_value = ResolvedFeatureDependencies(
        feature_requests=[],
        resolved_feature_dataset_ids={},
        warmup_bars=1,
    )

    runner, mock_loader, _ = _build_runner(feature_dependency_resolver=mock_resolver)

    runner.run(_make_stub_request())

    _, kwargs = mock_loader.load_window.call_args
    assert not kwargs.get("feature_datasets")


def test_runner_records_empty_feature_dataset_ids_for_no_feature_strategy() -> None:
    mock_resolver = MagicMock(spec=FeatureDependencyResolverService)
    mock_resolver.resolve.return_value = ResolvedFeatureDependencies(
        feature_requests=[],
        resolved_feature_dataset_ids={},
        warmup_bars=1,
    )

    runner, _, mock_repo = _build_runner(feature_dependency_resolver=mock_resolver)

    runner.run(_make_stub_request())

    # Inspect the SimulationRun contract passed to to_row (not the mocked row).
    simulation_run_contract = mock_repo.to_row.call_args[0][0]
    assert simulation_run_contract.execution_config["resolved_feature_dataset_ids"] == {}


# ---------------------------------------------------------------------------
# Tests: resolver wired, strategy declares persisted features
# ---------------------------------------------------------------------------


def test_runner_passes_resolved_feature_requests_to_window_loader() -> None:
    feature_req = _make_feature_request()

    mock_resolver = MagicMock(spec=FeatureDependencyResolverService)
    mock_resolver.resolve.return_value = ResolvedFeatureDependencies(
        feature_requests=[feature_req],
        resolved_feature_dataset_ids={"returns": "feat-returns-001"},
        warmup_bars=10,
    )

    runner, mock_loader, _ = _build_runner(feature_dependency_resolver=mock_resolver)

    runner.run(_make_stub_request())

    _, kwargs = mock_loader.load_window.call_args
    feature_datasets = list(kwargs["feature_datasets"])
    assert len(feature_datasets) == 1
    assert feature_datasets[0].feature_name == "returns"
    assert feature_datasets[0].dataset_version == "feat-returns-001"


def test_runner_records_resolved_feature_dataset_ids_in_execution_config() -> None:
    feature_req = _make_feature_request()

    mock_resolver = MagicMock(spec=FeatureDependencyResolverService)
    mock_resolver.resolve.return_value = ResolvedFeatureDependencies(
        feature_requests=[feature_req],
        resolved_feature_dataset_ids={"returns": "feat-returns-001"},
        warmup_bars=10,
    )

    runner, _, mock_repo = _build_runner(feature_dependency_resolver=mock_resolver)

    runner.run(_make_stub_request())

    # Inspect the SimulationRun contract passed to to_row (not the mocked row).
    simulation_run_contract = mock_repo.to_row.call_args[0][0]
    assert simulation_run_contract.execution_config["resolved_feature_dataset_ids"] == {
        "returns": "feat-returns-001"
    }


def test_runner_uses_resolver_warmup_bars() -> None:
    mock_resolver = MagicMock(spec=FeatureDependencyResolverService)
    mock_resolver.resolve.return_value = ResolvedFeatureDependencies(
        feature_requests=[],
        resolved_feature_dataset_ids={},
        warmup_bars=42,
    )

    runner, mock_loader, _ = _build_runner(feature_dependency_resolver=mock_resolver)

    runner.run(_make_stub_request())

    _, kwargs = mock_loader.load_window.call_args
    assert kwargs["warmup_bars"] == 42


def test_runner_calls_resolver_with_correct_simulation_parameters() -> None:
    mock_resolver = MagicMock(spec=FeatureDependencyResolverService)
    mock_resolver.resolve.return_value = ResolvedFeatureDependencies(
        feature_requests=[], resolved_feature_dataset_ids={}, warmup_bars=0
    )

    request = SimulationRunRequest(
        strategy_id="strat-2",
        strategy_config={"type": "stub", "parameters": {"price_change_threshold": 0.0}},
        dataset_version="bars-v2",
        random_seed=7,
        price_basis=PriceBasis.ADJUSTED,
        symbols=["MSFT", "GOOG"],
        start_date=date(2023, 6, 1),
        end_date=date(2023, 9, 29),
    )

    runner, _, _ = _build_runner(feature_dependency_resolver=mock_resolver)
    runner.run(request)

    mock_resolver.resolve.assert_called_once_with(
        strategy_type="stub",
        strategy_parameters={"price_change_threshold": 0.0},
        simulation_dataset_version="bars-v2",
        price_basis=PriceBasis.ADJUSTED,
        symbols=["MSFT", "GOOG"],
        start_date=date(2023, 6, 1),
        end_date=date(2023, 9, 29),
    )


# ---------------------------------------------------------------------------
# Tests: resolver failure propagates
# ---------------------------------------------------------------------------


def test_runner_propagates_feature_dependency_error() -> None:
    from autonomous_trading_platform.research.simulation.services.feature_dependency_resolver_service import (
        FeatureDependencyError,
    )

    mock_resolver = MagicMock(spec=FeatureDependencyResolverService)
    mock_resolver.resolve.side_effect = FeatureDependencyError("No feature dataset found")

    runner, _, _ = _build_runner(feature_dependency_resolver=mock_resolver)

    with pytest.raises(FeatureDependencyError, match="No feature dataset found"):
        runner.run(_make_stub_request())
