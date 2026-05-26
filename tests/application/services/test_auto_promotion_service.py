from __future__ import annotations

import uuid
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


def _make_source_run_id(strategy_id: str) -> uuid.UUID:
    """Deterministic UUID from strategy_id for test governance records."""
    return uuid.uuid5(uuid.NAMESPACE_OID, strategy_id)


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


def _seed_live_rule(
    session: Session,
    *,
    min_sharpe: float | None = 1.0,
    min_days_tested: int | None = 30,
    min_trade_count: int | None = 10,
) -> None:
    session.add(
        PromotionRules(
            rule_id="paper_to_live",
            from_status="approved_paper",
            to_status="approved_live",
            min_sharpe=min_sharpe,
            max_drawdown=0.20,
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


# ---------------------------------------------------------------------------
# FINDING-07: null required criteria hardening
# ---------------------------------------------------------------------------


def test_auto_promotion_scan_returns_invalid_rule_config_when_required_criteria_null(
    db_session: Session,
) -> None:
    """AutoPromotionService must not treat a null required criterion as 'no constraint'."""
    _seed_live_rule(db_session, min_sharpe=None)
    _seed_candidate(
        db_session,
        "paper_eligible",
        state="approved_for_paper_trading",
        sharpe=3.0,
        days=90,
        trades=50,
    )

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is False
    assert candidate.status == "invalid_rule_config"
    assert "min_sharpe" in candidate.missing_required_criteria


def test_auto_promotion_does_not_promote_when_required_criteria_null(
    db_session: Session,
) -> None:
    _seed_live_rule(db_session, min_days_tested=None)
    _seed_candidate(
        db_session,
        "paper_eligible",
        state="approved_for_paper_trading",
        sharpe=3.0,
        days=90,
        trades=50,
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")

    assert result.promotions_executed == []
    assert result.candidates[0].status == "invalid_rule_config"


def test_auto_promotion_emits_audit_for_invalid_rule_config(db_session: Session) -> None:
    _seed_live_rule(db_session, min_trade_count=None)
    _seed_candidate(
        db_session,
        "paper_eligible",
        state="approved_for_paper_trading",
        sharpe=3.0,
        days=90,
        trades=50,
    )

    AutoPromotionService(session=db_session).scan()

    event = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="PROMOTION_RULES_CONFIGURATION_ERROR")
        .one()
    )
    assert "min_trade_count" in event.event_metadata["missing_required_criteria"]
    assert event.event_metadata["source"] == "auto_promotion_scan"


def test_auto_promotion_skipped_optional_criteria_appear_in_result(
    db_session: Session,
) -> None:
    """Optional null criteria (min_cagr, min_win_rate) should be reflected as skipped."""
    _seed_rule(db_session)
    _seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is True
    # min_cagr and min_win_rate are null in _seed_rule → appear as skipped
    assert "min_cagr" in candidate.skipped_criteria or "min_win_rate" in candidate.skipped_criteria


def test_active_rule_payloads_expose_validity_and_missing_criteria(
    db_session: Session,
) -> None:
    _seed_live_rule(db_session, min_sharpe=None)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": False},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")

    live_rule_payload = next(
        (r for r in result.promotion_rules_used if r["to_status"] == "approved_live"), None
    )
    assert live_rule_payload is not None
    assert live_rule_payload["is_valid"] is False
    assert "min_sharpe" in live_rule_payload["missing_required_criteria"]
    assert "min_sharpe" in live_rule_payload["required_criteria"]


def test_fully_configured_live_rule_allows_auto_promotion(db_session: Session) -> None:
    _seed_live_rule(db_session, min_sharpe=1.0, min_days_tested=30, min_trade_count=10)
    _seed_candidate(
        db_session,
        "paper_pass",
        state="approved_for_paper_trading",
        sharpe=2.5,
        days=90,
        trades=50,
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")

    assert result.promotions_executed[0]["status"] == "promoted"
    assert result.promotions_executed[0]["strategy_id"] == "paper_pass"


# ---------------------------------------------------------------------------
# FINDING-15: source_run_id required for capital-bearing auto-promotions
# ---------------------------------------------------------------------------


def test_auto_promotion_scan_skips_research_candidate_without_source_run_id(
    db_session: Session,
) -> None:
    """Capital-bearing research→paper auto-promotion skips strategies without source_run_id."""
    _seed_rule(db_session)
    _seed_candidate(
        db_session,
        "no_source",
        sharpe=2.0,
        days=45,
        trades=20,
        include_source_run=False,
    )

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is False
    assert candidate.status == "missing_source_run"
    assert candidate.source_run_id is None
    assert candidate.fallback_allowed is False
    assert any("source_run_id" in r for r in candidate.reasons)


def test_auto_promotion_scan_skips_paper_candidate_without_source_run_id(
    db_session: Session,
) -> None:
    """Capital-bearing paper→live auto-promotion skips strategies without source_run_id."""
    _seed_live_rule(db_session, min_sharpe=1.0, min_days_tested=30, min_trade_count=10)
    _seed_candidate(
        db_session,
        "no_source_paper",
        state="approved_for_paper_trading",
        sharpe=3.0,
        days=90,
        trades=50,
        include_source_run=False,
    )

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is False
    assert candidate.status == "missing_source_run"
    assert candidate.fallback_allowed is False


def test_auto_promotion_does_not_execute_for_missing_source_run(db_session: Session) -> None:
    _seed_rule(db_session)
    _seed_candidate(
        db_session,
        "no_source",
        sharpe=2.0,
        days=45,
        trades=20,
        include_source_run=False,
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")

    assert result.promotions_executed == []
    assert result.candidates[0].status == "missing_source_run"


def test_auto_promotion_emits_audit_for_missing_source_run(db_session: Session) -> None:
    _seed_rule(db_session)
    _seed_candidate(
        db_session,
        "no_source",
        sharpe=2.0,
        days=45,
        trades=20,
        include_source_run=False,
    )

    AutoPromotionService(session=db_session).scan()

    event = db_session.query(AuditLogRow).filter_by(event_type="PROMOTION_MISSING_SOURCE_RUN").one()
    assert event.event_metadata["strategy_id"] == "no_source"
    assert event.event_metadata["error"] == "missing_source_run_id"
    assert event.event_metadata["fallback_allowed"] is False
    assert event.event_metadata["source"] == "auto_promotion_scan"


def test_auto_promotion_with_source_run_id_includes_it_in_audit_event(
    db_session: Session,
) -> None:
    _seed_rule(db_session)
    _seed_candidate(db_session, "has_source", sharpe=2.0, days=45, trades=20)
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")

    audit = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_AUTO_PROMOTION_COMPLETED")
        .one()
    )
    candidates = audit.event_metadata.get("candidate_strategies", [])
    promoted_candidate = next(c for c in candidates if c["strategy_id"] == "has_source")
    assert promoted_candidate["source_run_id"] == str(_make_source_run_id("has_source"))
    assert promoted_candidate["metrics_source_type"] == "explicit_source_run"
    assert promoted_candidate["fallback_allowed"] is False
    assert result.promotions_executed[0]["status"] == "promoted"


def test_auto_promotion_with_source_run_uses_explicit_run_metrics(db_session: Session) -> None:
    """The eligible candidate has source_run_id set; its own run's metrics are used."""
    _seed_rule(db_session)
    _seed_candidate(db_session, "eligible", sharpe=2.0, days=45, trades=20)

    [candidate] = AutoPromotionService(session=db_session).scan()

    assert candidate.eligible is True
    assert candidate.source_run_id == str(_make_source_run_id("eligible"))
    assert candidate.metrics_source_type == "explicit_source_run"


def test_auto_promotion_missing_source_run_status_does_not_attempt_promotion(
    db_session: Session,
) -> None:
    """Strategies with missing_source_run status must not be promoted even if enabled."""
    _seed_live_rule(db_session, min_sharpe=0.1, min_days_tested=1, min_trade_count=1)
    _seed_candidate(
        db_session,
        "no_src_paper",
        state="approved_for_paper_trading",
        sharpe=9.9,
        days=365,
        trades=1000,
        include_source_run=False,
    )
    OperatorSettingsRepository(db_session).update_current(
        {"auto_promote_enabled": True},
        updated_by="test",
    )

    result = AutoPromotionService(session=db_session).run(actor="test")

    assert result.promotions_executed == []
    governance_row = db_session.get(
        StrategyGovernance,
        {"strategy_id": "no_src_paper", "config_hash": "no_src_paper_hash"},
    )
    assert governance_row.current_state == "approved_for_paper_trading"


def _seed_candidate(
    session: Session,
    strategy_id: str,
    *,
    state: str = "approved_research",
    sharpe: float,
    drawdown: float = 0.05,
    days: int,
    trades: int,
    include_source_run: bool = True,
) -> None:
    now = datetime.now(UTC)
    # Use a deterministic UUID so governance.source_run_id matches MetricsSummary.run_id
    source_run_id = _make_source_run_id(strategy_id) if include_source_run else None
    run_id = str(source_run_id) if source_run_id else f"run_{strategy_id}"
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
            source_run_id=source_run_id,
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
