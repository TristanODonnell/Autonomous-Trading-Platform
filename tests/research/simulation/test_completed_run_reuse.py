"""Reading a completed run back: the rules, one at a time (research plan R3).

``completed_run_reuse.load_saved_run`` decides whether a saved run answers a request.
These tests hand it rows and frames made by hand, so each rule is seen alone; the
whole thing on real simulations is in ``tests/research/test_research_resume_on_real_runs.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.simulation import completed_run_reuse
from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.research.simulation.completed_run_reuse import (
    RunNotReusable,
    RunNotSaved,
    load_saved_run,
    reuse_fingerprint,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunRequest
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)

CODE = "a" * 40
FINGERPRINT = "f" * 64
IDENTITY = SimulationArtifactIdentity(
    run_id="run-1",
    experiment_id="exp-1",
    strategy_id="momentum__abc",
    dataset_version="raw_bars_v1",
    stage_name="sim",
    window_role="full",
)


def _saved_curve(rows: int = 5) -> pd.DataFrame:
    """An equity curve as the dataset keeps it: filing columns in, run id out."""
    stamps = pd.date_range("2024-01-02 14:30", periods=rows, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "strategy_id": IDENTITY.strategy_id,
            "dataset_version": IDENTITY.dataset_version,
            "timestamp": stamps,
            "equity": [100_000.0 + 10.0 * i for i in range(rows)],
            "cash": 50_000.0,
            "positions_value": [50_000.0 + 10.0 * i for i in range(rows)],
            "drawdown": 0.0,
            "date": [stamp.date() for stamp in stamps],
        }
    )


def _saved_trades() -> pd.DataFrame:
    stamps = pd.date_range("2024-01-02 14:40", periods=2, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "strategy_id": IDENTITY.strategy_id,
            "dataset_version": IDENTITY.dataset_version,
            "symbol": ["AAPL", "MSFT"],
            "timestamp": stamps,
            "side": ["BUY", "SELL"],
            "quantity": [10.0, 4.0],
            "price": [190.0, 400.0],
            "notional": [1900.0, 1600.0],
            "fees": [0.1, 0.1],
            "date": [stamp.date() for stamp in stamps],
        }
    )


@dataclass
class _Row:
    status: str = "COMPLETED"
    metrics_snapshot_id: str | None = "snap-1"
    execution_config: dict[str, Any] = field(
        default_factory=lambda: {
            "reuse": {"fingerprint": FINGERPRINT, "code_version": CODE},
            "result_summary": {
                "trade_count": 2,
                "equity_points": 3,
                "per_bar_metric_points": 5,
                "universe_exit_count": 4,
                "symbols_loaded": ["AAPL", "MSFT"],
                "warmup_shortfall_by_symbol": {"MSFT": 7},
            },
        }
    )


@dataclass
class _Metrics:
    total_return: float | None = 0.02
    sharpe_ratio: float | None = 1.5
    max_drawdown: float | None = -0.01
    volatility: float | None = 0.2
    trade_count: int | None = 2


class _Store:
    """The three things a saved run is read from, as one object the tests fill in."""

    def __init__(
        self,
        *,
        row: _Row | None,
        metrics: _Metrics | None = None,
        curve: pd.DataFrame | None = None,
        trades: pd.DataFrame | None = None,
    ) -> None:
        self.row, self.metrics = row, metrics
        self.frames = {"equity_curve": curve, "trade_logs": trades}
        self.asked: list[str] = []

    def get_by_run_id(self, run_id: str) -> Any:
        self.asked.append(run_id)
        return self.row

    def get_by_metrics_snapshot_id(self, metrics_snapshot_id: str) -> Any:
        return self.metrics

    def read_run_frame(
        self, *, output_name: str, identity: SimulationArtifactIdentity
    ) -> pd.DataFrame | None:
        return self.frames[output_name]


def _store(**overrides: Any) -> _Store:
    given: dict[str, Any] = {
        "row": _Row(),
        "metrics": _Metrics(),
        "curve": _saved_curve(),
        "trades": _saved_trades(),
    }
    given.update(overrides)
    return _Store(**given)


def _load(store: _Store, *, fingerprint: str = FINGERPRINT, code: str = CODE) -> Any:
    return load_saved_run(
        identity=IDENTITY,
        fingerprint=fingerprint,
        code=code,
        run_rows=store,
        metric_rows=store,
        frames=store,
    )


def _row_with(**changes: Any) -> _Row:
    """A good row with part of its execution config replaced; a None value removes it."""
    row = _Row()
    for key, value in changes.items():
        if value is None:
            row.execution_config.pop(key, None)
        else:
            row.execution_config[key] = value
    return row


def test_a_completed_run_comes_back_as_it_was_returned() -> None:
    store = _store()

    saved = _load(store)

    assert store.asked == ["run-1"]
    # The run id is put back first; the columns that only say where the frame was
    # filed are gone.
    assert list(saved.equity_curve.columns) == [
        "run_id",
        "strategy_id",
        "timestamp",
        "equity",
        "cash",
        "positions_value",
        "drawdown",
    ]
    assert set(saved.equity_curve["run_id"]) == {"run-1"}
    assert list(saved.trade_logs.columns) == [
        "run_id",
        "strategy_id",
        "symbol",
        "timestamp",
        "side",
        "quantity",
        "price",
        "notional",
        "fees",
    ]
    assert list(saved.trade_logs["symbol"]) == ["AAPL", "MSFT"]
    assert saved.symbols == ["AAPL", "MSFT"]
    assert saved.warmup_shortfall_by_symbol == {"MSFT": 7}
    assert (saved.per_bar_metric_points, saved.universe_exit_count) == (5, 4)
    assert saved.stored == completed_run_reuse.StoredMetrics(
        total_return=0.02, sharpe_ratio=1.5, max_drawdown=-0.01, volatility=0.2, trade_count=2
    )


def test_the_warmup_rows_are_left_off_the_curve() -> None:
    """Five rows were saved and the metrics were computed on three: the last three in
    time, whatever order the file gives them in."""
    shuffled = _saved_curve().iloc[[3, 0, 4, 1, 2]].reset_index(drop=True)

    saved = _load(_store(curve=shuffled))

    assert list(saved.equity_curve["equity"]) == [100_020.0, 100_030.0, 100_040.0]
    assert list(saved.equity_curve.index) == [0, 1, 2]


def test_a_run_with_no_fills_comes_back_with_an_empty_trade_log() -> None:
    """A frame with no rows leaves no file behind."""
    saved = _load(_store(trades=None))

    assert len(saved.trade_logs) == 0
    assert list(saved.trade_logs.columns) == [
        "run_id",
        "strategy_id",
        "symbol",
        "timestamp",
        "side",
        "quantity",
        "price",
        "notional",
        "fees",
    ]


def test_a_run_on_a_fixed_list_has_no_universe_exit_count() -> None:
    row = _Row()
    del row.execution_config["result_summary"]["universe_exit_count"]

    assert _load(_store(row=row)).universe_exit_count is None


@pytest.mark.parametrize("status", ["RUNNING", "FAILED"])
def test_a_run_that_did_not_complete_is_not_a_saved_run(status: str) -> None:
    """Whatever else its row holds. A killed process leaves RUNNING behind."""
    with pytest.raises(RunNotSaved, match=f"the run row is {status}"):
        _load(_store(row=_Row(status=status)))


def test_no_row_is_not_a_saved_run() -> None:
    with pytest.raises(RunNotSaved, match="no run row"):
        _load(_store(row=None))


@pytest.mark.parametrize(
    ("store", "reason"),
    [
        pytest.param(
            _store(row=_row_with(reuse=None)),
            "the run was recorded without a fingerprint",
            id="recorded before reuse was built",
        ),
        pytest.param(
            _store(row=_row_with(reuse={"fingerprint": "0" * 64, "code_version": "b" * 40})),
            f"the run was made from other inputs or other code (code then {'b' * 40}, now {CODE})",
            id="another fingerprint",
        ),
        pytest.param(
            _store(row=_row_with(result_summary={"equity_points": 3})),
            "the run row does not say which names were loaded",
            id="names loaded not recorded",
        ),
        pytest.param(
            _store(metrics=None),
            "the run has no stored metrics to check the saved frames against",
            id="no stored metrics",
        ),
        pytest.param(
            _store(row=_Row(metrics_snapshot_id=None)),
            "the run has no stored metrics to check the saved frames against",
            id="row points at no metrics",
        ),
        pytest.param(
            _store(curve=None),
            "the saved equity curve has 0 rows, the run row says 3",
            id="curve gone",
        ),
        pytest.param(
            _store(curve=_saved_curve(rows=2)),
            "the saved equity curve has 2 rows, the run row says 3",
            id="curve short",
        ),
    ],
)
def test_a_completed_run_that_must_not_be_handed_back(store: _Store, reason: str) -> None:
    with pytest.raises(RunNotReusable) as refused:
        _load(store)
    assert str(refused.value) == reason


def test_nothing_is_handed_back_to_a_process_that_does_not_know_its_code() -> None:
    with pytest.raises(RunNotReusable, match="the code version of this process is not known"):
        _load(_store(), code=completed_run_reuse.UNKNOWN_CODE_VERSION)


# ── the fingerprint ──────────────────────────────────────────────────────────


def _request(**changes: Any) -> SimulationRunRequest:
    given: dict[str, Any] = {
        "strategy_id": "momentum__abc",
        "strategy_config": {
            "strategy_id": "momentum__abc",
            "type": "momentum",
            "parameters": {"lookback": 20, "buy_above": 0.02, "sell_below": -0.02},
        },
        "dataset_version": "raw_bars_v1",
        "random_seed": 7,
        "price_basis": PriceBasis.RAW,
        "symbols": ["AAPL", "MSFT"],
        "start_date": date(2024, 1, 2),
        "end_date": date(2024, 3, 28),
    }
    given.update(changes)
    return SimulationRunRequest(**given)


def _fingerprint(request: SimulationRunRequest, **changes: Any) -> str:
    given: dict[str, Any] = {
        "code": CODE,
        "cost_model": {"commission_per_share": "0.005"},
        "fill_model": {"model": "volume_share", "impact": 0.1},
    }
    given.update(changes)
    return reuse_fingerprint(request, **given)


def test_the_same_request_has_the_same_fingerprint() -> None:
    reordered = _request(
        strategy_config={
            "parameters": {"sell_below": -0.02, "lookback": 20, "buy_above": 0.02},
            "type": "momentum",
            "strategy_id": "momentum__abc",
        }
    )

    assert _fingerprint(_request()) == _fingerprint(_request())
    assert _fingerprint(reordered) == _fingerprint(_request())
    assert len(_fingerprint(_request())) == 64


@pytest.mark.parametrize(
    "other",
    [
        pytest.param(
            _request(
                strategy_config={
                    "strategy_id": "momentum__abc",
                    "type": "momentum",
                    "parameters": {"lookback": 21, "buy_above": 0.02, "sell_below": -0.02},
                }
            ),
            id="a setting changed under the same strategy id",
        ),
        pytest.param(_request(initial_cash=250_000.0), id="cash"),
        pytest.param(_request(settlement_days=1), id="settlement"),
        pytest.param(_request(strict_data_loading=False), id="strict loading"),
        pytest.param(_request(shuffle_timestamp=True), id="shuffled"),
        pytest.param(_request(corporate_actions=[]), id="corporate actions handed in"),
        pytest.param(_request(dividend_events=[]), id="dividends handed in"),
    ],
)
def test_what_the_run_id_leaves_out_changes_the_fingerprint(other: SimulationRunRequest) -> None:
    assert _fingerprint(other) != _fingerprint(_request())


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"code": "b" * 40}, id="code"),
        pytest.param({"cost_model": {"commission_per_share": "0.01"}}, id="cost model"),
        pytest.param({"fill_model": {"model": "volume_share", "impact": 0.2}}, id="fill model"),
        pytest.param({"fill_model": None}, id="no fill model"),
    ],
)
def test_the_code_and_the_models_change_the_fingerprint(changes: dict[str, Any]) -> None:
    assert _fingerprint(_request(), **changes) != _fingerprint(_request())


# ── the code version ─────────────────────────────────────────────────────────


@pytest.fixture()
def fresh_code_version():
    completed_run_reuse.code_version.cache_clear()
    yield
    completed_run_reuse.code_version.cache_clear()


def test_an_image_says_which_commit_it_is(monkeypatch, fresh_code_version) -> None:
    """An image has no checkout to ask; its build writes the commit into GIT_SHA."""
    monkeypatch.setenv("GIT_SHA", f" {CODE}\n")
    monkeypatch.setattr(completed_run_reuse, "get_git_commit", lambda: "from-the-checkout")

    assert completed_run_reuse.code_version() == CODE


def test_a_checkout_is_asked_when_the_image_says_nothing(monkeypatch, fresh_code_version) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setattr(completed_run_reuse, "get_git_commit", lambda: "from-the-checkout")

    assert completed_run_reuse.code_version() == "from-the-checkout"


def test_neither_an_image_nor_a_checkout_is_unknown(monkeypatch, fresh_code_version) -> None:
    monkeypatch.setenv("GIT_SHA", "")
    monkeypatch.setattr(completed_run_reuse, "get_git_commit", lambda: "unknown")

    assert completed_run_reuse.code_version() == completed_run_reuse.UNKNOWN_CODE_VERSION


# ── the frames on disk ───────────────────────────────────────────────────────


def _returned_curve(run_id: str, rows: int = 4) -> pd.DataFrame:
    stamps = pd.date_range("2024-01-02 14:30", periods=rows, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "run_id": run_id,
            "strategy_id": "momentum__abc",
            "timestamp": stamps,
            "equity": [100_000.0 + i for i in range(rows)],
            "cash": 100_000.0,
            "positions_value": 0.0,
            "drawdown": 0.0,
        }
    )


def test_a_runs_frame_is_read_from_its_own_directory(tmp_path: Path) -> None:
    repository = ParquetSimulationRepository(base_path=str(tmp_path))
    mine = SimulationArtifactIdentity(
        run_id="run-1",
        experiment_id="exp-1",
        strategy_id="momentum__abc",
        dataset_version="raw_bars_v1",
        # Characters a directory name cannot carry as they are.
        stage_name="walk forward: 50%",
        window_role="test/fold 1",
    )
    other = SimulationArtifactIdentity(
        run_id="run-2",
        experiment_id="exp-1",
        strategy_id="momentum__abc",
        dataset_version="raw_bars_v1",
        stage_name="walk forward: 50%",
        window_role="test/fold 1",
    )
    repository.write_equity_curve(frame=_returned_curve("run-1"), identity=mine)
    repository.write_equity_curve(frame=_returned_curve("run-2", rows=9), identity=other)

    back = repository.read_run_frame(output_name="equity_curve", identity=mine)

    assert back is not None
    assert list(back["equity"]) == [100_000.0, 100_001.0, 100_002.0, 100_003.0]
    assert list(back["timestamp"]) == list(_returned_curve("run-1")["timestamp"])
    # The partition columns are in the directory names, not in the rows.
    assert not {"run_id", "experiment_id", "stage_name", "window_role"} & set(back.columns)


def test_a_run_that_wrote_nothing_has_no_frame(tmp_path: Path) -> None:
    repository = ParquetSimulationRepository(base_path=str(tmp_path))

    assert repository.read_run_frame(output_name="equity_curve", identity=IDENTITY) is None
    # Another run's files in the dataset do not stand in for it.
    other = SimulationArtifactIdentity(
        run_id="run-2",
        experiment_id="exp-1",
        strategy_id="momentum__abc",
        dataset_version="raw_bars_v1",
        stage_name="sim",
        window_role="full",
    )
    repository.write_equity_curve(frame=_returned_curve("run-2"), identity=other)
    assert repository.read_run_frame(output_name="equity_curve", identity=IDENTITY) is None
    with pytest.raises(ValueError, match="Unsupported simulation output_name=nothing"):
        repository.read_run_frame(output_name="nothing", identity=IDENTITY)
