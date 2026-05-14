from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.auto_promotion_service import (
    AutoPromotionService,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


def test_auto_promotion_scanner_uses_promotion_rules_not_deprecated_settings(
    db_session: Session,
) -> None:
    _seed_rule(db_session)
    _seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_promote_enabled": False,
            "min_sharpe_for_promotion": 99.0,
            "min_paper_trading_period_days": 999,
        },
        updated_by="test",
    )

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is True
    assert candidate.rule_id == "research_to_paper"
    assert candidate.reasons == ["eligible"]


def test_auto_promotion_scanner_reports_ineligible_reasons(db_session: Session) -> None:
    _seed_rule(db_session)
    _seed_candidate(db_session, "bad", sharpe=0.2, drawdown=0.30, days=3, trades=1)

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is False
    assert candidate.status == "ineligible"
    assert any("min_sharpe" in reason for reason in candidate.reasons)
    assert any("max_drawdown" in reason for reason in candidate.reasons)
    assert any("min_days_tested" in reason for reason in candidate.reasons)
    assert any("min_trade_count" in reason for reason in candidate.reasons)


def test_auto_promotion_disabled_flag_prevents_promotion(db_session: Session) -> None:
    _seed_rule(db_session)
    _seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": False},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")
    governance = db_session.get(
        StrategyGovernance,
        {"strategy_id": "eligible", "config_hash": "eligible_hash"},
    )

    assert result.skipped_reason == "auto_promote_disabled"
    assert result.promotions_executed == []
    assert governance.current_state == "approved_research"
    assert (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_AUTO_PROMOTION_SKIPPED")
        .count()
        == 1
    )


def test_auto_promotion_enabled_promotes_only_eligible_and_emits_notification(
    db_session: Session,
) -> None:
    _seed_rule(db_session)
    _seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)
    _seed_candidate(db_session, "bad", sharpe=0.2, drawdown=0.30, days=3, trades=1)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True, "notify_strategy_promotion_events": True},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")
    eligible = db_session.get(
        StrategyGovernance,
        {"strategy_id": "eligible", "config_hash": "eligible_hash"},
    )
    bad = db_session.get(
        StrategyGovernance,
        {"strategy_id": "bad", "config_hash": "bad_hash"},
    )

    assert result.skipped_reason is None
    assert result.promotions_executed == [
        {
            "strategy_id": "eligible",
            "from_state": "approved_research",
            "to_state": "approved_for_paper_trading",
            "rule_id": "research_to_paper",
            "status": "promoted",
        }
    ]
    assert eligible.current_state == "approved_for_paper_trading"
    assert bad.current_state == "approved_research"
    assert (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_PROMOTION_EVENT").count() == 1
    )


def test_auto_promotion_reports_missing_rule(db_session: Session) -> None:
    _seed_candidate(
        db_session,
        "paper_candidate",
        state="approved_for_paper_trading",
        sharpe=3.0,
        days=60,
        trades=30,
    )

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is False
    assert candidate.status == "missing_rule"
    assert candidate.rule_id is None
    assert "No active PromotionRules row" in candidate.reasons[0]


def _seed_rule(session: Session) -> None:
    session.add(
        PromotionRules(
            rule_id="research_to_paper",
            from_status="approved_research",
            to_status="approved_paper",
            min_sharpe=1.5,
            max_drawdown=0.15,
            min_days_tested=30,
            min_trade_count=10,
            min_cagr=None,
            min_win_rate=None,
            is_active=True,
            created_at=datetime.now(UTC),
            notes="test",
        )
    )
    session.flush()


def _seed_candidate(
    session: Session,
    strategy_id: str,
    *,
    state: str = "approved_research",
    sharpe: float,
    drawdown: float = 0.05,
    days: int,
    trades: int,
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
            max_drawdown=drawdown,
            trade_count=trades,
            winning_trade_count=max(trades - 2, 0),
            losing_trade_count=min(2, trades),
            volatility=0.10,
            metrics_json={
                "strategy_id": strategy_id,
                "days_tested": days,
                "cagr": 0.10,
                "win_rate": 0.60,
            },
        )
    )
    session.flush()
