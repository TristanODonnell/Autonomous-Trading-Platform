"""Rotation report (portfolio rotation step 5A)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.platform_replay.rotation_hooks import (
    build_rotation_summary,
    daily_closes,
)
from autonomous_trading_platform.application.services.rotation_report_service import (
    RotationReportService,
    performance_metrics,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.portfolio_reviews import (
    PortfolioReviewDecisionRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    StrategySleeveSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

D0 = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)


def _day(n: int) -> datetime:
    return D0 + timedelta(days=n)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_performance_metrics_on_a_known_curve() -> None:
    equity = pd.Series([100.0, 110.0, 99.0, 121.0], index=[date(2024, 1, d) for d in (2, 3, 4, 5)])

    m = performance_metrics(equity)

    assert m is not None
    returns = pd.Series([0.10, -0.10, 121 / 99 - 1])
    assert m.total_return == pytest.approx(0.21)
    assert m.max_drawdown == pytest.approx(0.10)  # 110 -> 99
    assert m.trading_days == 3
    assert m.sharpe == pytest.approx(returns.mean() / returns.std(ddof=1) * math.sqrt(252))
    assert m.annualized_volatility == pytest.approx(returns.std(ddof=1) * math.sqrt(252))


def test_performance_metrics_needs_two_points() -> None:
    assert performance_metrics(pd.Series([100.0], index=[date(2024, 1, 2)])) is None


def test_daily_closes_takes_the_last_bar_per_day() -> None:
    bars = pd.DataFrame(
        {
            "timestamp": [
                datetime(2024, 1, 2, 15, tzinfo=UTC),
                datetime(2024, 1, 2, 20, 55, tzinfo=UTC),
                datetime(2024, 1, 3, 20, 55, tzinfo=UTC),
            ],
            "close": [10.0, 11.0, 12.0],
        }
    )

    closes = daily_closes(bars)

    assert closes.to_dict() == {date(2024, 1, 2): 11.0, date(2024, 1, 3): 12.0}


# ---------------------------------------------------------------------------
# From the system of record
# ---------------------------------------------------------------------------


def _snapshot(session: Session, sid: str, at: datetime, net_pnl: str) -> None:
    zero = Decimal("0")
    session.add(
        StrategySleeveSnapshotRow(
            snapshot_id=uuid4(),
            strategy_id=sid,
            timestamp=at,
            market_value=zero,
            cost_basis=zero,
            realized_pnl=zero,
            unrealized_pnl=zero,
            fees=zero,
            net_pnl=Decimal(net_pnl),
            position_count=0,
        )
    )


def _move(session: Session, sid: str, status: MembershipStatus, reason: str, at: datetime) -> None:
    ActivePortfolioService(session).set_status(sid, status, reason, actor="test", now=at)


def _seed_run(session: Session) -> None:
    # a: active all along; b: active, swapped out on day 2 (flat afterwards);
    # c: bench -> on-deck -> swapped in on day 2.
    _move(session, "a", MembershipStatus.ACTIVE, "selected", _day(0))
    _move(session, "b", MembershipStatus.ACTIVE, "selected", _day(0))
    _move(session, "c", MembershipStatus.BENCH, "bench_novel", _day(0))
    _move(session, "c", MembershipStatus.ON_DECK, "review_promote_on_deck", _day(1))
    _move(session, "b", MembershipStatus.ON_DECK, "review_swap", _day(2))
    _move(session, "c", MembershipStatus.ACTIVE, "review_swap", _day(2))
    for n, (a, b, c) in enumerate(
        [("0", "0", None), ("100", "-50", None), ("150", "-40", None), ("120", None, "30")]
    ):
        _snapshot(session, "a", _day(n), a)
        if b is not None:
            _snapshot(session, "b", _day(n), b)
        if c is not None:
            _snapshot(session, "c", _day(n), c)
    session.add(
        BenchEvaluationRow(
            evaluation_id=uuid4(),
            review_id="bench_1",
            strategy_id="c",
            reviewed_at=_day(0),
            tier="pending",
            decision="admit",
            reason="novel",
        )
    )
    session.add(
        BenchEvaluationRow(
            evaluation_id=uuid4(),
            review_id="bench_1",
            strategy_id="x",
            reviewed_at=_day(0),
            tier="pending",
            decision="retire",
            reason="redundant",
        )
    )
    session.add(
        PortfolioReviewDecisionRow(
            decision_id=uuid4(),
            review_id="review_1",
            reviewed_at=_day(2),
            decision_type="swap",
            strategy_id="c",
            counterpart_id="b",
            guardrails={"governance": True},
            applied=True,
            reason="challenger_beat_incumbent",
        )
    )
    session.add(
        Fill(
            fill_id=str(uuid4()),
            broker_order_id="bo-1",
            intent_id=uuid4(),
            run_id=uuid4(),
            timestamp=_day(1),
            symbol="AAPL",
            side="buy",
            quantity=Decimal("10"),
            price=Decimal("100"),
            fees=Decimal("0"),
        )
    )
    session.flush()


def test_report_from_the_system_of_record(db_session: Session) -> None:
    _seed_run(db_session)

    report = RotationReportService(db_session).build(
        start_date=D0.date(), end_date=_day(3).date(), starting_cash=1000
    )

    # Equity: 1000 + a + b (carried forward after b stops) + c.
    assert report.portfolio is not None
    assert report.portfolio.start_value == 1000
    assert report.portfolio.end_value == pytest.approx(1000 + 120 - 40 + 30)
    assert report.total_swaps == 1
    (month,) = report.activity
    assert (month.swaps, month.on_deck_promotions, month.bench_admissions, month.retirements) == (
        1,
        1,
        1,
        1,
    )
    assert month.governance_promotions == 1
    assert month.seats_dropped == 0  # the swapped-out incumbent is part of the swap
    contributions = {c.strategy_id: c for c in report.contributions}
    assert contributions["a"].net_pnl == 120
    assert contributions["b"].net_pnl == -40
    assert contributions["b"].days_by_tier == {"active": 2, "on_deck": 1}
    assert contributions["c"].final_tier == "active"
    assert report.turnover == pytest.approx(1000 / report_mean_equity())


def report_mean_equity() -> float:
    return float(pd.Series([1000.0, 1050.0, 1110.0, 1110.0]).mean())


def test_benchmark_is_buy_and_hold_scaled_to_starting_cash(db_session: Session) -> None:
    _seed_run(db_session)
    closes = pd.Series([100.0, 102.0, 101.0, 105.0], index=[_day(n).date() for n in range(4)])

    report = RotationReportService(db_session).build(
        start_date=D0.date(),
        end_date=_day(3).date(),
        starting_cash=1000,
        benchmark_symbol="SPY",
        benchmark_closes=closes,
    )

    assert report.benchmark is not None
    assert report.benchmark_symbol == "SPY"
    assert report.benchmark.start_value == 1000
    assert report.benchmark.total_return == pytest.approx(0.05)


def test_empty_run_is_reported_with_a_warning(db_session: Session) -> None:
    report = RotationReportService(db_session).build(
        start_date=D0.date(), end_date=_day(3).date(), starting_cash=1000, benchmark_symbol="SPY"
    )

    assert report.portfolio is None
    assert "no_sleeve_snapshots" in report.warnings
    assert "no_benchmark_bars:SPY" in report.warnings


def test_no_rotation_summary_outside_portfolio_mode(db_session: Session) -> None:
    assert (
        build_rotation_summary(
            session=db_session,
            start_date=D0.date(),
            end_date=_day(3).date(),
            starting_cash=1000,
            dataset_version_id=None,
            symbols=["SPY"],
        )
        is None
    )
    OperatorSettingsRepository(db_session).update_current(
        {"portfolio_mode_enabled": True}, updated_by="test"
    )

    report = build_rotation_summary(
        session=db_session,
        start_date=D0.date(),
        end_date=_day(3).date(),
        starting_cash=1000,
        dataset_version_id=None,
        symbols=["SPY"],
    )

    assert report is not None
