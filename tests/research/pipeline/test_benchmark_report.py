"""Buy and hold of the names in force, and candidates against it (plan G9, Step 1b row 5).

Every expected number below is worked out by hand from the prices written next to it.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pytest

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.research.pipeline.benchmark_report import (
    AdmissionBenchmark,
    BenchmarkSeries,
    add_to_gate_report,
    benchmark_series,
    candidate_returns,
    read_daily_closes,
    versus_benchmark,
)

D1, D2, D3, D4 = (date(2026, 3, day) for day in (2, 3, 4, 5))


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def _split(symbol: str, day: date, ratio: str) -> CorporateAction:
    return CorporateAction(
        action_id=f"{symbol}-split-{day}",
        symbol=symbol,
        action_type=CorporateActionType.SPLIT_FORWARD,
        effective_date=day,
        split_ratio=Decimal(ratio),
        currency="USD",
        new_symbol=symbol,
        source="test",
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _dividend(symbol: str, day: date, per_share: str) -> CorporateAction:
    return CorporateAction(
        action_id=f"{symbol}-dividend-{day}",
        symbol=symbol,
        action_type=CorporateActionType.CASH_DIVIDEND,
        effective_date=day,
        cash_amount=Decimal(per_share),
        currency="USD",
        new_symbol=symbol,
        source="test",
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# A: +10% on D1 (from its first bar), D2 and D4, flat on D3.
# B: flat on D1; a 2-for-1 split on D2 and a close of 26 (52 for one old share: +4%);
#    no bar on D3; on D4 a close of 27 and a 0.50 dividend: (27 + 0.5) / 26 - 1.
# C: in the dataset all along, in force only from D3; +10% from D2's close to D3's.
CLOSES = {
    "A": [(_at(D1, 14, 30), 100.0), (_at(D1, 20, 55), 110.0), (_at(D2, 20, 55), 121.0),
          (_at(D3, 20, 55), 121.0), (_at(D4, 20, 55), 133.1)],
    "B": [(_at(D1, 14, 30), 50.0), (_at(D1, 20, 55), 50.0), (_at(D2, 20, 55), 26.0),
          (_at(D4, 20, 55), 27.0)],
    "C": [(_at(D1, 20, 55), 10.0), (_at(D2, 20, 55), 10.0), (_at(D3, 20, 55), 11.0)],
}  # fmt: skip
ACTIONS = [_split("B", D2, "2"), _dividend("B", D4, "0.50")]


def _members(moment: datetime) -> frozenset[str]:
    return frozenset({"A", "B"} if moment.date() < D3 else {"A", "B", "C"})


def _series(**overrides) -> BenchmarkSeries:
    kwargs = {
        "closes_by_symbol": CLOSES,
        "actions": ACTIONS,
        "members_at": _members,
        "first": D1,
        "last": D4,
    }
    return benchmark_series(**{**kwargs, **overrides})


# -- buy and hold ----------------------------------------------------------------------


def test_each_day_is_the_mean_move_of_the_names_in_force() -> None:
    series = _series()
    assert series.returns[D1] == pytest.approx((0.10 + 0.0) / 2)
    assert series.returns[D2] == pytest.approx((0.10 + 0.04) / 2)  # B's split: 2 x 26 / 50
    assert series.returns[D3] == pytest.approx((0.0 + 0.10) / 2)  # B has no bar; C is in
    assert series.returns[D4] == pytest.approx((0.10 + (27 + 0.5) / 26 - 1) / 2)
    assert series.names_held == {D1: 2, D2: 2, D3: 2, D4: 2}


def test_a_split_is_not_counted_as_a_loss() -> None:
    no_actions = _series(actions=[])
    assert no_actions.returns[D2] == pytest.approx((0.10 + 26 / 50 - 1) / 2)  # -48% for B


def test_a_dividend_is_paid_to_the_shares_held() -> None:
    without = _series(actions=[_split("B", D2, "2")])
    assert without.returns[D4] == pytest.approx((0.10 + 27 / 26 - 1) / 2)
    # Paid on the shares held at the ex-date: two after the split, so it is 2 x 0.50
    # per old share, which is what (27 + 0.5) / 26 is in new shares.
    assert _series().returns[D4] - without.returns[D4] == pytest.approx(0.5 / 26 / 2)


def test_a_name_out_of_force_is_not_held() -> None:
    everyone = _series(members_at=lambda _moment: frozenset({"A", "B", "C"}))
    assert everyone.returns[D1] == pytest.approx((0.10 + 0.0 + 0.0) / 3)
    assert _series().returns[D1] == pytest.approx(0.05)


def test_the_window_buys_at_each_names_first_bar() -> None:
    # From D2: A is bought at its first bar of D2 (its only one, 121), so D2 is flat for
    # it; B's split on D2 is already in D2's price and is not applied again.
    series = _series(first=D2)
    assert series.returns[D2] == pytest.approx(0.0)
    assert list(series.returns) == [D2, D3, D4]


def test_the_block_compounds_the_days() -> None:
    block = _series().block()
    total = math.prod(1 + r for r in _series().returns.values()) - 1
    assert block["total_return"] == pytest.approx(total)
    assert block["sessions"] == 4
    assert block["mean_names_held"] == 2
    assert block["max_drawdown"] == 0.0  # every day is up


# -- a candidate's run against it ------------------------------------------------------


def test_a_candidates_days_come_from_its_equity_curve() -> None:
    curve = pd.DataFrame(
        {
            "timestamp": [_at(D1, 14, 30), _at(D1, 20, 55), _at(D2, 14, 30), _at(D2, 20, 55)],
            "equity": [100_000.0, 101_000.0, 100_500.0, 100_990.0],
        }
    )
    returns = candidate_returns(curve, D1, D4)
    assert returns == {
        D1: pytest.approx(101_000 / 100_000 - 1),
        D2: pytest.approx(100_990 / 101_000 - 1),
    }


def test_excess_return_and_information_ratio() -> None:
    benchmark = BenchmarkSeries(
        first=D1, last=D2, returns={D1: 0.01, D2: 0.01}, names_held={D1: 1, D2: 1}
    )
    versus = versus_benchmark({D1: 0.02, D2: 0.04}, benchmark)
    assert versus["days"] == 2
    assert versus["return"] == pytest.approx(1.02 * 1.04 - 1)
    assert versus["benchmark_return"] == pytest.approx(1.01 * 1.01 - 1)
    assert versus["excess_return"] == pytest.approx(1.02 * 1.04 - 1.01 * 1.01)
    # Active returns 0.01 and 0.03: mean 0.02, sample std sqrt(2) x 0.01.
    assert versus["information_ratio"] == pytest.approx(
        0.02 / (math.sqrt(2) * 0.01) * math.sqrt(252)
    )


def test_holding_the_benchmark_beats_it_by_nothing() -> None:
    benchmark = _series()
    versus = versus_benchmark(dict(benchmark.returns), benchmark)
    assert versus["excess_return"] == pytest.approx(0.0)
    assert versus["information_ratio"] is None  # no spread to divide by


# -- reading the closes ----------------------------------------------------------------


class _Reader:
    def __init__(self, tables: dict[str, pa.Table]):
        self.tables = tables
        self.asked: list[str] = []

    def read(self, *, dataset, dataset_version, symbol, start_date, end_date, engine="pyarrow"):
        self.asked.append(symbol)
        return self.tables[symbol]


def test_only_each_days_first_and_last_bar_are_kept() -> None:
    table = pa.table(
        {
            "timestamp": [_at(D1, 15), _at(D1, 14, 30), _at(D1, 20, 55), _at(D2, 14, 30)],
            "close": [101.0, 100.0, 110.0, 120.0],
            "volume": [1, 2, 3, 4],
        }
    )
    reader = _Reader({"A": table})
    closes = read_daily_closes(
        reader=reader, dataset=cast(Any, None), dataset_version="v", symbols=["A"],
        start_date=D1, end_date=D2,
    )  # fmt: skip
    assert closes == {
        "A": [(_at(D1, 14, 30), 100.0), (_at(D1, 20, 55), 110.0), (_at(D2, 14, 30), 120.0)]
    }


# -- into the gate report --------------------------------------------------------------


def _run(first: date, last: date, equities: list[float]) -> SimpleNamespace:
    days = [D1, D2, D3, D4][: len(equities)]
    return SimpleNamespace(
        start_date=first,
        end_date=last,
        equity_curve=pd.DataFrame(
            {"timestamp": [_at(day, 20, 55) for day in days], "equity": equities}
        ),
    )


def test_every_strategy_gets_the_windows_a_stage_kept_a_run_of_and_its_folds() -> None:
    selection = BenchmarkSeries(
        first=D1, last=D2, returns={D1: 0.01, D2: 0.0}, names_held={D1: 2, D2: 2}
    )
    evaluation = BenchmarkSeries(first=D3, last=D4, returns={D3: 0.0}, names_held={D3: 2})
    folds = (
        BenchmarkSeries(first=D3, last=D3, returns={D3: 0.02}, names_held={D3: 2}),
        BenchmarkSeries(first=D4, last=D4, returns={D4: -0.01}, names_held={D4: 2}),
    )
    benchmark = AdmissionBenchmark(selection=selection, evaluation=evaluation, test_folds=folds)
    stage_results = [
        SimpleNamespace(
            diagnostics={
                "s1": SimpleNamespace(reference_result=_run(D1, D2, [100.0, 103.0])),
                "s2": SimpleNamespace(reference_result=_run(D1, D2, [100.0, 100.0])),
            }
        ),
        SimpleNamespace(diagnostics={"s1": SimpleNamespace(reference_result=None)}),
    ]
    report: dict[str, Any] = {
        "strategies": [
            {
                "strategy_id": "s1",
                "stages": [
                    {"stage": "initial_quality_filter"},
                    {
                        "stage": "walk_forward_robustness",
                        "folds": [
                            {"fold": 0, "test_return": 0.05},
                            {"fold": 1, "test_return": 0.0},
                        ],
                    },
                ],
            },
            {"strategy_id": "s2", "stages": [{"stage": "initial_quality_filter"}]},
        ]
    }

    add_to_gate_report(report, cast(Any, stage_results), benchmark)

    assert report["benchmark"]["selection"]["total_return"] == pytest.approx(0.01)
    s1, s2 = (record["versus_benchmark"] for record in report["strategies"])
    # The selection run: one day of +3% (its first point is the run's start) against 1%.
    assert s1["selection"]["return"] == pytest.approx(0.03)
    assert s1["selection"]["excess_return"] == pytest.approx(0.02)
    assert "evaluation" not in s1  # no stage kept a run over the evaluation dates
    assert s1["test_folds"] == [
        {
            "fold": 0,
            "test_return": 0.05,
            "benchmark_return": pytest.approx(0.02),
            "excess_return": pytest.approx(0.03),
        },
        {
            "fold": 1,
            "test_return": 0.0,
            "benchmark_return": pytest.approx(-0.01),
            "excess_return": pytest.approx(0.01),
        },
    ]
    assert s2["selection"]["excess_return"] == pytest.approx(-0.01)
    assert "test_folds" not in s2


def test_a_day_that_is_no_session_is_not_read_and_its_move_lands_on_the_next() -> None:
    # D3 is not a session (a holiday in a synthetic fixture, a hole left out by name):
    # A's flat D3 and C's +10% on it land on D4, measured from D2's closes.
    sessions = {D1, D2, D4}
    series = _series(sessions=sessions)
    assert list(series.returns) == [D1, D2, D4]
    # On D4 the names in force are A, B, C; C has no D4 bar, so A and B are held.
    assert series.returns[D4] == pytest.approx((133.1 / 121 - 1 + (27 + 0.5) / 26 - 1) / 2)

    curve = pd.DataFrame(
        {
            "timestamp": [_at(D1, 20, 55), _at(D2, 20, 55), _at(D3, 20, 55), _at(D4, 20, 55)],
            "equity": [100.0, 102.0, 999.0, 103.0],
        }
    )
    returns = candidate_returns(curve, D1, D4, sessions=sessions)
    assert list(returns) == [D1, D2, D4]
    assert returns[D4] == pytest.approx(103 / 102 - 1)  # D3's point is not read
