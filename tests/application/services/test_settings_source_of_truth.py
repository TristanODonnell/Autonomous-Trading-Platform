from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


def test_manual_promotion_uses_promotion_rules_not_deprecated_operator_settings(
    db_session: Session,
) -> None:
    _seed_promotion_rule(db_session)
    _seed_strategy(db_session, "promotion_candidate", sharpe=1.6, days_tested=15)
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_promote_enabled": False,
            "min_sharpe_for_promotion": 9.9,
            "min_paper_trading_period_days": 365,
        },
        updated_by="test",
    )

    result = StrategyGovernanceService(session=db_session).transition(
        strategy_id="promotion_candidate",
        to_state="paper",
        reason="manual promotion",
        updated_by="risk-manager",
        actor_role="risk_manager",
    )

    assert result.to_state == "approved_for_paper_trading"


def test_allocation_uses_policies_and_overrides_not_legacy_operator_cap(
    db_session: Session,
) -> None:
    _seed_allocation_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "allocation_candidate", state="approved_for_paper_trading")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "per_strategy_cap": 0.05},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.auto_rebalance_enabled is True
    assert result.after_allocation["allocation_candidate"] == Decimal("0.800000")
    assert result.proposals[0].cap_pct == Decimal("0.800000")
    assert Decimal(str(result.active_policies[0]["max_pct_of_capital"])) == Decimal("0.8")


def test_operator_settings_automation_flags_still_gate_automation(
    db_session: Session,
) -> None:
    _seed_allocation_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "gated_candidate", state="approved_for_paper_trading")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": False, "per_strategy_cap": 0.80},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason == "auto_rebalance_disabled"
    assert result.before_allocation == result.after_allocation


def _seed_promotion_rule(session: Session) -> None:
    session.add(
        PromotionRules(
            rule_id="research_to_paper",
            from_status="approved_research",
            to_status="approved_paper",
            min_sharpe=1.5,
            max_drawdown=0.20,
            min_days_tested=10,
            min_trade_count=1,
            min_cagr=None,
            min_win_rate=None,
            is_active=True,
            created_at=datetime.now(UTC),
            notes="source-of-truth test",
        )
    )
    session.flush()


def _seed_allocation_policy(session: Session, *, max_pct: float) -> None:
    session.add(
        CapitalAllocationPolicies(
            policy_id="paper_default",
            approval_status="approved_paper",
            performance_tier=None,
            max_pct_of_capital=max_pct,
            max_position_size_usd=None,
            max_drawdown_allowed=0.20,
            is_active=True,
            created_at=datetime.now(UTC),
            notes="source-of-truth test",
        )
    )
    session.flush()


def _seed_strategy(
    session: Session,
    strategy_id: str,
    *,
    state: str = "approved_research",
    sharpe: float = 2.0,
    days_tested: int = 30,
) -> None:
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
            current_state=state,
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
            total_return=0.10,
            sharpe_ratio=sharpe,
            max_drawdown=0.05,
            trade_count=20,
            winning_trade_count=12,
            losing_trade_count=8,
            volatility=0.10,
            metrics_json={
                "strategy_id": strategy_id,
                "days_tested": days_tested,
                "cagr": 0.10,
                "win_rate": 0.60,
            },
        )
    )
    session.flush()
