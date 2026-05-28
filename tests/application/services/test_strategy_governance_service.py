from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.governance_exceptions import (
    MissingSourceRunError,
    PromotionCriteriaConfigurationError,
    PromotionRulesMissingError,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_strategy(
    session: Session,
    strategy_id: str,
    *,
    state: str = "approved_for_paper_trading",
    sharpe: float = 2.0,
    drawdown: float = 0.05,
    days: int = 60,
    trades: int = 30,
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
                "cagr": 0.12,
                "win_rate": 0.60,
            },
        )
    )
    session.flush()


def _seed_live_rule(
    session: Session,
    *,
    min_sharpe: float | None = 1.0,
    min_days_tested: int | None = 30,
    min_trade_count: int | None = 10,
    max_drawdown: float | None = 0.20,
    min_win_rate: float | None = None,
) -> None:
    session.add(
        PromotionRules(
            rule_id="paper_to_live",
            from_status="approved_paper",
            to_status="approved_live",
            min_sharpe=min_sharpe,
            max_drawdown=max_drawdown,
            min_days_tested=min_days_tested,
            min_trade_count=min_trade_count,
            min_cagr=None,
            min_win_rate=min_win_rate,
            is_active=True,
            created_at=datetime.now(UTC),
            notes="test",
        )
    )
    session.flush()


def _seed_paper_rule(
    session: Session,
    *,
    min_sharpe: float | None = 1.0,
    min_days_tested: int | None = 30,
    min_trade_count: int | None = 10,
    max_drawdown: float | None = 0.20,
) -> None:
    session.add(
        PromotionRules(
            rule_id="research_to_paper",
            from_status="approved_research",
            to_status="approved_paper",
            min_sharpe=min_sharpe,
            max_drawdown=max_drawdown,
            min_days_tested=min_days_tested,
            min_trade_count=min_trade_count,
            min_cagr=None,
            min_win_rate=None,
            is_active=True,
            created_at=datetime.now(UTC),
            notes="test",
        )
    )
    session.flush()


def _service(session: Session) -> StrategyGovernanceService:
    return StrategyGovernanceService(session=session)


# ---------------------------------------------------------------------------
# missing rule tests
# ---------------------------------------------------------------------------


def test_promotion_to_live_raises_when_no_rule_exists(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")

    with pytest.raises(PromotionRulesMissingError) as exc_info:
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="run_s1",
        )

    assert exc_info.value.from_state == "approved_paper"
    assert exc_info.value.to_state == "approved_live"


def test_promotion_to_live_missing_rule_emits_config_error_audit(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")

    with pytest.raises(PromotionRulesMissingError):
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="run_s1",
        )

    event = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="PROMOTION_RULES_CONFIGURATION_ERROR")
        .one()
    )
    assert event.event_metadata["error"] == "missing_rule"
    assert event.event_metadata["strategy_id"] == "s1"


# ---------------------------------------------------------------------------
# null required criteria tests
# ---------------------------------------------------------------------------


def test_promotion_to_live_raises_when_min_sharpe_is_null(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")
    _seed_live_rule(db_session, min_sharpe=None)

    with pytest.raises(PromotionCriteriaConfigurationError) as exc_info:
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="run_s1",
        )

    assert "min_sharpe" in exc_info.value.missing_fields


def test_promotion_to_live_raises_when_min_days_tested_is_null(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")
    _seed_live_rule(db_session, min_days_tested=None)

    with pytest.raises(PromotionCriteriaConfigurationError) as exc_info:
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="run_s1",
        )

    assert "min_days_tested" in exc_info.value.missing_fields


def test_promotion_to_live_raises_when_min_trade_count_is_null(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")
    _seed_live_rule(db_session, min_trade_count=None)

    with pytest.raises(PromotionCriteriaConfigurationError) as exc_info:
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="run_s1",
        )

    assert "min_trade_count" in exc_info.value.missing_fields


def test_null_required_criteria_emits_config_error_audit(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")
    _seed_live_rule(db_session, min_sharpe=None)

    with pytest.raises(PromotionCriteriaConfigurationError):
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="run_s1",
        )

    event = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="PROMOTION_RULES_CONFIGURATION_ERROR")
        .one()
    )
    assert event.event_metadata["error"] == "null_required_criteria"
    assert "min_sharpe" in event.event_metadata["missing_required_criteria"]
    assert event.event_metadata["rule_id"] == "paper_to_live"


# ---------------------------------------------------------------------------
# optional null criteria tests
# ---------------------------------------------------------------------------


def test_optional_null_criteria_do_not_block_promotion(db_session: Session) -> None:
    """min_cagr and min_win_rate are optional for paper->live; null is allowed."""
    _seed_strategy(db_session, "s1", sharpe=2.0, days=60, trades=20, drawdown=0.05)
    _seed_live_rule(
        db_session,
        min_sharpe=1.0,
        min_days_tested=30,
        min_trade_count=10,
        max_drawdown=0.20,
        min_win_rate=None,  # optional — null is fine
    )

    result = _service(db_session).transition(
        strategy_id="s1",
        to_state="approved_for_live_trading",
        reason="test",
        updated_by="admin",
        actor_role="admin",
        source_run_id="run_s1",
    )

    assert result.to_state == "approved_for_live_trading"


def test_optional_null_criteria_are_logged_in_audit(db_session: Session) -> None:
    _seed_strategy(db_session, "s1", sharpe=2.0, days=60, trades=20, drawdown=0.05)
    _seed_live_rule(
        db_session,
        min_sharpe=1.0,
        min_days_tested=30,
        min_trade_count=10,
        max_drawdown=None,  # optional — should appear as skipped in audit
        min_win_rate=None,
    )

    _service(db_session).transition(
        strategy_id="s1",
        to_state="approved_for_live_trading",
        reason="test",
        updated_by="admin",
        actor_role="admin",
        source_run_id="run_s1",
    )

    transition_audit = (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_GOVERNANCE_TRANSITIONED").one()
    )
    skipped = transition_audit.event_metadata.get("promotion_criteria_skipped_optional", [])
    assert "max_drawdown" in skipped


# ---------------------------------------------------------------------------
# full valid promotion test
# ---------------------------------------------------------------------------


def test_valid_fully_configured_rule_allows_promotion_when_metrics_pass(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "s1", sharpe=2.5, days=90, trades=50, drawdown=0.08)
    _seed_live_rule(
        db_session,
        min_sharpe=1.0,
        min_days_tested=30,
        min_trade_count=10,
        max_drawdown=0.20,
    )

    result = _service(db_session).transition(
        strategy_id="s1",
        to_state="approved_for_live_trading",
        reason="test",
        updated_by="admin",
        actor_role="admin",
        source_run_id="run_s1",
    )

    assert result.to_state == "approved_for_live_trading"
    transition_audit = (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_GOVERNANCE_TRANSITIONED").one()
    )
    evaluated = transition_audit.event_metadata.get("promotion_criteria_evaluated", [])
    assert any(c["criterion"] == "min_sharpe" and c["passed"] for c in evaluated)
    assert any(c["criterion"] == "min_days_tested" and c["passed"] for c in evaluated)
    assert any(c["criterion"] == "min_trade_count" and c["passed"] for c in evaluated)


def test_valid_fully_configured_rule_rejects_promotion_when_metrics_fail(
    db_session: Session,
) -> None:
    _seed_strategy(db_session, "s1", sharpe=0.3, days=15, trades=2, drawdown=0.08)
    _seed_live_rule(
        db_session,
        min_sharpe=1.0,
        min_days_tested=30,
        min_trade_count=10,
        max_drawdown=0.20,
    )

    with pytest.raises(ValueError, match="promotion criteria"):
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="run_s1",
        )


# ---------------------------------------------------------------------------
# paper promotion (lighter rules — missing rule is also blocked)
# ---------------------------------------------------------------------------


def test_promotion_to_paper_raises_when_no_rule_exists(db_session: Session) -> None:
    _seed_strategy(db_session, "s1", state="approved_research")

    with pytest.raises(PromotionRulesMissingError):
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_paper_trading",
            reason="test",
            updated_by="risk_manager",
            actor_role="risk_manager",
            source_run_id="run_s1",
        )


# ---------------------------------------------------------------------------
# FINDING-15: source_run_id required for capital-bearing promotions
# ---------------------------------------------------------------------------


def test_promotion_to_paper_without_source_run_id_raises(db_session: Session) -> None:
    _seed_strategy(db_session, "s1", state="approved_research")
    _seed_paper_rule(db_session)

    with pytest.raises(MissingSourceRunError) as exc_info:
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_paper_trading",
            reason="test",
            updated_by="risk_manager",
            actor_role="risk_manager",
            # source_run_id intentionally omitted
        )

    assert exc_info.value.strategy_id == "s1"
    assert "approved_for_paper_trading" in exc_info.value.target_state


def test_promotion_to_live_without_source_run_id_raises(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")
    _seed_live_rule(db_session)

    with pytest.raises(MissingSourceRunError) as exc_info:
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            # source_run_id intentionally omitted
        )

    assert exc_info.value.strategy_id == "s1"
    assert "approved_for_live_trading" in exc_info.value.target_state


def test_missing_source_run_emits_audit_event(db_session: Session) -> None:
    _seed_strategy(db_session, "s1")
    _seed_live_rule(db_session)

    with pytest.raises(MissingSourceRunError):
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
        )

    event = db_session.query(AuditLogRow).filter_by(event_type="PROMOTION_MISSING_SOURCE_RUN").one()
    assert event.event_metadata["strategy_id"] == "s1"
    assert event.event_metadata["error"] == "missing_source_run_id"
    assert event.event_metadata["fallback_allowed"] is False


def test_promotion_to_paper_with_source_run_id_uses_specified_run(db_session: Session) -> None:
    _seed_strategy(db_session, "s1", state="approved_research", sharpe=2.0, days=45, trades=20)
    _seed_paper_rule(db_session)

    result = _service(db_session).transition(
        strategy_id="s1",
        to_state="approved_for_paper_trading",
        reason="test",
        updated_by="risk_manager",
        actor_role="risk_manager",
        source_run_id="run_s1",
    )

    assert result.to_state == "approved_for_paper_trading"
    audit = (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_GOVERNANCE_TRANSITIONED").one()
    )
    assert audit.event_metadata["source_run_id"] == "run_s1"
    assert audit.event_metadata["metrics_source_type"] == "explicit_source_run"
    assert audit.event_metadata["fallback_allowed"] is False


def test_promotion_audit_records_source_run_and_fallback_policy(db_session: Session) -> None:
    _seed_strategy(db_session, "s1", sharpe=2.5, days=90, trades=50, drawdown=0.08)
    _seed_live_rule(db_session)

    _service(db_session).transition(
        strategy_id="s1",
        to_state="approved_for_live_trading",
        reason="test",
        updated_by="admin",
        actor_role="admin",
        source_run_id="run_s1",
    )

    audit = (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_GOVERNANCE_TRANSITIONED").one()
    )
    meta = audit.event_metadata
    assert meta["source_run_id"] == "run_s1"
    assert meta["metrics_source_type"] == "explicit_source_run"
    assert meta["fallback_allowed"] is False


def test_latest_run_fallback_not_used_for_capital_bearing_promotion(db_session: Session) -> None:
    """With source_run_id pointing to a non-existent run, capital-bearing promotion gets
    empty metrics and fails criteria rather than silently falling back to latest run."""
    _seed_strategy(db_session, "s1", sharpe=2.5, days=90, trades=50, drawdown=0.08)
    _seed_live_rule(db_session, min_sharpe=1.0, min_days_tested=30, min_trade_count=10)

    with pytest.raises(ValueError, match="promotion criteria"):
        _service(db_session).transition(
            strategy_id="s1",
            to_state="approved_for_live_trading",
            reason="test",
            updated_by="admin",
            actor_role="admin",
            source_run_id="nonexistent_run_id",  # valid source_run_id but no matching metrics
        )
