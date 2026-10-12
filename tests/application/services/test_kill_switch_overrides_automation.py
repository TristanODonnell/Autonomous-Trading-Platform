"""Pre-launch plan 3.1: the kill switch overrides every automation, whatever its own switch says."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.auto_demotion_service import (
    AutoDemotionService,
)
from autonomous_trading_platform.application.services.auto_promotion_service import (
    AutoPromotionService,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    PortfolioReviewMode,
)
from autonomous_trading_platform.storage.sor.models.allocation_rebalance_history import (
    AllocationRebalanceHistory,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.portfolio_reviews import PortfolioReviewRow
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from tests.application.services import test_auto_demotion_service as demotion_tests
from tests.application.services import test_auto_promotion_service as promotion_tests
from tests.application.services import test_portfolio_review_service as review_tests
from tests.application.services import test_quality_based_reallocation_service as rebalance_tests


def _kill(session: Session) -> None:
    KillSwitchStateRepository(session).enable(
        reason="incident", updated_by="ops", updated_at=datetime.now(UTC)
    )


def test_auto_promotion_is_skipped_under_the_kill_switch(db_session: Session) -> None:
    promotion_tests._seed_rule(db_session)
    promotion_tests._seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True}, updated_by="test"
    )
    _kill(db_session)

    result = AutoPromotionService(session=db_session).run(actor="test")

    assert result.skipped_reason == "kill_switch_enabled"
    assert result.auto_promote_enabled is True  # the switch was on; the kill switch won
    assert result.promotions_executed == []
    governance = db_session.get(
        StrategyGovernance, {"strategy_id": "eligible", "config_hash": "eligible_hash"}
    )
    assert governance.current_state == "candidate"
    assert (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_AUTO_PROMOTION_SKIPPED")
        .count()
        == 1
    )


def test_auto_demotion_is_skipped_under_the_kill_switch(db_session: Session) -> None:
    demotion_tests._seed_strategy(
        db_session, "breach", state="approved_for_live_trading", drawdown=0.25
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12}, updated_by="test"
    )
    _kill(db_session)

    result = AutoDemotionService(session=db_session).run(actor="test")

    assert result.skipped_reason == "kill_switch_enabled"
    assert result.demotions_executed == []
    assert [c.strategy_id for c in result.candidates] == ["breach"]  # still detected, not acted on
    assert demotion_tests._get_governance(db_session, "breach").current_state == (
        "approved_for_live_trading"
    )


def test_allocation_rebalance_is_skipped_under_the_kill_switch(db_session: Session) -> None:
    rebalance_tests._seed_policy(db_session, max_pct=0.80)
    rebalance_tests._seed_strategy(
        db_session, "good", sharpe=2.0, total_return=0.20, max_drawdown=0.03
    )
    rebalance_tests._seed_strategy(
        db_session, "bad", sharpe=-0.5, total_return=-0.10, max_drawdown=0.30
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "per_strategy_cap": 0.10}, updated_by="test"
    )
    _kill(db_session)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason == "kill_switch_enabled"
    assert result.auto_rebalance_enabled is True
    history = db_session.query(AllocationRebalanceHistory).one()
    assert history.status == "skipped_kill_switch"
    assert history.skipped_reason == "kill_switch_enabled"
    assert (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_ALLOCATION_REBALANCED").count()
        == 0
    )


def test_auto_review_runs_as_advisory_under_the_kill_switch(db_session: Session) -> None:
    review_tests._seed_swap(db_session)
    review_tests._hold(db_session, "a1")
    reallocation = review_tests._FakeReallocation()
    _kill(db_session)

    result = review_tests._auto_swap(db_session, reallocation=reallocation)

    assert result.mode == PortfolioReviewMode.ADVISORY
    assert result.kill_switch_blocked is True
    assert result.decisions and all(d.applied is False for d in result.decisions)
    statuses = review_tests._statuses(db_session)
    assert statuses["a1"] == "active" and statuses["a2"] == "active"
    assert reallocation.calls == []
    row = db_session.get(PortfolioReviewRow, result.review_id)
    assert row is not None and row.mode == "advisory"


def test_review_records_the_worker_run_it_consumed(db_session: Session) -> None:
    review_tests._seed_swap(db_session)
    review_tests._mode(db_session, PortfolioReviewMode.ADVISORY)

    result = review_tests._service(db_session).run(
        now=review_tests.T0, worker_run_id="six_month_2026w42"
    )

    assert result is not None
    assert result.worker_run_id == "six_month_2026w42"
    assert result.kill_switch_blocked is False
    row = db_session.get(PortfolioReviewRow, result.review_id)
    assert row is not None and row.worker_run_id == "six_month_2026w42"
