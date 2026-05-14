from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


def test_quality_reallocation_rewards_good_strategy_and_reduces_bad_strategy(
    db_session: Session,
) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "good", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
    _seed_strategy(db_session, "bad", sharpe=-0.5, total_return=-0.10, max_drawdown=0.30)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "per_strategy_cap": 0.10},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.after_allocation["good"] > result.after_allocation["bad"]
    assert result.after_allocation["good"] <= result.proposals[0].cap_pct
    assert result.audit_event_type == "STRATEGY_ALLOCATION_REBALANCED"
    assert (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_ALLOCATION_REBALANCED").count()
        == 1
    )


def test_quality_reallocation_respects_disabled_override_and_policy_cap(
    db_session: Session,
) -> None:
    _seed_policy(db_session, max_pct=0.90)
    _seed_strategy(db_session, "good", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
    _seed_strategy(db_session, "manual", sharpe=1.5, total_return=0.15, max_drawdown=0.04)
    _seed_strategy(db_session, "disabled", sharpe=3.0, total_return=0.30, max_drawdown=0.01)
    now = datetime.now(UTC)
    db_session.add(
        AllocationOverrides(
            override_id="manual_override",
            strategy_id="manual",
            overridden_by="risk-manager",
            override_reason="manual cap",
            max_pct_of_capital=0.22,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now,
            expires_at=None,
        )
    )
    db_session.add(
        StrategyControlState(
            strategy_id="disabled",
            enabled=False,
            reason="operator disabled",
            updated_by="test",
            updated_at=now,
        )
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "per_strategy_cap": 0.25},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.after_allocation["manual"] == result.before_allocation["manual"]
    assert result.after_allocation["manual"] == result.after_allocation["manual"].__class__(
        "0.220000"
    )
    assert result.after_allocation["disabled"] == result.after_allocation["disabled"].__class__(
        "0.000000"
    )
    assert all(row.after_pct <= row.cap_pct for row in result.proposals)
    assert all(row.cap_pct == row.cap_pct.__class__("0.900000") for row in result.proposals)
    assert any(row.manual_override_respected for row in result.proposals)


def test_quality_reallocation_skips_without_changes_when_flag_disabled(
    db_session: Session,
) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "good", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": False},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason == "auto_rebalance_disabled"
    assert result.before_allocation == result.after_allocation
    assert db_session.query(AllocationOverrides).all() == []
    assert (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_SKIPPED")
        .count()
        == 1
    )


def _seed_policy(session: Session, *, max_pct: float) -> None:
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
            notes="test",
        )
    )
    session.flush()


def _seed_strategy(
    session: Session,
    strategy_id: str,
    *,
    sharpe: float,
    total_return: float,
    max_drawdown: float,
) -> None:
    now = datetime.now(UTC)
    run_id = f"run_{strategy_id}"
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
            current_state="approved_for_paper_trading",
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
            run_id=run_id,
            created_at=now,
            total_return=total_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_drawdown,
            trade_count=20,
            winning_trade_count=12,
            losing_trade_count=8,
            volatility=0.10,
            metrics_json={"strategy_id": strategy_id, "win_rate": 0.60},
        )
    )
    session.flush()
