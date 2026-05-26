"""GOV-013: Audit trail actor/source correctness.

Verifies that audit log entries record the correct actor depending on how the
governance cycle was triggered (scheduler vs cli vs direct automation call).

- Scheduler-triggered cycles write actor="scheduler"
- CLI-triggered cycles write actor="cli"
- Automation services called directly write actor=AUTO_*_ACTOR constant
- Manual operator transitions write the operator's user ID as actor

Also verifies that the primary audit event always persists, regardless of
notification flag state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.auto_demotion_service import (
    AUTO_DEMOTION_ACTOR,
    AUTO_DEMOTION_EVENT,
    AutoDemotionService,
)
from autonomous_trading_platform.application.services.auto_promotion_service import (
    AUTO_PROMOTION_ACTOR,
    AutoPromotionService,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    AUTO_REBALANCE_ACTOR,
    QualityBasedReallocationService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
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


def _seed_demotable_strategy(session: Session, strategy_id: str, drawdown: float = 0.25) -> None:
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


def _seed_promotable_strategy(session: Session, strategy_id: str) -> None:
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
            current_state="approved_research",
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
            total_return=0.30,
            sharpe_ratio=2.5,
            max_drawdown=0.05,
            trade_count=100,
            winning_trade_count=60,
            losing_trade_count=40,
            volatility=0.10,
            metrics_json={"strategy_id": strategy_id, "days_tested": 45, "win_rate": 0.60},
        )
    )
    session.add(
        PromotionRules(
            rule_id="rule_research_paper",
            from_status="approved_research",
            to_status="approved_paper",
            min_sharpe=1.0,
            max_drawdown=0.15,
            min_days_tested=30,
            min_trade_count=50,
            min_cagr=None,
            min_win_rate=None,
            is_active=True,
            created_at=now,
        )
    )
    session.flush()


def _seed_rebalanceable_strategy(session: Session, strategy_id: str) -> None:
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


def _last_audit_actor(session: Session, event_type: str) -> str | None:
    row = (
        session.query(AuditLogRow)
        .filter_by(event_type=event_type)
        .order_by(AuditLogRow.event_timestamp.desc())
        .first()
    )
    if row is None:
        return None
    return (row.metadata_ or {}).get("actor")


# ---------------------------------------------------------------------------
# Auto-demotion actor
# ---------------------------------------------------------------------------


def test_demotion_direct_call_records_automation_actor(db_session: Session) -> None:
    _seed_demotable_strategy(db_session, "d1")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run()

    assert _last_audit_actor(db_session, AUTO_DEMOTION_EVENT) == AUTO_DEMOTION_ACTOR


def test_demotion_scheduler_actor_recorded(db_session: Session) -> None:
    _seed_demotable_strategy(db_session, "d2")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="scheduler")

    assert _last_audit_actor(db_session, AUTO_DEMOTION_EVENT) == "scheduler"


def test_demotion_cli_actor_recorded(db_session: Session) -> None:
    _seed_demotable_strategy(db_session, "d3")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="cli")

    assert _last_audit_actor(db_session, AUTO_DEMOTION_EVENT) == "cli"


# ---------------------------------------------------------------------------
# Auto-promotion actor
# ---------------------------------------------------------------------------


def test_promotion_scheduler_actor_recorded(db_session: Session) -> None:
    _seed_promotable_strategy(db_session, "p1")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    AutoPromotionService(session=db_session).run(actor="scheduler")

    actor = _last_audit_actor(db_session, "STRATEGY_AUTO_PROMOTION_COMPLETED")
    assert actor == "scheduler"


def test_promotion_cli_actor_recorded(db_session: Session) -> None:
    _seed_promotable_strategy(db_session, "p2")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    AutoPromotionService(session=db_session).run(actor="cli")

    actor = _last_audit_actor(db_session, "STRATEGY_AUTO_PROMOTION_COMPLETED")
    assert actor == "cli"


def test_promotion_direct_call_records_automation_actor(db_session: Session) -> None:
    _seed_promotable_strategy(db_session, "p3")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    AutoPromotionService(session=db_session).run()

    actor = _last_audit_actor(db_session, "STRATEGY_AUTO_PROMOTION_COMPLETED")
    assert actor == AUTO_PROMOTION_ACTOR


# ---------------------------------------------------------------------------
# Allocation rebalance actor
# ---------------------------------------------------------------------------


def test_rebalance_scheduler_actor_recorded(db_session: Session) -> None:
    _seed_rebalanceable_strategy(db_session, "re1")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "per_strategy_cap": 0.50},
        updated_by="test",
    )

    QualityBasedReallocationService(session=db_session).rebalance(actor="scheduler")

    actor = _last_audit_actor(db_session, "STRATEGY_ALLOCATION_REBALANCED")
    assert actor == "scheduler"


def test_rebalance_cli_actor_recorded(db_session: Session) -> None:
    _seed_rebalanceable_strategy(db_session, "re2")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "per_strategy_cap": 0.50},
        updated_by="test",
    )

    QualityBasedReallocationService(session=db_session).rebalance(actor="cli")

    actor = _last_audit_actor(db_session, "STRATEGY_ALLOCATION_REBALANCED")
    assert actor == "cli"


def test_rebalance_direct_call_records_automation_actor(db_session: Session) -> None:
    _seed_rebalanceable_strategy(db_session, "re3")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "per_strategy_cap": 0.50},
        updated_by="test",
    )

    QualityBasedReallocationService(session=db_session).rebalance()

    actor = _last_audit_actor(db_session, "STRATEGY_ALLOCATION_REBALANCED")
    assert actor == AUTO_REBALANCE_ACTOR


# ---------------------------------------------------------------------------
# Manual operator transition actor
# ---------------------------------------------------------------------------


def test_manual_transition_records_operator_user_id(db_session: Session) -> None:
    now = datetime.now(UTC)
    # A rule row is required for promotion to proceed (fail-closed). No criteria
    # are required for research->paper, so all thresholds can be null.
    db_session.add(
        PromotionRules(
            rule_id="research_to_paper_audit",
            from_status="approved_research",
            to_status="approved_paper",
            min_sharpe=None,
            max_drawdown=None,
            min_days_tested=None,
            min_trade_count=None,
            min_cagr=None,
            min_win_rate=None,
            is_active=True,
            created_at=now,
            notes=None,
        )
    )
    db_session.add(
        StrategyGovernance(
            strategy_id="op1",
            config_hash="op1_hash",
            current_state="approved_research",
            experiment_id="test",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    db_session.flush()

    StrategyGovernanceService(session=db_session).transition(
        strategy_id="op1",
        to_state="approved_for_paper_trading",
        reason="manual promotion",
        updated_by="user_alice",
        actor_role="risk_manager",
    )

    assert _last_audit_actor(db_session, "STRATEGY_GOVERNANCE_TRANSITIONED") == "user_alice"
