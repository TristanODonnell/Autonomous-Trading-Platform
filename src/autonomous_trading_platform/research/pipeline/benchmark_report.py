"""Buy and hold of the candidate's own names, and every candidate set against it.

Research plan G9 (Step 1b row 5), report-only: nobody is eliminated by these numbers
and nothing here can fail the research step. Stage 1's bar is absolute (Sharpe and
return above a floor), so in a rising market a strategy that is long most of the time
passes on market exposure alone; this says, per window, how much of a candidate's
return the names would have given by being held.

The benchmark (decided by the user 2026-11-24):

- **Names.** Equal weight across the names the universe timeline has in force that
  day: the set the candidate may trade, so an excess return measures the strategy's
  choices, not a difference of universe. Rebalanced daily.
- **Prices.** Raw closes, with the run's own corporate actions: a split multiplies the
  shares held, a cash dividend is paid on its ex-date to the shares held then. The
  simulator credits both to the candidate's equity, so the two are alike.
- **Start.** Bought at each name's first bar of the window, as a candidate's equity
  starts at its first bar. A name with no bar on a day is skipped that day and its
  move is counted on its next day with a bar, from its last close in the window.
- **Days.** The sessions of the layout (the NYSE calendar less the days the dataset
  left out), by the UTC date of their bars, as the simulator dates them. A bar on any
  other day is not read: its move lands on the next session, measured from the last
  session's close, for buy and hold and candidate alike.

Per candidate and window, from its run's equity curve (last equity of each day; the
first day from the curve's first point): ``return``, ``benchmark_return``,
``excess_return`` (the difference of the two totals) and ``information_ratio``:
mean(daily return - benchmark's) / std(of that difference) * sqrt(252). Walk-forward
keeps no curve of its test runs, only their totals, so a fold gets the excess return
only.

Pure functions, except :func:`read_daily_closes`, which reads bars through a reader
it is handed.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from autonomous_trading_platform.accounting.corporate_actions import (
    dividend_per_share_of,
    is_applicable,
    is_split,
    split_ratio_of,
)
from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.observability.logging import get_logger

if TYPE_CHECKING:
    import pandas as pd

    from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
    from autonomous_trading_platform.research.simulation.simulation_runner import (
        SimulationRunResult,
    )
    from autonomous_trading_platform.storage.parquet.datasets import ParquetDataset

    from .admission_window import AdmissionWindowLayout, SessionSpan
    from .stages.base_stage import StageResult

logger = get_logger(__name__)

TRADING_DAYS_PER_YEAR = 252

# One name's bars as the benchmark reads them: (timestamp, close), in time order.
Closes = Sequence[tuple[datetime, float]]


@dataclass(frozen=True)
class BenchmarkSeries:
    """Buy and hold over one window: each day's return and how many names it held."""

    first: date
    last: date
    returns: dict[date, float]
    names_held: dict[date, int]

    def block(self) -> dict[str, Any]:
        """The window as the report shows it."""
        daily = list(self.returns.values())
        curve, peak, drawdown = 1.0, 1.0, 0.0
        for r in daily:
            curve *= 1.0 + r
            peak = max(peak, curve)
            drawdown = min(drawdown, curve / peak - 1.0)
        held = list(self.names_held.values())
        return {
            "first": self.first.isoformat(),
            "last": self.last.isoformat(),
            "sessions": len(daily),
            "total_return": curve - 1.0 if daily else None,
            "sharpe": _annualised_ratio(daily),
            "max_drawdown": drawdown if daily else None,
            "mean_names_held": sum(held) / len(held) if held else None,
            "fewest_names_held": min(held) if held else None,
        }


def benchmark_series(
    *,
    closes_by_symbol: Mapping[str, Closes],
    actions: Sequence[CorporateAction],
    members_at: Callable[[datetime], frozenset[str]],
    first: date,
    last: date,
    sessions: Collection[date] | None = None,
) -> BenchmarkSeries:
    """Equal-weight buy and hold of the names in force, day by day, from ``first`` to
    ``last``, on ``sessions`` only when given (see the module docstring for the rules)."""
    days: dict[str, dict[date, tuple[datetime, float, float]]] = {}
    for symbol, closes in closes_by_symbol.items():
        by_day: dict[date, tuple[datetime, float, float]] = {}
        for timestamp, close in closes:
            day = timestamp.date()
            if not first <= day <= last or (sessions is not None and day not in sessions):
                continue
            if day in by_day:
                opened, first_close, _ = by_day[day]
                by_day[day] = (opened, first_close, close)
            else:
                by_day[day] = (timestamp, close, close)
        days[symbol] = by_day

    events: dict[str, list[CorporateAction]] = {}
    for action in sorted(actions, key=lambda a: a.effective_date):
        if is_applicable(action) and (is_split(action) or _is_dividend(action)):
            events.setdefault(action.symbol, []).append(action)

    sessions = sorted({day for by_day in days.values() for day in by_day})
    returns: dict[date, float] = {}
    names_held: dict[date, int] = {}
    last_close: dict[str, tuple[date, float]] = {}
    for day in sessions:
        opening = min(by_day[day][0] for by_day in days.values() if day in by_day)
        members = members_at(opening)
        moves: list[float] = []
        for symbol, by_day in days.items():
            today = by_day.get(day)
            if today is None:
                continue
            _, first_close, close = today
            previous = last_close.get(symbol)
            last_close[symbol] = (day, close)
            if symbol not in members:
                continue
            if previous is None:
                # Bought at the name's first bar in the window: today's actions are
                # already in its price (a split) or were paid before it (ex-date).
                moves.append(close / first_close - 1.0)
                continue
            since, base = previous
            shares, cash = 1.0, 0.0
            for action in events.get(symbol, []):
                if since < action.effective_date <= day:
                    if is_split(action):
                        shares *= float(split_ratio_of(action))
                    else:
                        cash += shares * float(dividend_per_share_of(action))
            moves.append((shares * close + cash) / base - 1.0)
        returns[day] = sum(moves) / len(moves) if moves else 0.0
        names_held[day] = len(moves)
    return BenchmarkSeries(first=first, last=last, returns=returns, names_held=names_held)


def candidate_returns(
    equity_curve: pd.DataFrame,
    first: date,
    last: date,
    sessions: Collection[date] | None = None,
) -> dict[date, float]:
    """Each day's return of a run's equity, from ``first`` to ``last``: the last equity
    of the day over the last of the day before (the first day: over the curve's first
    point, the equity the run started with). With ``sessions``, no other day is read,
    so its move lands on the next session."""
    if equity_curve is None or equity_curve.empty:
        return {}
    rows = sorted(
        (_utc(timestamp), float(equity))
        for timestamp, equity in zip(equity_curve["timestamp"], equity_curve["equity"], strict=True)
        if first <= _utc(timestamp).date() <= last
    )
    if not rows:
        return {}
    closing: dict[date, float] = {}
    for timestamp, equity in rows:
        if sessions is None or timestamp.date() in sessions:
            closing[timestamp.date()] = equity
    out: dict[date, float] = {}
    previous = rows[0][1]
    for day, equity in closing.items():
        out[day] = equity / previous - 1.0 if previous else 0.0
        previous = equity
    return out


def versus_benchmark(candidate: Mapping[date, float], benchmark: BenchmarkSeries) -> dict[str, Any]:
    """One candidate's run against buy and hold, over the days both have."""
    shared = [day for day in candidate if day in benchmark.returns]
    ours = [candidate[day] for day in shared]
    theirs = [benchmark.returns[day] for day in shared]
    own_total = _compound(ours)
    benchmark_total = _compound(theirs)
    return {
        "days": len(shared),
        "return": own_total,
        "benchmark_return": benchmark_total,
        "excess_return": (
            own_total - benchmark_total
            if own_total is not None and benchmark_total is not None
            else None
        ),
        "information_ratio": _annualised_ratio([a - b for a, b in zip(ours, theirs, strict=True)]),
    }


def run_versus_benchmark(result: SimulationRunResult, benchmark: BenchmarkSeries) -> dict[str, Any]:
    """A run against buy and hold, on the days buy and hold has (its sessions)."""
    return versus_benchmark(
        candidate_returns(
            result.equity_curve, benchmark.first, benchmark.last, sessions=benchmark.returns
        ),
        benchmark,
    )


@dataclass(frozen=True)
class AdmissionBenchmark:
    """Buy and hold over each piece of the admission layout the gates read."""

    selection: BenchmarkSeries
    evaluation: BenchmarkSeries
    test_folds: tuple[BenchmarkSeries, ...]

    def block(self) -> dict[str, Any]:
        return {
            "names": "in force on the universe timeline, equal weight, rebalanced daily",
            "selection": self.selection.block(),
            "evaluation": self.evaluation.block(),
            "test_folds": [fold.block() for fold in self.test_folds],
        }


def admission_benchmark(
    *,
    layout: AdmissionWindowLayout,
    closes_by_symbol: Mapping[str, Closes],
    actions: Sequence[CorporateAction],
    members_at: Callable[[datetime], frozenset[str]],
    sessions: Collection[date] | None = None,
) -> AdmissionBenchmark:
    def over(span: SessionSpan) -> BenchmarkSeries:
        return benchmark_series(
            closes_by_symbol=closes_by_symbol,
            actions=actions,
            members_at=members_at,
            first=span.first,
            last=span.last,
            sessions=sessions,
        )

    return AdmissionBenchmark(
        selection=over(layout.selection),
        evaluation=over(layout.evaluation),
        test_folds=tuple(over(fold) for fold in layout.test_folds),
    )


def holdout_benchmark(
    *,
    span: SessionSpan,
    closes_by_symbol: Mapping[str, Closes],
    actions: Sequence[CorporateAction],
    members_at: Callable[[datetime], frozenset[str]],
    sessions: Collection[date] | None = None,
) -> BenchmarkSeries:
    return benchmark_series(
        closes_by_symbol=closes_by_symbol,
        actions=actions,
        members_at=members_at,
        first=span.first,
        last=span.last,
        sessions=sessions,
    )


def add_to_gate_report(
    gate_report: dict[str, Any],
    stage_results: Sequence[StageResult],
    benchmark: AdmissionBenchmark,
) -> None:
    """Write the benchmark into the gate report: its windows at the top, and each
    strategy's ``versus_benchmark`` for every window a stage kept a run of (the run's
    dates say which) and for each walk-forward test fold."""
    gate_report["benchmark"] = benchmark.block()
    windows = {"selection": benchmark.selection, "evaluation": benchmark.evaluation}

    runs: dict[str, dict[str, SimulationRunResult]] = {}
    for stage_result in stage_results:
        for strategy_id, diagnostics in stage_result.diagnostics.items():
            result = diagnostics.reference_result
            if result is None:
                continue
            for name, series in windows.items():
                if (result.start_date, result.end_date) == (series.first, series.last):
                    runs.setdefault(strategy_id, {}).setdefault(name, result)

    for record in gate_report.get("strategies", []):
        strategy_id = record["strategy_id"]
        versus: dict[str, Any] = {
            name: run_versus_benchmark(result, windows[name])
            for name, result in runs.get(strategy_id, {}).items()
        }
        folds = next((entry["folds"] for entry in record["stages"] if "folds" in entry), None)
        if folds:
            versus["test_folds"] = [_fold_versus(fold, benchmark.test_folds) for fold in folds]
        record["versus_benchmark"] = _json_safe(versus)
    _log(gate_report)


def benchmark_not_built(reason: str) -> dict[str, Any]:
    return {"not_built": reason}


class _BarReader(Protocol):
    def read(
        self,
        *,
        dataset: ParquetDataset,
        dataset_version: str,
        symbol: str,
        start_date: date,
        end_date: date,
        engine: str = "pyarrow",
    ) -> Any: ...


def read_daily_closes(
    *,
    reader: _BarReader,
    dataset: ParquetDataset,
    dataset_version: str,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
) -> dict[str, list[tuple[datetime, float]]]:
    """Each name's first and last bar of every day, as (timestamp, close) in time order:
    all :func:`benchmark_series` reads. One name is read at a time and only these two
    columns are kept, so 24 months of 31 names is ~33k points, not 1.3M bar objects."""
    out: dict[str, list[tuple[datetime, float]]] = {}
    for symbol in symbols:
        table = reader.read(
            dataset=dataset,
            dataset_version=dataset_version,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        ).select(["timestamp", "close"])
        rows = sorted(
            (_utc(timestamp), float(close))
            for timestamp, close in zip(
                table.column("timestamp").to_pylist(),
                table.column("close").to_pylist(),
                strict=True,
            )
        )
        ends: dict[date, list[tuple[datetime, float]]] = {}
        for row in rows:
            day = ends.setdefault(row[0].date(), [row])
            if len(day) == 1 and day[0] is not row:
                day.append(row)
            elif len(day) == 2:
                day[1] = row
        out[symbol] = [row for day in ends.values() for row in day]
    return out


def _fold_versus(fold: Mapping[str, Any], test_folds: Sequence[BenchmarkSeries]) -> dict[str, Any]:
    index = fold.get("fold")
    test_return = fold.get("test_return")
    series = test_folds[index] if isinstance(index, int) and 0 <= index < len(test_folds) else None
    benchmark_return = series.block()["total_return"] if series is not None else None
    excess = (
        test_return - benchmark_return
        if isinstance(test_return, float) and benchmark_return is not None
        else None
    )
    return {
        "fold": index,
        "test_return": test_return,
        "benchmark_return": benchmark_return,
        "excess_return": excess,
    }


def _is_dividend(action: CorporateAction) -> bool:
    return action.action_type is CorporateActionType.CASH_DIVIDEND


def _compound(daily: Sequence[float]) -> float | None:
    if not daily:
        return None
    total = 1.0
    for r in daily:
        total *= 1.0 + r
    return total - 1.0


def _annualised_ratio(daily: Sequence[float]) -> float | None:
    """mean / sample std * sqrt(252); None with fewer than two days or no spread."""
    if len(daily) < 2:
        return None
    mean = sum(daily) / len(daily)
    variance = sum((r - mean) ** 2 for r in daily) / (len(daily) - 1)
    if variance <= 0.0:
        return None
    return mean / math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _utc(timestamp: Any) -> datetime:
    """A bar or equity timestamp (datetime or pandas Timestamp) as an aware UTC datetime."""
    moment: datetime = (
        timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
    )
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _log(gate_report: Mapping[str, Any]) -> None:
    block = gate_report.get("benchmark", {})
    logger.info(
        "benchmark | buy and hold of the names in force | selection %s | evaluation %s | folds %s",
        _shown(block.get("selection", {}).get("total_return")),
        _shown(block.get("evaluation", {}).get("total_return")),
        " / ".join(_shown(fold.get("total_return")) for fold in block.get("test_folds", [])),
    )
    for record in gate_report.get("strategies", []):
        versus = record.get("versus_benchmark") or {}
        parts = [
            f"{name} excess={_shown(versus[name].get('excess_return'))} "
            f"ir={_shown(versus[name].get('information_ratio'))}"
            for name in ("selection", "evaluation")
            if name in versus
        ]
        if "test_folds" in versus:
            parts.append(
                "folds excess="
                + " / ".join(_shown(fold.get("excess_return")) for fold in versus["test_folds"])
            )
        logger.info("versus_benchmark | %s | %s", record["strategy_id"], " | ".join(parts) or "-")


def _shown(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.4f}"
