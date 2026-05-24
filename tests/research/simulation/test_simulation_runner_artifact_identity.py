"""
Tests that SimulationRunner constructs SimulationArtifactIdentity correctly
and passes it to ResultRecorderService.record_results.

All heavy dependencies (execution engine, dataset resolver, etc.) are mocked.
Metrics computation functions are patched to avoid needing real equity data.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
)

# ---------------------------------------------------------------------------
# Minimal DataFrames returned by the fake execution engine
# ---------------------------------------------------------------------------


def _make_equity_df(run_id: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": [run_id],
            "experiment_id": ["exp-1"],
            "strategy_id": ["strat-1"],
            "timestamp": [pd.Timestamp("2023-03-15 10:00:00", tz="UTC")],
            "equity": [100_000.0],
            "cash": [100_000.0],
            "positions_value": [0.0],
            "drawdown": [0.0],
        }
    )


def _make_trade_df(run_id: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": [run_id],
            "symbol": ["AAPL"],
            "timestamp": [pd.Timestamp("2023-03-15 10:00:00", tz="UTC")],
            "side": ["BUY"],
            "quantity": [10.0],
            "price": [150.0],
            "notional": [1500.0],
            "fees": [1.5],
        }
    )


def _make_empty_df() -> pd.DataFrame:
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Helpers — build a fully-mocked SimulationRunner
# ---------------------------------------------------------------------------


def _make_mock_execution_result(run_id: str) -> MagicMock:
    result = MagicMock()
    result.trade_logs = _make_trade_df(run_id)
    result.equity_curve = _make_equity_df(run_id)
    result.per_bar_metrics = _make_empty_df()
    result.positions = _make_empty_df()
    result.signal_log = _make_empty_df()
    return result


def _make_fake_metrics():
    from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
        ReturnMetrics,
    )
    from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
        RiskMetrics,
    )
    from autonomous_trading_platform.research.experiments.filtering.metrics.stability_metrics import (
        StabilityMetrics,
    )
    from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
        TradeMetrics,
    )

    rm = MagicMock(spec=ReturnMetrics)
    rm.total_return = 0.05
    rk = MagicMock(spec=RiskMetrics)
    rk.sharpe_ratio = 1.2
    rk.max_drawdown = -0.05
    rk.volatility = 0.1
    tm = MagicMock(spec=TradeMetrics)
    tm.total_trades = 10
    tm.win_rate = 0.6
    sm = MagicMock(spec=StabilityMetrics)
    sm.consistency_score = 0.7
    return rm, rk, tm, sm


class CapturingRecorder:
    """Drop-in for ResultRecorderService that captures the identity passed in."""

    def __init__(self) -> None:
        self.recorded: list[SimulationArtifactIdentity] = []

    def record_results(self, *, identity: SimulationArtifactIdentity, **_kwargs) -> None:
        self.recorded.append(identity)


def _build_runner(capturing_recorder: CapturingRecorder) -> SimulationRunner:
    mock_resolved = MagicMock()
    mock_resolved.metadata = {"dataset_version": "v1"}
    mock_resolved.dataset = MagicMock()

    mock_window = MagicMock()
    mock_window.warmup_timestamps = []
    mock_window.symbols = ["AAPL"]

    mock_sim_run_repo = MagicMock()
    mock_sim_run_repo.get_by_run_id.return_value = MagicMock(
        execution_config={}, metrics_snapshot_id=None
    )

    mock_metrics_repo = MagicMock()
    mock_metrics_repo.to_row.return_value = MagicMock()

    return SimulationRunner(
        dataset_resolver=_make_dataset_resolver(mock_resolved),
        window_loader=_make_window_loader(mock_window),
        result_recorder=capturing_recorder,  # type: ignore[arg-type]
        execution_engine=_make_execution_engine(),
        context_builder=MagicMock(),
        simulated_execution_service=MagicMock(),
        simulation_run_repository=mock_sim_run_repo,
        strategy_config_repository=MagicMock(),
        experiment_repository=None,
        metrics_summary_repository=mock_metrics_repo,
        manifest_service=None,
        strategy_factory=_make_strategy_factory(),
    )


def _make_dataset_resolver(resolved: MagicMock) -> MagicMock:
    dr = MagicMock()
    dr.resolve_bars_dataset.return_value = resolved
    return dr


def _make_window_loader(window: MagicMock) -> MagicMock:
    wl = MagicMock()
    wl.load_window.return_value = window
    return wl


def _make_execution_engine() -> MagicMock:
    engine = MagicMock()
    # return value set per test run_id
    engine.execute.side_effect = lambda **kwargs: _make_mock_execution_result(
        str(kwargs.get("run_id", "run-mock"))
    )
    return engine


def _make_strategy_factory() -> MagicMock:
    sf = MagicMock()
    sf.build.return_value = MagicMock()
    return sf


def _make_request(
    *,
    strategy_id: str = "strat-1",
    dataset_version: str = "v1",
    experiment_id: str = "exp-1",
    stage_name: str | None = "cheap",
    window_role: str | None = "train",
    seed: int = 42,
) -> SimulationRunRequest:
    return SimulationRunRequest(
        strategy_id=strategy_id,
        strategy_config={"type": "moving_average_crossover", "parameters": {}},
        dataset_version=dataset_version,
        random_seed=seed,
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 6, 30),
        experiment_id=experiment_id,
        stage_name=stage_name,
        window_role=window_role,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_METRICS_PATCH = [
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_return_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_risk_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_trade_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_stability_metrics",
]


@pytest.fixture()
def patched_metrics():
    rm, rk, tm, sm = _make_fake_metrics()
    with (
        patch(_METRICS_PATCH[0], return_value=rm),
        patch(_METRICS_PATCH[1], return_value=rk),
        patch(_METRICS_PATCH[2], return_value=tm),
        patch(_METRICS_PATCH[3], return_value=sm),
    ):
        yield rm, rk, tm, sm


def test_runner_passes_identity_with_correct_stage_and_window(
    patched_metrics,
) -> None:
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    request = _make_request(stage_name="cheap", window_role="train")
    runner.run(request)

    assert len(recorder.recorded) == 1
    identity = recorder.recorded[0]
    assert identity.stage_name == "cheap"
    assert identity.window_role == "train"


def test_runner_passes_identity_with_correct_experiment_and_strategy(
    patched_metrics,
) -> None:
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    request = _make_request(experiment_id="exp-abc", strategy_id="strat-xyz")
    runner.run(request)

    identity = recorder.recorded[0]
    assert identity.experiment_id == "exp-abc"
    assert identity.strategy_id == "strat-xyz"


def test_runner_passes_identity_with_dataset_version(patched_metrics) -> None:
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    request = _make_request(dataset_version="v3")
    runner.run(request)

    identity = recorder.recorded[0]
    assert identity.dataset_version == "v3"


def test_runner_identity_contains_seed(patched_metrics) -> None:
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    request = _make_request(seed=99)
    runner.run(request)

    identity = recorder.recorded[0]
    assert identity.seed == 99


def test_runner_defaults_stage_name_to_adhoc_when_none(patched_metrics) -> None:
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    request = _make_request(stage_name=None)
    runner.run(request)

    identity = recorder.recorded[0]
    assert identity.stage_name == "adhoc"


def test_runner_defaults_window_role_to_default_when_none(patched_metrics) -> None:
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    request = _make_request(window_role=None)
    runner.run(request)

    identity = recorder.recorded[0]
    assert identity.window_role == "default"


def test_two_runs_same_config_produce_same_run_id(patched_metrics) -> None:
    """Deterministic run_id: identical inputs must produce the same run_id for idempotent replay."""
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    request = _make_request()
    runner.run(request)
    runner.run(request)

    assert len(recorder.recorded) == 2
    run_ids = {i.run_id for i in recorder.recorded}
    assert len(run_ids) == 1, "Identical inputs must produce the same deterministic run_id"


def test_two_runs_different_seeds_get_different_run_ids(patched_metrics) -> None:
    """Different seeds must produce different run_ids."""
    recorder = CapturingRecorder()
    runner = _build_runner(recorder)

    runner.run(_make_request(seed=1))
    runner.run(_make_request(seed=2))

    run_ids = {i.run_id for i in recorder.recorded}
    assert len(run_ids) == 2, "Different seeds must produce different run_ids"
