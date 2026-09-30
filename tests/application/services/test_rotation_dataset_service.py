"""Rotation dataset export (portfolio rotation step 5C)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.rotation_dataset_service import (
    EXPORT_EXPERIMENT_ID,
    RotationDatasetService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.rotation_dataset import RotationDataset
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.models.portfolio_reviews import PortfolioReviewRow
from tests.application.services.test_active_portfolio_service import _eligible
from tests.application.services.test_strategy_governance_service import (
    _seed_paper_rule,
    _seed_strategy,
)

START = date(2024, 1, 2)
END = date(2024, 3, 29)


def _ts(d: date, hour: int = 21) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC)


@dataclass
class _Result:
    equity_curve: pd.DataFrame
    trade_logs: pd.DataFrame


class _FakeRunner:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def run(self, request: Any) -> _Result:
        self.requests.append(request)
        stamps = [_ts(START + timedelta(days=i), 20) for i in range(3)]
        return _Result(
            equity_curve=pd.DataFrame({"timestamp": stamps, "equity": [100.0, 101.0, 99.5]}),
            trade_logs=pd.DataFrame(
                {
                    "timestamp": [stamps[0]],
                    "symbol": ["AAPL"],
                    "side": ["BUY"],
                    "quantity": [1.0],
                    "price": [100.0],
                    "fees": [0.0],
                }
            ),
        )


def _bench_eval(session: Session, sid: str, day: date, decision: str) -> None:
    session.add(
        BenchEvaluationRow(
            evaluation_id=uuid4(),
            review_id=f"bench_{day}",
            strategy_id=sid,
            reviewed_at=_ts(day),
            tier="pending",
            decision=decision,
            reason="test",
        )
    )


def _export(session: Session, runner: _FakeRunner) -> RotationDataset:
    return RotationDatasetService(session, runner).export(
        start_date=START,
        end_date=END,
        starting_cash=250_000,
        dataset_version="raw_bars_test",
        symbols=["AAPL", "SPY"],
        market_closes=pd.Series([100.0, 102.0], index=[START, START + timedelta(days=1)]),
        market_symbol="SPY",
        recorded_rotation={"total_swaps": 0},
    )


def test_export_covers_the_pool_with_availability(db_session: Session) -> None:
    _eligible(db_session, "seed")  # approved from the start
    ActivePortfolioService(db_session).set_status(
        "seed", MembershipStatus.ACTIVE, "selected", actor="test", now=_ts(START)
    )
    _eligible(db_session, "admitted", state="candidate")
    _bench_eval(db_session, "admitted", date(2024, 1, 22), "admit")
    _bench_eval(db_session, "admitted", date(2024, 3, 4), "retire")
    _eligible(db_session, "rejected", state="candidate")  # never admitted: not in the pool
    _bench_eval(db_session, "rejected", date(2024, 1, 22), "retire")
    db_session.add(
        PortfolioReviewRow(review_id="r1", reviewed_at=_ts(date(2024, 1, 22)), mode="auto")
    )
    db_session.flush()
    runner = _FakeRunner()

    dataset = _export(db_session, runner)

    by_id = {s.strategy_id: s for s in dataset.strategies}
    assert set(by_id) == {"seed", "admitted"}
    assert by_id["seed"].seeded_approved and by_id["seed"].available_from == START
    assert by_id["admitted"].available_from == date(2024, 1, 22)
    assert by_id["admitted"].retired_on == date(2024, 3, 4)
    assert by_id["admitted"].promotable is False  # no promotion rule seeded
    assert len(by_id["seed"].equity) == 3 and by_id["seed"].fills[0].side == "buy"
    assert dataset.initial_active == ["seed"]
    assert dataset.review_dates == [_ts(date(2024, 1, 22))]
    assert dataset.market_returns == pytest.approx({START + timedelta(days=1): 0.02})
    assert dataset.recorded_rotation == {"total_swaps": 0}
    assert {r.experiment_id for r in runner.requests} == {EXPORT_EXPERIMENT_ID}
    assert all(r.start_date == START and r.end_date == END for r in runner.requests)
    # Round-trips through JSON (the sweep reads it back).
    assert RotationDataset.model_validate_json(dataset.model_dump_json()) == dataset


def test_would_pass_promotion_checks_rules_without_side_effects(db_session: Session) -> None:
    _seed_strategy(db_session, "good", state="candidate", sharpe=2.0, days=45, trades=20)
    _seed_strategy(db_session, "weak", state="candidate", sharpe=0.1, days=45, trades=20)
    _seed_paper_rule(db_session)
    service = StrategyGovernanceService(session=db_session)
    audits_before = db_session.query(AuditLogRow).count()

    good = service.would_pass_promotion(
        "good", "approved_for_paper_trading", source_run_id="run_good"
    )
    weak = service.would_pass_promotion(
        "weak", "approved_for_paper_trading", source_run_id="run_weak"
    )

    assert good == (True, None)
    assert weak[0] is False and "promotion criteria" in (weak[1] or "")
    assert db_session.query(AuditLogRow).count() == audits_before


def test_would_pass_promotion_without_a_rule(db_session: Session) -> None:
    _seed_strategy(db_session, "c1", state="candidate")

    passed, reason = StrategyGovernanceService(session=db_session).would_pass_promotion(
        "c1", "approved_for_paper_trading"
    )

    assert passed is False and reason
