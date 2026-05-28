"""GOV-014: Notification matrix tests.

Each flag independently gates its notification event while primary audit events
always persist. Tests cover enabled → emits and disabled → suppresses for:
  - notify_strategy_demotion_events
  - notify_drawdown_alerts
  - notify_allocation_rebalance_events
  - notify_strategy_promotion_events (promotion path, already wired)
  - notify_pipeline_failures (pipeline failure path, spot-check)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.auto_demotion_service import (
    AutoDemotionService,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.runtime.services.pipeline_failure_notification_service import (
    PipelineFailureNotificationService,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_strategy_with_breach(session: Session, strategy_id: str, drawdown: float = 0.25) -> None:
    now = datetime.now(UTC)
    session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            config_json={"strategy_id": strategy_id},
            created_at=now,
            strategy_type="test",
            metadata_json={},
        )
    )
    session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            current_state="approved_for_live_trading",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    session.add(
        MetricsSummary(
            metrics_snapshot_id=f"metrics_{strategy_id}",
            run_id=f"run_{strategy_id}",
            created_at=now,
            total_return=-0.10,
            sharpe_ratio=-0.5,
            max_drawdown=drawdown,
            trade_count=20,
            winning_trade_count=5,
            losing_trade_count=15,
            volatility=0.20,
            metrics_json={"strategy_id": strategy_id},
        )
    )
    session.flush()


def _seed_strategy_for_rebalance(session: Session, strategy_id: str) -> None:
    now = datetime.now(UTC)
    session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            config_json={"strategy_id": strategy_id},
            created_at=now,
            strategy_type="test",
            metadata_json={},
        )
    )
    session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            current_state="approved_for_live_trading",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    session.add(
        MetricsSummary(
            metrics_snapshot_id=f"metrics_{strategy_id}",
            run_id=f"run_{strategy_id}",
            created_at=now,
            total_return=0.15,
            sharpe_ratio=1.8,
            max_drawdown=0.05,
            trade_count=50,
            winning_trade_count=30,
            losing_trade_count=20,
            volatility=0.10,
            metrics_json={"strategy_id": strategy_id},
        )
    )
    session.add(
        CapitalAllocationPolicies(
            policy_id=f"policy_{strategy_id}",
            approval_status="approved_live",
            performance_tier=None,
            max_pct_of_capital=0.50,
            max_position_size_usd=None,
            max_drawdown_allowed=0.25,
            is_active=True,
            created_at=now,
        )
    )
    session.flush()


def _notification_count(session: Session, event_type: str) -> int:
    return cast(
        int,
        session.query(AuditLogRow)
        .filter_by(event_type=event_type, component="notifications")
        .count(),
    )


def _governance_audit_count(session: Session, event_type: str) -> int:
    return cast(int, session.query(AuditLogRow).filter_by(event_type=event_type).count())


# ---------------------------------------------------------------------------
# notify_strategy_demotion_events
# ---------------------------------------------------------------------------


def test_demotion_notification_emitted_when_flag_enabled(db_session: Session) -> None:
    _seed_strategy_with_breach(db_session, "s1")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_demote_on_breach": True,
            "max_strategy_drawdown": 0.12,
            "notify_strategy_demotion_events": True,
        },
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="test")

    assert _notification_count(db_session, "STRATEGY_DEMOTION_EVENT") == 1
    assert _governance_audit_count(db_session, "STRATEGY_AUTO_DEMOTED") == 1


def test_demotion_notification_suppressed_when_flag_disabled(db_session: Session) -> None:
    _seed_strategy_with_breach(db_session, "s2")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_demote_on_breach": True,
            "max_strategy_drawdown": 0.12,
            "notify_strategy_demotion_events": False,
        },
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="test")

    assert _notification_count(db_session, "STRATEGY_DEMOTION_EVENT") == 0
    assert _governance_audit_count(db_session, "STRATEGY_AUTO_DEMOTED") == 1


# ---------------------------------------------------------------------------
# notify_drawdown_alerts
# ---------------------------------------------------------------------------


def test_drawdown_alert_emitted_when_flag_enabled_and_breach(db_session: Session) -> None:
    _seed_strategy_with_breach(db_session, "s3")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_demote_on_breach": True,
            "max_strategy_drawdown": 0.12,
            "notify_drawdown_alerts": True,
        },
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="test")

    assert _notification_count(db_session, "DRAWDOWN_ALERT_EVENT") == 1


def test_drawdown_alert_suppressed_when_flag_disabled(db_session: Session) -> None:
    _seed_strategy_with_breach(db_session, "s4")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_demote_on_breach": True,
            "max_strategy_drawdown": 0.12,
            "notify_drawdown_alerts": False,
        },
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="test")

    assert _notification_count(db_session, "DRAWDOWN_ALERT_EVENT") == 0
    assert _governance_audit_count(db_session, "STRATEGY_AUTO_DEMOTED") == 1


def test_drawdown_alert_emitted_even_when_auto_demote_disabled(db_session: Session) -> None:
    """Drawdown alerts fire for breach candidates even if auto-demotion is off."""
    _seed_strategy_with_breach(db_session, "s5")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_demote_on_breach": False,
            "max_strategy_drawdown": 0.12,
            "notify_drawdown_alerts": True,
        },
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="test")

    assert _notification_count(db_session, "DRAWDOWN_ALERT_EVENT") == 1
    assert _governance_audit_count(db_session, "STRATEGY_AUTO_DEMOTED") == 0


# ---------------------------------------------------------------------------
# notify_allocation_rebalance_events
# ---------------------------------------------------------------------------


def test_rebalance_notification_emitted_when_flag_enabled(db_session: Session) -> None:
    _seed_strategy_for_rebalance(db_session, "r1")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_rebalance_enabled": True,
            "per_strategy_cap": 0.50,
            "notify_allocation_rebalance_events": True,
        },
        updated_by="test",
    )

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert _notification_count(db_session, "STRATEGY_ALLOCATION_REBALANCE_EVENT") == 1
    assert _governance_audit_count(db_session, "STRATEGY_ALLOCATION_REBALANCED") == 1


def test_rebalance_notification_suppressed_when_flag_disabled(db_session: Session) -> None:
    _seed_strategy_for_rebalance(db_session, "r2")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_rebalance_enabled": True,
            "per_strategy_cap": 0.50,
            "notify_allocation_rebalance_events": False,
        },
        updated_by="test",
    )

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert _notification_count(db_session, "STRATEGY_ALLOCATION_REBALANCE_EVENT") == 0
    assert _governance_audit_count(db_session, "STRATEGY_ALLOCATION_REBALANCED") == 1


def test_rebalance_notification_not_emitted_when_skipped(db_session: Session) -> None:
    """No notification when auto-rebalance is disabled (skipped path)."""
    _seed_strategy_for_rebalance(db_session, "r3")
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_rebalance_enabled": False,
            "notify_allocation_rebalance_events": True,
        },
        updated_by="test",
    )

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert _notification_count(db_session, "STRATEGY_ALLOCATION_REBALANCE_EVENT") == 0
    assert _governance_audit_count(db_session, "STRATEGY_ALLOCATION_REBALANCE_SKIPPED") == 1


# ---------------------------------------------------------------------------
# notify_strategy_promotion_events (regression — already wired)
# ---------------------------------------------------------------------------


def _seed_research_to_paper_rule(session: Session) -> None:
    """Minimal rule required by the fail-closed promotion check. No criteria for research->paper."""
    session.add(
        PromotionRules(
            rule_id="research_to_paper_notif",
            from_status="approved_research",
            to_status="approved_paper",
            min_sharpe=None,
            max_drawdown=None,
            min_days_tested=None,
            min_trade_count=None,
            min_cagr=None,
            min_win_rate=None,
            is_active=True,
            created_at=datetime.now(UTC),
            notes=None,
        )
    )
    session.flush()


def test_promotion_notification_suppressed_when_flag_disabled(db_session: Session) -> None:
    now = datetime.now(UTC)
    session = db_session
    _seed_research_to_paper_rule(session)
    session.add(
        StrategyGovernance(
            strategy_id="promo1",
            config_hash="promo1_hash",
            current_state="approved_research",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"notify_strategy_promotion_events": False},
        updated_by="test",
    )

    StrategyGovernanceService(session=db_session).transition(
        strategy_id="promo1",
        to_state="approved_for_paper_trading",
        reason="test",
        updated_by="operator1",
        actor_role="risk_manager",
        source_run_id="promo1_run",
    )

    assert _notification_count(db_session, "STRATEGY_PROMOTION_EVENT") == 0
    assert _governance_audit_count(db_session, "STRATEGY_GOVERNANCE_TRANSITIONED") == 1


def test_promotion_notification_emitted_when_flag_enabled(db_session: Session) -> None:
    now = datetime.now(UTC)
    session = db_session
    _seed_research_to_paper_rule(session)
    session.add(
        StrategyGovernance(
            strategy_id="promo2",
            config_hash="promo2_hash",
            current_state="approved_research",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"notify_strategy_promotion_events": True},
        updated_by="test",
    )

    StrategyGovernanceService(session=db_session).transition(
        strategy_id="promo2",
        to_state="approved_for_paper_trading",
        reason="test",
        updated_by="operator1",
        actor_role="risk_manager",
        source_run_id="promo2_run",
    )

    assert _notification_count(db_session, "STRATEGY_PROMOTION_EVENT") == 1
    assert _governance_audit_count(db_session, "STRATEGY_GOVERNANCE_TRANSITIONED") == 1


# ---------------------------------------------------------------------------
# notify_pipeline_failures (regression — already wired)
# ---------------------------------------------------------------------------


def _make_failed_run(job_name: str = "test_job") -> RuntimeJobRun:
    now = datetime.now(UTC)
    return RuntimeJobRun(
        job_run_id="run-001",
        job_name=job_name,
        trigger_type="scheduler",
        status="failed",
        correlation_id="corr-001",
        parent_job_run_id=None,
        started_at=now,
        completed_at=now,
        duration_ms=100,
        error_message="something broke",
        input_summary_json={},
        output_summary_json=None,
    )


def test_pipeline_failure_notification_emitted_when_flag_enabled(db_session: Session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {"notify_pipeline_failures": True},
        updated_by="test",
    )

    PipelineFailureNotificationService(db_session).notify_failure(_make_failed_run())

    assert _notification_count(db_session, "PIPELINE_FAILURE_EVENT") == 1


def test_pipeline_failure_notification_suppressed_when_flag_disabled(db_session: Session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {"notify_pipeline_failures": False},
        updated_by="test",
    )

    PipelineFailureNotificationService(db_session).notify_failure(_make_failed_run())

    assert _notification_count(db_session, "PIPELINE_FAILURE_EVENT") == 0
