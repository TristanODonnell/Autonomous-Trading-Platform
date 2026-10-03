"""Bench re-simulation: fixed configs over one shared window, kept out of approval metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.bench_resimulation_service import (
    BenchResimulationService,
    BenchWindow,
    daily_returns,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
    metrics_quality_score,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from tests.application.services.test_quality_based_reallocation_service import _seed_strategy

_T0 = datetime(2024, 3, 1, tzinfo=UTC)
_WINDOW = BenchWindow(
    dataset_version="raw_bars_test",
    price_basis=PriceBasis.RAW,
    symbols=["AAPL", "MSFT"],
    start_date=date(2024, 1, 2),
    end_date=date(2024, 3, 1),
)


def _equity(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [_T0 + timedelta(days=i) for i in range(len(values))],
            "equity": values,
        }
    )


def _result(equity: list[float], *, trades: int = 12, sharpe: float = 1.5) -> Any:
    return SimpleNamespace(
        run_id=uuid4(),
        trade_count=trades,
        return_metrics=SimpleNamespace(total_return=equity[-1] / equity[0] - 1),
        risk_metrics=SimpleNamespace(sharpe_ratio=sharpe, max_drawdown=-0.04),
        trade_metrics=SimpleNamespace(win_rate=0.55, daily_turnover=3.5),
        equity_curve=_equity(equity),
    )


@dataclass
class _FakeRunner:
    results: dict[str, Any]
    requests: list[SimulationRunRequest]

    def run(self, request: SimulationRunRequest) -> Any:
        self.requests.append(request)
        outcome = self.results[request.strategy_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _config(session: Session, strategy_id: str, strategy_type: str, config_json: dict) -> None:
    session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            config_json=config_json,
            created_at=_T0,
            strategy_type=strategy_type,
        )
    )
    session.flush()


def test_resimulates_the_stored_researched_config_over_the_shared_window(
    db_session: Session,
) -> None:
    params = {"lookback": 5, "buy_above": 0.1, "sell_below": -0.1}
    _config(
        db_session,
        "momentum__abc",
        "momentum",
        {"type": "momentum", "strategy_id": "momentum__abc", "parameters": params},
    )
    runner = _FakeRunner({"momentum__abc": _result([100, 101, 103])}, [])

    outcomes = BenchResimulationService(db_session, runner).resimulate(
        ["momentum__abc"], window=_WINDOW, review_id="r1"
    )

    request = runner.requests[0]
    assert request.strategy_config == {
        "type": "momentum",
        "strategy_id": "momentum__abc",
        "parameters": params,
    }
    assert (request.start_date, request.end_date) == (_WINDOW.start_date, _WINDOW.end_date)
    assert request.symbols == ["AAPL", "MSFT"]
    assert request.dataset_version == "raw_bars_test"
    assert request.experiment_id == "bench_resim_r1"
    assert outcomes["momentum__abc"].ok


def test_outcome_carries_metrics_shared_score_and_daily_returns(db_session: Session) -> None:
    _config(db_session, "s1", "momentum", {})
    result = _result([100, 102, 99.96], trades=12, sharpe=1.5)
    runner = _FakeRunner({"s1": result}, [])

    outcome = BenchResimulationService(db_session, runner).resimulate(
        ["s1"], window=_WINDOW, review_id="r1"
    )["s1"]

    assert outcome.run_id == result.run_id
    assert outcome.trade_count == 12
    assert outcome.score == metrics_quality_score(
        sharpe=1.5,
        total_return=outcome.total_return,
        max_drawdown=-0.04,
        win_rate=0.55,
        trade_count=12,
    )
    assert list(outcome.daily_returns.round(4)) == [0.02, -0.02]
    assert outcome.daily_turnover == 3.5  # feeds the review's turnover lens (step 5c-G)


def test_one_failure_does_not_stop_the_others(db_session: Session) -> None:
    _config(db_session, "bad", "momentum", {})
    _config(db_session, "good", "momentum", {})
    runner = _FakeRunner({"bad": RuntimeError("no bars"), "good": _result([100, 101])}, [])

    outcomes = BenchResimulationService(db_session, runner).resimulate(
        ["bad", "good", "missing"], window=_WINDOW, review_id="r1"
    )

    assert outcomes["bad"].error == "no bars"
    assert outcomes["good"].ok
    assert outcomes["missing"].error == "missing_strategy_config"
    assert [r.strategy_id for r in runner.requests] == ["bad", "good"]


def test_daily_returns_use_the_last_equity_point_per_day() -> None:
    curve = pd.DataFrame(
        {
            "timestamp": [
                _T0,
                _T0 + timedelta(hours=3),
                _T0 + timedelta(days=1),
                _T0 + timedelta(days=1, hours=3),
            ],
            "equity": [100.0, 110.0, 99.0, 121.0],
        }
    )

    returns = daily_returns(curve)

    assert list(returns.round(4)) == [0.1]  # 110 -> 121


def _bench_run(session: Session, strategy_id: str, *, sharpe: float) -> None:
    run_id = f"bench_{uuid4().hex[:12]}"
    session.add(
        SimulationRuns(
            run_id=run_id,
            experiment_id="bench_resim_r1",
            strategy_id=strategy_id,
            dataset_version="raw_bars_test",
            universe_version="v1",
            price_basis="raw",
            symbols=["AAPL"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 3, 1),
            start_time=datetime.now(UTC),
            execution_config={},
            status="completed",
        )
    )
    session.add(
        MetricsSummary(
            metrics_snapshot_id=f"metrics_{run_id}",
            run_id=run_id,
            created_at=datetime.now(UTC) + timedelta(days=1),  # newer than approval metrics
            total_return=-0.5,
            sharpe_ratio=sharpe,
            max_drawdown=-0.6,
            trade_count=3,
            winning_trade_count=0,
            losing_trade_count=3,
            volatility=0.5,
            metrics_json={"strategy_id": strategy_id, "win_rate": 0.0},
        )
    )
    session.flush()


def test_bench_resim_runs_never_replace_approval_backtest_metrics(db_session: Session) -> None:
    _seed_strategy(db_session, "s1", sharpe=2.0, total_return=0.2, max_drawdown=-0.05)
    _bench_run(db_session, "s1", sharpe=-3.0)

    metrics = QualityBasedReallocationService(session=db_session)._latest_metrics("s1")

    assert metrics["sharpe_ratio"] == pytest.approx(2.0)
    assert metrics["total_return"] == pytest.approx(0.2)


def test_bench_only_strategy_has_no_approval_metrics(db_session: Session) -> None:
    _bench_run(db_session, "fresh", sharpe=1.0)

    metrics = QualityBasedReallocationService(session=db_session)._latest_metrics("fresh")

    assert metrics["sharpe_ratio"] is None


def test_metrics_quality_score_is_neutral_at_one() -> None:
    assert metrics_quality_score(
        sharpe=None, total_return=None, max_drawdown=None, win_rate=None, trade_count=None
    ) == Decimal("1")
    assert metrics_quality_score(
        sharpe=-5.0, total_return=-0.5, max_drawdown=-0.9, win_rate=0.0, trade_count=0
    ) == Decimal("0.01")


def test_run_id_type_is_preserved(db_session: Session) -> None:
    _config(db_session, "s1", "momentum", {})
    result = _result([100, 101])
    runner = _FakeRunner({"s1": result}, [])

    outcome = BenchResimulationService(db_session, runner).resimulate(
        ["s1"], window=_WINDOW, review_id="r1"
    )["s1"]

    assert isinstance(outcome.run_id, UUID)
