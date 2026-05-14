from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.auto_demotion_service import (
    AutoDemotionService,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
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
    assert db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_AUTO_DEMOTED").count() == 1


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


def _seed_strategy(
    session: Session,
    strategy_id: str,
    *,
    state: str,
    drawdown: float,
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


def _get_governance(session: Session, strategy_id: str) -> StrategyGovernance:
    row = session.get(
        StrategyGovernance,
        {"strategy_id": strategy_id, "config_hash": f"{strategy_id}_hash"},
    )
    assert row is not None
    return cast(StrategyGovernance, row)
