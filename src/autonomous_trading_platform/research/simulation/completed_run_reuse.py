"""Reading a completed run back instead of simulating it again (research plan R3).

A research job that dies partway has already paid for most of its simulations. Each
one left two things behind: a ``simulation_runs`` row, and its frames as Parquet under
its own run id. The run id is derived from the run's inputs, so the same request asks
for the same id. This module reads such a run back.

The saved run is the checkpoint. Nothing else is kept: no second store beside the
database, and no pass / fail verdict. A stage that gets a run back judges it again
with the same pure rule it would apply to a new one, so a resumed pipeline cannot
keep a verdict the current rules would not give.

A run is handed back only when all of this holds; otherwise it is simulated again,
which costs time and never changes a result:

- its row is COMPLETED;
- the row carries the fingerprint of this request (below) made by this code version;
- its saved equity curve and trade log are there and are as long as the row says;
- the metrics recomputed from the saved frames equal the stored ones (checked by the
  runner, which owns the metric functions).

What the run id does not cover is in the fingerprint: the code version, the strategy's
settings, cash, cost and fill models, settlement, and corporate actions handed in with
the request. Not covered by either: corporate actions the runner reads from its own
source. A resume therefore belongs on the database the first attempt wrote to, with
nothing ingested in between.

The frames that come back hold the columns the simulation datasets keep. The equity
curve loses its cash detail (settled, unsettled and reserved cash, buying power,
pending settlements, dividend cash) and the trade log its ``slippage`` column. No
stage or metric reads those.

Reuse is off unless switched on (``SimulationRunner.reuse_completed_runs``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd

from autonomous_trading_platform.common.system_info import get_git_commit
from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)

if TYPE_CHECKING:
    from autonomous_trading_platform.research.simulation.simulation_runner import (
        SimulationRunRequest,
    )

UNKNOWN_CODE_VERSION = "unknown"
COMPLETED = "COMPLETED"

# Keys of simulation_runs.execution_config.
REUSE_KEY = "reuse"
RESULT_SUMMARY_KEY = "result_summary"

# Columns of a saved frame that say where it was filed, not what the run produced.
_FILING_COLUMNS = ("dataset_version", "date", "experiment_id", "stage_name", "window_role")
# The trade log of a run with no fills: what the simulation datasets keep of it.
_TRADE_LOG_COLUMNS = (
    "run_id",
    "strategy_id",
    "symbol",
    "timestamp",
    "side",
    "quantity",
    "price",
    "notional",
    "fees",
)


class RunNotSaved(Exception):
    """There is no completed run to read back: the ordinary case for a run not yet
    done. Not worth a warning."""


class RunNotReusable(Exception):
    """A completed run is there and must not be handed back: worth a warning."""


@lru_cache(maxsize=1)
def code_version() -> str:
    """The commit this process runs: ``GIT_SHA`` where the image sets it (an image
    has no git checkout to ask), the checkout's HEAD otherwise, else "unknown".

    On a checkout this is HEAD, which says nothing about uncommitted changes.
    """
    return os.environ.get("GIT_SHA", "").strip() or get_git_commit() or UNKNOWN_CODE_VERSION


def reuse_fingerprint(
    request: SimulationRunRequest,
    *,
    code: str,
    cost_model: Mapping[str, Any] | None,
    fill_model: Mapping[str, Any] | None,
) -> str:
    """Everything that decides a run's result and is not in its run id.

    The run id holds the strategy id, seed, dataset, price basis, symbols, dates,
    experiment, stage, window role, cost multiplier and universe timeline.
    """
    payload = {
        "code_version": code,
        "strategy_config": request.strategy_config,
        "initial_cash": request.initial_cash,
        "strict_data_loading": request.strict_data_loading,
        "shuffle_timestamp": request.shuffle_timestamp,
        "resample_to_daily": request.resample_to_daily,
        "cost_multiplier": request.cost_multiplier,
        "settlement_days": request.settlement_days,
        "execution_policy_config": _plain(request.execution_policy_config),
        "corporate_actions": _plain(request.corporate_actions),
        "dividend_events": _plain(request.dividend_events),
        "cost_model": cost_model,
        "fill_model": fill_model,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    """A value as JSON can hold it, the same way every time."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    return str(value)


@dataclass(frozen=True)
class StoredMetrics:
    """The metrics the first attempt stored: what the saved frames are checked against."""

    total_return: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    volatility: float | None
    trade_count: int | None


@dataclass(frozen=True)
class SavedRun:
    """A completed run as read back, before its metrics are recomputed."""

    # Warmup rows removed: the rows the first attempt computed its metrics on.
    equity_curve: pd.DataFrame
    trade_logs: pd.DataFrame
    # The names the run loaded bars for.
    symbols: list[str]
    warmup_shortfall_by_symbol: dict[str, int]
    per_bar_metric_points: int
    universe_exit_count: int | None
    stored: StoredMetrics


class _RunRow(Protocol):
    status: str
    execution_config: dict[str, Any]
    metrics_snapshot_id: str | None


class _RunRows(Protocol):
    def get_by_run_id(self, run_id: str) -> Any: ...


class _MetricRows(Protocol):
    def get_by_metrics_snapshot_id(self, metrics_snapshot_id: str) -> Any: ...


class _RunFrames(Protocol):
    def read_run_frame(
        self, *, output_name: str, identity: SimulationArtifactIdentity
    ) -> pd.DataFrame | None: ...


def load_saved_run(
    *,
    identity: SimulationArtifactIdentity,
    fingerprint: str,
    code: str,
    run_rows: _RunRows,
    metric_rows: _MetricRows,
    frames: _RunFrames,
) -> SavedRun:
    """The completed run with this identity, read back from its row and its frames.

    Raises RunNotSaved when there is none, RunNotReusable when there is one that
    must not stand in for the request.
    """
    row: _RunRow | None = run_rows.get_by_run_id(identity.run_id)
    if row is None:
        raise RunNotSaved("no run row")
    if row.status != COMPLETED:
        raise RunNotSaved(f"the run row is {row.status}")

    if code == UNKNOWN_CODE_VERSION:
        raise RunNotReusable("the code version of this process is not known")
    config = row.execution_config or {}
    recorded = config.get(REUSE_KEY)
    if not isinstance(recorded, Mapping) or not recorded.get("fingerprint"):
        raise RunNotReusable("the run was recorded without a fingerprint")
    if recorded.get("fingerprint") != fingerprint:
        raise RunNotReusable(
            "the run was made from other inputs or other code "
            f"(code then {recorded.get('code_version')}, now {code})"
        )

    summary = config.get(RESULT_SUMMARY_KEY)
    if not isinstance(summary, Mapping) or "symbols_loaded" not in summary:
        raise RunNotReusable("the run row does not say which names were loaded")
    equity_points = int(summary.get("equity_points", -1))

    metrics = (
        metric_rows.get_by_metrics_snapshot_id(row.metrics_snapshot_id)
        if row.metrics_snapshot_id
        else None
    )
    if metrics is None:
        raise RunNotReusable("the run has no stored metrics to check the saved frames against")

    saved_curve = frames.read_run_frame(output_name="equity_curve", identity=identity)
    rows_saved = 0 if saved_curve is None else len(saved_curve)
    if saved_curve is None or equity_points < 0 or rows_saved < equity_points:
        raise RunNotReusable(
            f"the saved equity curve has {rows_saved} rows, the run row says {equity_points}"
        )
    # Warmup rows come first in time and were left out of the metrics: the rows the
    # metrics were computed on are the last ``equity_points``.
    curve = _as_returned(saved_curve, identity).sort_values("timestamp", kind="stable")
    curve = curve.tail(equity_points).reset_index(drop=True)

    saved_trades = frames.read_run_frame(output_name="trade_logs", identity=identity)
    if saved_trades is None:
        trades = pd.DataFrame(columns=list(_TRADE_LOG_COLUMNS))
    else:
        trades = _as_returned(saved_trades, identity).reset_index(drop=True)

    exits = summary.get("universe_exit_count")
    return SavedRun(
        equity_curve=curve,
        trade_logs=trades,
        symbols=[str(name) for name in summary["symbols_loaded"]],
        warmup_shortfall_by_symbol={
            str(name): int(bars)
            for name, bars in (summary.get("warmup_shortfall_by_symbol") or {}).items()
        },
        per_bar_metric_points=int(summary.get("per_bar_metric_points", 0)),
        universe_exit_count=int(exits) if exits is not None else None,
        stored=StoredMetrics(
            total_return=metrics.total_return,
            sharpe_ratio=metrics.sharpe_ratio,
            max_drawdown=metrics.max_drawdown,
            volatility=metrics.volatility,
            trade_count=metrics.trade_count,
        ),
    )


def _as_returned(saved: pd.DataFrame, identity: SimulationArtifactIdentity) -> pd.DataFrame:
    """A saved frame with the columns a run returns: the filing columns dropped and
    the run id, which is in the directory name, put back first."""
    frame = saved.drop(columns=[c for c in _FILING_COLUMNS if c in saved.columns])
    if "run_id" in frame.columns:
        frame = frame.drop(columns=["run_id"])
    frame.insert(0, "run_id", identity.run_id)
    return frame
