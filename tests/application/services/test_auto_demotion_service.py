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
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
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


def test_auto_demotion_scanner_recommends_demotion_on_drawdown_breach(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "breach", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    [candidate] = AutoDemotionService(session=db_session).scan()

    assert candidate.status == "breach"
    assert candidate.recommended_state == "approved_for_paper_trading"
    assert candidate.should_disable is True
    assert candidate.should_zero_allocation is True
    assert "exceeds max_strategy_drawdown" in candidate.reasons[0]


def test_auto_demotion_scanner_no_breach_produces_no_demotion(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "safe", state="approved_for_paper_trading", drawdown=0.03)
    OperatorSettingsRepository(db_session).update_current(
        {"max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    [candidate] = AutoDemotionService(session=db_session).scan()

    assert candidate.status == "no_breach"
    assert candidate.recommended_state is None


def test_auto_demotion_disabled_flag_prevents_state_change(db_session: Session) -> None:
    _seed_strategy(db_session, "breach", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": False, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    result = AutoDemotionService(session=db_session).run(actor="test")
    governance = _get_governance(db_session, "breach")

    assert result.skipped_reason == "auto_demote_disabled"
    assert result.demotions_executed == []
    assert governance.current_state == "approved_for_live_trading"
    assert (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_AUTO_DEMOTION_SKIPPED").count()
        == 1
    )


def test_auto_demotion_enabled_changes_state_disables_and_zeroes_allocation(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "breach", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    result = AutoDemotionService(session=db_session).run(actor="test")
    governance = _get_governance(db_session, "breach")
    control = db_session.get(StrategyControlState, "breach")
    override = db_session.query(AllocationOverrides).filter_by(strategy_id="breach").one()

    assert result.demotions_executed[0]["new_governance_state"] == "approved_for_paper_trading"
    assert governance.current_state == "approved_for_paper_trading"
    assert control.enabled is False
    assert override.max_pct_of_capital == 0.0
    audit = db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_AUTO_DEMOTED").one()
    assert audit.event_metadata["actor_role"] == "system_risk"
    assert audit.event_metadata["breached_metric"] == "drawdown"
    assert audit.event_metadata["previous_state"] == "approved_for_live_trading"
    assert audit.event_metadata["new_state"] == "approved_for_paper_trading"


def test_auto_demotion_duplicate_breach_is_idempotent(db_session: Session) -> None:
    _seed_strategy(db_session, "breach", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )
    service = AutoDemotionService(session=db_session)

    first = service.run(actor="test")
    second = service.run(actor="test")

    assert len(first.demotions_executed) == 1
    assert second.demotions_executed == []
    assert any(candidate.status == "already_demoted" for candidate in second.candidates)
    assert db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_AUTO_DEMOTED").count() == 1


def test_auto_demotion_paper_strategy_demotes_to_research(db_session: Session) -> None:
    _seed_strategy(db_session, "paper_breach", state="approved_for_paper_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    result = AutoDemotionService(session=db_session).run(actor="test")
    governance = _get_governance(db_session, "paper_breach")

    assert result.demotions_executed[0]["new_governance_state"] == "approved_research"
    assert governance.current_state == "approved_research"


def test_auto_demotion_detects_sharpe_maintenance_breach(db_session: Session) -> None:
    _seed_strategy(
        db_session,
        "bad_sharpe",
        state="approved_for_live_trading",
        drawdown=0.03,
        sharpe=-0.25,
    )
    _seed_rule(
        db_session,
        rule_id="live_maintenance",
        from_status="approved_paper",
        to_status="approved_live",
        maintenance_min_sharpe=0.0,
    )
    OperatorSettingsRepository(db_session).update_current(
        {"max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    [candidate] = AutoDemotionService(session=db_session).scan()

    assert candidate.status == "breach"
    assert candidate.breached_metric == "sharpe"
    assert candidate.recommended_state == "approved_for_paper_trading"


def test_auto_demotion_detects_win_rate_maintenance_breach(db_session: Session) -> None:
    _seed_strategy(
        db_session,
        "bad_win_rate",
        state="approved_for_live_trading",
        drawdown=0.03,
        sharpe=1.0,
        winning_trades=2,
        losing_trades=18,
    )
    _seed_rule(
        db_session,
        rule_id="win_rate_maintenance",
        from_status="approved_paper",
        to_status="approved_live",
        maintenance_min_win_rate=0.30,
    )
    OperatorSettingsRepository(db_session).update_current(
        {"max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    [candidate] = AutoDemotionService(session=db_session).scan()

    assert candidate.status == "breach"
    assert candidate.breached_metric == "win_rate"
    assert candidate.breached_value == 0.10


def test_auto_demotion_insufficient_sample_does_not_demote(db_session: Session) -> None:
    _seed_strategy(
        db_session,
        "thin_sample",
        state="approved_for_live_trading",
        drawdown=0.25,
        trade_count=3,
    )
    _seed_rule(
        db_session,
        rule_id="sample_gate",
        from_status="approved_paper",
        to_status="approved_live",
        lookback_window_trades=10,
        maintenance_max_drawdown=0.12,
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    result = AutoDemotionService(session=db_session).run(actor="test")
    governance = _get_governance(db_session, "thin_sample")

    assert result.candidates[0].status == "skipped_insufficient_sample"
    assert result.demotions_executed == []
    assert governance.current_state == "approved_for_live_trading"


def test_auto_demotion_dry_run_reports_breach_without_transition(db_session: Session) -> None:
    _seed_strategy(db_session, "dry_run", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    result = AutoDemotionService(session=db_session).run(actor="test", dry_run=True)
    governance = _get_governance(db_session, "dry_run")

    assert result.dry_run is True
    assert result.candidates[0].status == "breach"
    assert result.demotions_executed == []
    assert governance.current_state == "approved_for_live_trading"


def test_demoted_live_strategy_no_longer_receives_live_allocation(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "alloc_breach", state="approved_for_live_trading", drawdown=0.25)
    now = datetime.now(UTC)
    db_session.add(
        CapitalAllocationPolicies(
            policy_id="live_policy",
            approval_status="approved_live",
            performance_tier=None,
            max_pct_of_capital=0.50,
            max_position_size_usd=None,
            max_drawdown_allowed=0.25,
            is_active=True,
            created_at=now,
        )
    )
    db_session.add(
        CapitalAllocationPolicies(
            policy_id="paper_policy",
            approval_status="approved_paper",
            performance_tier=None,
            max_pct_of_capital=0.25,
            max_position_size_usd=None,
            max_drawdown_allowed=0.25,
            is_active=True,
            created_at=now,
        )
    )
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_demote_on_breach": True,
            "auto_rebalance_enabled": True,
            "max_strategy_drawdown": 0.12,
        },
        updated_by="test",
    )

    AutoDemotionService(session=db_session).run(actor="test")
    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.after_allocation["alloc_breach"] == 0
    assert result.proposals[0].disabled is True


def test_auto_demotion_failed_transition_is_reported_without_state_change(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "transition_fail", state="approved_for_live_trading", drawdown=0.25)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.12},
        updated_by="test",
    )

    from autonomous_trading_platform.application.services.strategy_governance_service import (
        StrategyGovernanceService,
    )

    class FailingGovernanceService:
        def transition(self, **_kwargs):
            raise ValueError("transition denied")

    result = AutoDemotionService(
        session=db_session,
        governance_service=cast(StrategyGovernanceService, FailingGovernanceService()),
    ).run(actor="test")
    governance = _get_governance(db_session, "transition_fail")

    assert result.demotions_executed[0]["status"] == "failed"
    assert governance.current_state == "approved_for_live_trading"


def _seed_strategy(
    session: Session,
    strategy_id: str,
    *,
    state: str,
    drawdown: float,
    sharpe: float = -0.5,
    trade_count: int = 20,
    winning_trades: int = 5,
    losing_trades: int = 15,
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
            total_return=-0.10,
            sharpe_ratio=sharpe,
            max_drawdown=drawdown,
            trade_count=trade_count,
            winning_trade_count=winning_trades,
            losing_trade_count=losing_trades,
            volatility=0.20,
            metrics_json={"strategy_id": strategy_id},
        )
    )
    session.flush()


def _seed_rule(
    session: Session,
    *,
    rule_id: str,
    from_status: str,
    to_status: str,
    lookback_window_trades: int | None = None,
    maintenance_min_sharpe: float | None = None,
    maintenance_min_win_rate: float | None = None,
    maintenance_max_drawdown: float | None = None,
) -> None:
    session.add(
        PromotionRules(
            rule_id=rule_id,
            from_status=from_status,
            to_status=to_status,
            min_sharpe=None,
            max_drawdown=None,
            min_days_tested=None,
            min_trade_count=None,
            min_cagr=None,
            min_win_rate=None,
            lookback_window_bars=None,
            lookback_window_trades=lookback_window_trades,
            maintenance_min_sharpe=maintenance_min_sharpe,
            maintenance_min_win_rate=maintenance_min_win_rate,
            maintenance_max_drawdown=maintenance_max_drawdown,
            is_active=True,
            created_at=datetime.now(UTC),
            notes=None,
        )
    )
    session.flush()


def _get_governance(session: Session, strategy_id: str) -> StrategyGovernance:
    row = session.get(
        StrategyGovernance,
        {"strategy_id": strategy_id, "config_hash": f"{strategy_id}_hash"},
    )
    assert row is not None
    return cast(StrategyGovernance, row)
