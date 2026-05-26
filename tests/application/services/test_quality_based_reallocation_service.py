from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
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
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
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


def test_expired_manual_override_does_not_prevent_auto_rebalance(
    db_session: Session,
) -> None:
    """An expired manual override must not block quality-based rebalancing."""
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "alpha", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
    now = datetime.now(UTC)
    db_session.add(
        AllocationOverrides(
            override_id="expired_manual",
            strategy_id="alpha",
            overridden_by="risk-manager",
            override_reason="old manual cap",
            max_pct_of_capital=0.10,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(days=10),
            expires_at=now - timedelta(seconds=1),  # expired
        )
    )
    db_session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    proposal = next(p for p in result.proposals if p.strategy_id == "alpha")
    assert not proposal.manual_override_respected, (
        "Expired manual override must not be treated as an active manual constraint"
    )
    assert proposal.reason == "quality_weighted"


def test_valid_manual_override_still_prevents_auto_rebalance(
    db_session: Session,
) -> None:
    """A non-expired manual override must still protect the strategy from auto-rebalance changes."""
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "beta", sharpe=2.0, total_return=0.20, max_drawdown=0.03)
    now = datetime.now(UTC)
    db_session.add(
        AllocationOverrides(
            override_id="valid_manual",
            strategy_id="beta",
            overridden_by="risk-manager",
            override_reason="active manual cap",
            max_pct_of_capital=0.15,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(hours=1),
            expires_at=now + timedelta(days=1),  # not expired
        )
    )
    db_session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    proposal = next(p for p in result.proposals if p.strategy_id == "beta")
    assert proposal.manual_override_respected
    assert proposal.after_pct == proposal.after_pct.__class__("0.150000")


def test_auto_override_with_no_expiry_remains_valid(
    db_session: Session,
) -> None:
    """Auto-rebalance overrides with expires_at=None must remain valid until replaced."""
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "gamma", sharpe=1.0, total_return=0.10, max_drawdown=0.05)
    now = datetime.now(UTC)
    db_session.add(
        AllocationOverrides(
            override_id="auto_override",
            strategy_id="gamma",
            overridden_by="auto_rebalance",
            override_reason="quality-based reallocation",
            max_pct_of_capital=0.30,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(hours=2),
            expires_at=None,
        )
    )
    db_session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "min_rebalance_interval_hours": 0},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    # Auto overrides with no expiry should be visible and replaced (not skipped as missing)
    assert "gamma" in result.before_allocation or "gamma" in result.after_allocation
    proposal = next(p for p in result.proposals if p.strategy_id == "gamma")
    assert not proposal.manual_override_respected


def test_expired_override_is_not_selected_by_portfolio_engine(
    db_session: Session,
) -> None:
    """PortfolioEngine.get_allocation() must ignore expired overrides."""
    now = datetime.now(UTC)
    policy = CapitalAllocationPolicies(
        policy_id="policy_live",
        approval_status="approved_paper",
        performance_tier=None,
        max_pct_of_capital=0.40,
        max_position_size_usd=None,
        max_drawdown_allowed=0.20,
        is_active=True,
        created_at=now,
        notes="test",
    )
    db_session.add(policy)
    db_session.add(
        AllocationOverrides(
            override_id="expired_ov",
            strategy_id="delta",
            overridden_by="risk-manager",
            override_reason="stale manual cap",
            max_pct_of_capital=0.05,  # lower than policy
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(days=5),
            expires_at=now - timedelta(seconds=1),  # expired
        )
    )
    db_session.flush()

    engine = PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(db_session),
        overrides_repo=AllocationOverridesRepository(db_session),
        promotion_rules_repo=PromotionRulesRepository(db_session),
        total_capital=100_000.0,
    )
    allocation = engine.get_allocation(
        strategy_id="delta",
        approval_status=GovernanceState.APPROVED_PAPER,
    )

    # The expired override (0.05) must be ignored; policy (0.40) must apply
    assert not allocation.override_applied
    assert allocation.max_pct_of_capital == 0.40


def test_portfolio_engine_and_rebalancer_agree_on_active_override_semantics(
    db_session: Session,
) -> None:
    """PortfolioEngine and QualityBasedReallocationService must classify overrides identically."""
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "epsilon", sharpe=1.5, total_return=0.12, max_drawdown=0.06)
    _seed_strategy(db_session, "zeta", sharpe=0.8, total_return=0.05, max_drawdown=0.12)
    now = datetime.now(UTC)

    # epsilon: expired manual override (should be ignored by both)
    db_session.add(
        AllocationOverrides(
            override_id="expired_epsilon",
            strategy_id="epsilon",
            overridden_by="risk-manager",
            override_reason="old cap",
            max_pct_of_capital=0.02,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(days=3),
            expires_at=now - timedelta(hours=1),
        )
    )
    # zeta: active manual override (should be respected by both)
    db_session.add(
        AllocationOverrides(
            override_id="active_zeta",
            strategy_id="zeta",
            overridden_by="risk-manager",
            override_reason="live cap",
            max_pct_of_capital=0.20,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(hours=1),
            expires_at=now + timedelta(days=7),
        )
    )
    db_session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True},
        updated_by="test",
    )

    engine = PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(db_session),
        overrides_repo=AllocationOverridesRepository(db_session),
        promotion_rules_repo=PromotionRulesRepository(db_session),
        total_capital=100_000.0,
    )

    # PortfolioEngine: epsilon's expired override should NOT apply
    epsilon_alloc = engine.get_allocation(
        strategy_id="epsilon", approval_status=GovernanceState.APPROVED_PAPER
    )
    assert not epsilon_alloc.override_applied

    # PortfolioEngine: zeta's active override SHOULD apply
    zeta_alloc = engine.get_allocation(
        strategy_id="zeta", approval_status=GovernanceState.APPROVED_PAPER
    )
    assert zeta_alloc.override_applied
    assert zeta_alloc.max_pct_of_capital == 0.20

    # Rebalancer: epsilon must be quality-weighted (expired override ignored)
    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")
    epsilon_proposal = next(p for p in result.proposals if p.strategy_id == "epsilon")
    zeta_proposal = next(p for p in result.proposals if p.strategy_id == "zeta")
    assert not epsilon_proposal.manual_override_respected
    assert zeta_proposal.manual_override_respected


def test_expired_override_ignored_event_is_logged(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ALLOCATION_OVERRIDE_EXPIRED_IGNORED must be logged when an expired manual override is skipped."""
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "theta", sharpe=1.2, total_return=0.08, max_drawdown=0.07)
    now = datetime.now(UTC)
    db_session.add(
        AllocationOverrides(
            override_id="expired_theta",
            strategy_id="theta",
            overridden_by="risk-manager",
            override_reason="stale cap",
            max_pct_of_capital=0.05,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(minutes=5),
        )
    )
    db_session.flush()
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True},
        updated_by="test",
    )

    svc_logger = (
        "autonomous_trading_platform.application.services.quality_based_reallocation_service"
    )
    with caplog.at_level(logging.INFO, logger=svc_logger):
        QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    logged_events = [r.message for r in caplog.records if r.name == svc_logger]
    assert any("ALLOCATION_OVERRIDE_EXPIRED_IGNORED" in msg for msg in logged_events)


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
