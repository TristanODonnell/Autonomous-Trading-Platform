"""Per-strategy live metrics in portfolio mode come from each strategy's own sleeve."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.auto_promotion_service import (
    AutoPromotionService,
)
from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
)
from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    StrategySleeveLedgerRow,
    StrategySleevePositionRow,
    StrategySleeveSnapshotRow,
)

_DAY0 = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)


def _snapshots(session: Session, strategy_id: str, net_pnls: list[float], alloc: float) -> None:
    for i, net in enumerate(net_pnls):
        session.add(
            StrategySleeveSnapshotRow(
                snapshot_id=uuid4(),
                strategy_id=strategy_id,
                run_id=None,
                timestamp=_DAY0 + timedelta(days=i),
                allocated_capital=Decimal(str(alloc)),
                market_value=Decimal("0"),
                cost_basis=Decimal("0"),
                realized_pnl=Decimal(str(net)),
                unrealized_pnl=Decimal("0"),
                fees=Decimal("0"),
                net_pnl=Decimal(str(net)),
                position_count=0,
            )
        )
    session.flush()


def _sell(session: Session, strategy_id: str, realized: str, *, source: str, day: int) -> None:
    session.add(
        StrategySleeveLedgerRow(
            entry_id=uuid4().hex,
            strategy_id=strategy_id,
            symbol="AAPL",
            side="sell",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fees=Decimal("0"),
            realized_pnl=Decimal(realized),
            source=source,
            timestamp=_DAY0 + timedelta(days=day),
        )
    )
    session.flush()


def test_two_strategies_in_one_run_get_their_own_metrics(db_session: Session) -> None:
    # Same account, same run — but each strategy's P&L path is its own.
    _snapshots(db_session, "winner", [0, 100, 200, 150, 300, 420], alloc=10_000)
    _snapshots(db_session, "loser", [0, -50, -120, -60, -200, -260], alloc=10_000)
    service = LivePerformanceMetricsService(db_session)
    now = _DAY0 + timedelta(days=6)

    winner = service.compute_for_strategy("winner", now=now)
    loser = service.compute_for_strategy("loser", now=now)

    assert winner.rolling_sharpe is not None and winner.rolling_sharpe > 0
    assert loser.rolling_sharpe is not None and loser.rolling_sharpe < 0
    assert winner.realized_return == pytest.approx(0.042, abs=1e-3)
    assert loser.realized_return == pytest.approx(-0.026, abs=1e-3)
    assert loser.realized_drawdown is not None and loser.realized_drawdown < 0
    assert winner.days_live == 6


def test_returns_are_relative_to_each_strategys_allocated_capital(db_session: Session) -> None:
    # Same dollar P&L on a 4x larger budget is a 4x smaller return.
    _snapshots(db_session, "small", [0, 100, 200], alloc=5_000)
    _snapshots(db_session, "large", [0, 100, 200], alloc=20_000)
    service = LivePerformanceMetricsService(db_session)
    now = _DAY0 + timedelta(days=3)

    small = service.compute_for_strategy("small", now=now)
    large = service.compute_for_strategy("large", now=now)

    assert small.realized_return is not None and large.realized_return is not None
    assert small.realized_return == pytest.approx(4 * large.realized_return, rel=1e-2)


def test_trades_come_from_sleeve_sells_including_internal_crosses(db_session: Session) -> None:
    _snapshots(db_session, "alpha", [0, 10, 40], alloc=10_000)
    _sell(db_session, "alpha", "50", source="broker_fill", day=1)
    _sell(db_session, "alpha", "-20", source="broker_fill", day=1)
    _sell(db_session, "alpha", "10", source="internal_cross", day=2)

    metrics = LivePerformanceMetricsService(db_session).compute_for_strategy(
        "alpha", now=_DAY0 + timedelta(days=3)
    )

    assert metrics.trade_count == 3
    assert metrics.winning_trade_count == 2


def test_metrics_are_as_of_the_given_time(db_session: Session) -> None:
    # A backtest asks "as of the replay tick": later data must not leak in.
    _snapshots(db_session, "alpha", [0, 100, 200, -500, -900], alloc=10_000)
    service = LivePerformanceMetricsService(db_session)

    early = service.compute_for_strategy("alpha", now=_DAY0 + timedelta(days=2, hours=1))
    late = service.compute_for_strategy("alpha", now=_DAY0 + timedelta(days=5))

    assert early.realized_return == pytest.approx(0.02, abs=1e-3)
    assert early.realized_drawdown is None
    assert late.realized_drawdown is not None and late.realized_drawdown < 0


def test_refresh_monitored_persists_snapshots_for_every_monitored_strategy(
    db_session: Session,
) -> None:
    now = _DAY0 + timedelta(days=3)
    db_session.add(
        StrategyGovernance(
            strategy_id="approved",
            config_hash="h",
            current_state="approved_for_paper_trading",
            experiment_id="e",
            source_run_id=None,
            submitted_at=_DAY0,
            updated_at=_DAY0,
            submitted_by="test",
        )
    )
    db_session.add(
        StrategyGovernance(
            strategy_id="bench",
            config_hash="h",
            current_state="candidate",
            experiment_id="e",
            source_run_id=None,
            submitted_at=_DAY0,
            updated_at=_DAY0,
            submitted_by="test",
        )
    )
    db_session.add(
        PortfolioMembershipRow(
            strategy_id="member",
            status="winding_down",
            since=_DAY0,
            updated_by="test",
            updated_at=_DAY0,
        )
    )
    db_session.add(
        StrategySleevePositionRow(
            strategy_id="__unattributed__",
            symbol="AAPL",
            quantity=Decimal("1"),
            avg_cost=Decimal("100"),
            updated_at=_DAY0,
        )
    )
    db_session.flush()
    service = LivePerformanceMetricsService(db_session)

    refreshed = service.refresh_monitored(now=now)

    assert sorted(m.strategy_id for m in refreshed) == ["__unattributed__", "approved", "member"]
    latest = service.get_latest("approved")
    assert latest is not None
    assert latest.computed_at == now


def test_auto_promotion_evaluates_live_metrics_as_of_the_given_time(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[datetime | None] = []
    original = LivePerformanceMetricsService.compute_for_strategy

    def spy(self, strategy_id, **kwargs):
        seen.append(kwargs.get("now"))
        return original(self, strategy_id, **kwargs)

    monkeypatch.setattr(LivePerformanceMetricsService, "compute_for_strategy", spy)
    db_session.add(
        StrategyGovernance(
            strategy_id="cand",
            config_hash="h",
            current_state="candidate",
            experiment_id="e",
            source_run_id=None,
            submitted_at=_DAY0,
            updated_at=_DAY0,
            submitted_by="test",
        )
    )
    db_session.flush()
    as_of = _DAY0 + timedelta(days=10)

    AutoPromotionService(session=db_session).run(actor="test", enforce_enabled=False, now=as_of)

    assert seen and all(value == as_of for value in seen)
