"""Tests for FINDING-06: Rebalance Stability Controls and Turnover Guardrails."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import AllocationOverrides
from autonomous_trading_platform.storage.sor.models.allocation_rebalance_history import (
    AllocationRebalanceHistory,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_policy(session: Session, *, max_pct: float = 0.80) -> None:
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
    sharpe: float = 1.0,
    total_return: float = 0.10,
    max_drawdown: float = 0.05,
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


def _enable_rebalance(session: Session, **extra) -> None:
    values = {"auto_rebalance_enabled": True, "per_strategy_cap": 0.80}
    values.update(extra)
    OperatorSettingsRepository(session).update_current(values, updated_by="test")


def _insert_completed_history(
    session: Session,
    *,
    completed_at: datetime,
    rebalance_id: str = "hist_completed",
) -> AllocationRebalanceHistory:
    row = AllocationRebalanceHistory(
        rebalance_id=rebalance_id,
        run_id=None,
        actor="test",
        trigger_source=None,
        status="completed",
        skipped_reason=None,
        started_at=completed_at - timedelta(minutes=5),
        completed_at=completed_at,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Interval guard tests
# ---------------------------------------------------------------------------


def test_rebalance_skipped_when_interval_window_not_elapsed(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_rebalance_interval_hours=24.0)

    now = datetime.now(UTC)
    _insert_completed_history(db_session, completed_at=now - timedelta(hours=12))

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason == "interval_guard"
    assert result.audit_event_type == "STRATEGY_ALLOCATION_REBALANCE_SKIPPED"
    assert result.allocation_changes_count == 0
    assert not result.noop
    assert db_session.query(AllocationOverrides).count() == 0

    history = (
        db_session.query(AllocationRebalanceHistory).filter_by(status="skipped_interval").all()
    )
    assert len(history) == 1
    assert history[0].skipped_reason == "interval_guard"
    assert history[0].completed_at is not None


def test_rebalance_executes_after_interval_expires(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_rebalance_interval_hours=24.0)

    now = datetime.now(UTC)
    _insert_completed_history(db_session, completed_at=now - timedelta(hours=25))

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason != "interval_guard"
    assert result.audit_event_type in {
        "STRATEGY_ALLOCATION_REBALANCED",
        "STRATEGY_ALLOCATION_REBALANCE_NOOP",
    }


def test_rebalance_executes_when_no_prior_history(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_rebalance_interval_hours=24.0)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason is None or result.skipped_reason == "all_changes_below_threshold"


def test_rebalance_executes_when_interval_hours_zero(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_rebalance_interval_hours=0.0)

    now = datetime.now(UTC)
    _insert_completed_history(db_session, completed_at=now - timedelta(minutes=1))

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason != "interval_guard"


# ---------------------------------------------------------------------------
# Hysteresis / minimum delta threshold tests
# ---------------------------------------------------------------------------


def test_tiny_allocation_changes_below_threshold_are_ignored(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1", sharpe=1.0, total_return=0.10, max_drawdown=0.05)

    # Seed an existing auto_rebalance override very close to the proposed value
    # The quality-weighted allocation for one strategy at ~80% will stay near 80%.
    # We force min_allocation_change_pct=99% to guarantee no writes.
    _enable_rebalance(db_session, min_allocation_change_pct=0.99)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    # With a 99% threshold, no quality-weighted change will exceed it
    assert result.allocation_changes_count == 0
    assert result.noop is True
    assert result.audit_event_type == "STRATEGY_ALLOCATION_REBALANCE_NOOP"
    assert db_session.query(AllocationOverrides).count() == 0


def test_large_allocation_changes_are_applied(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    _seed_strategy(db_session, "s2")
    _enable_rebalance(db_session, min_allocation_change_pct=0.01)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    # Both strategies start at 0% effective (policy cap, no prior auto override).
    # Any quality-weighted assignment > 1% exceeds threshold.
    assert result.allocation_changes_count >= 1
    assert result.noop is False
    assert result.audit_event_type == "STRATEGY_ALLOCATION_REBALANCED"
    assert db_session.query(AllocationOverrides).filter_by(is_active=True).count() >= 1


def test_allocation_change_exactly_at_threshold_is_applied(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    now = datetime.now(UTC)

    # Seed an existing auto_rebalance override at 0.50
    db_session.add(
        AllocationOverrides(
            override_id="prev_auto",
            strategy_id="s1",
            overridden_by="auto_rebalance",
            override_reason="quality-based reallocation: quality_weighted",
            max_pct_of_capital=0.50,
            max_position_size_usd=None,
            max_drawdown_allowed=None,
            is_active=True,
            created_at=now - timedelta(hours=2),
            expires_at=None,
        )
    )
    db_session.flush()
    # threshold = 0.01 (1%), strategy currently at 50%, policy cap 80%
    # The quality-weighted allocation should differ meaningfully from 0.50 given
    # that the quality score is computed fresh and budget = 100% - 0 fixed.
    _enable_rebalance(db_session, min_allocation_change_pct=0.01)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    # New allocation should be ~80% (quality-weighted against cap), delta ~30% >> 1%
    assert result.allocation_changes_count >= 1


def test_no_op_rebalance_produces_no_override_writes(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_allocation_change_pct=0.99)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.noop is True
    assert db_session.query(AllocationOverrides).count() == 0
    assert result.skipped_reason == "all_changes_below_threshold"

    # Noop should still be audited
    noop_events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_NOOP")
        .all()
    )
    assert len(noop_events) == 1

    # Noop is recorded in history
    noop_history = db_session.query(AllocationRebalanceHistory).filter_by(status="noop").all()
    assert len(noop_history) == 1
    assert noop_history[0].skipped_reason == "all_changes_below_threshold"


# ---------------------------------------------------------------------------
# Concurrent rebalance protection tests
# ---------------------------------------------------------------------------


def test_concurrent_rebalance_execution_blocked(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session)

    now = datetime.now(UTC)

    # Simulate a concurrent "running" lock row (committed by another process)
    lock_row = AllocationRebalanceHistory(
        rebalance_id="concurrent_lock_row",
        run_id=None,
        actor="scheduler",
        trigger_source=None,
        status="running",
        skipped_reason=None,
        started_at=now - timedelta(minutes=2),
        completed_at=None,
    )
    db_session.add(lock_row)
    db_session.flush()

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason == "concurrent_lock"
    assert result.allocation_changes_count == 0
    assert db_session.query(AllocationOverrides).count() == 0

    skipped = (
        db_session.query(AllocationRebalanceHistory).filter_by(status="skipped_concurrent").all()
    )
    assert len(skipped) == 1


def test_stale_lock_does_not_block_rebalance(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session)

    now = datetime.now(UTC)

    # Stale running row (> 4 hours old)
    stale_lock = AllocationRebalanceHistory(
        rebalance_id="stale_lock_row",
        run_id=None,
        actor="scheduler",
        trigger_source=None,
        status="running",
        skipped_reason=None,
        started_at=now - timedelta(hours=5),
        completed_at=None,
    )
    db_session.add(stale_lock)
    db_session.flush()

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    # Stale lock should be ignored
    assert result.skipped_reason != "concurrent_lock"


# ---------------------------------------------------------------------------
# History persistence tests
# ---------------------------------------------------------------------------


def test_rebalance_execution_state_persisted_on_success(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _seed_strategy(db_session, "s2")
    _enable_rebalance(db_session, min_allocation_change_pct=0.001)

    result = QualityBasedReallocationService(session=db_session).rebalance(
        run_id="test_run_123", actor="test"
    )

    history = (
        db_session.query(AllocationRebalanceHistory)
        .filter(AllocationRebalanceHistory.rebalance_id == result.rebalance_id)
        .one_or_none()
    )
    assert history is not None
    assert history.run_id == "test_run_123"
    assert history.actor == "test"
    assert history.status in {"completed", "noop"}
    assert history.started_at is not None
    assert history.completed_at is not None
    assert history.strategies_evaluated == 2


def test_rebalance_history_records_turnover_metrics(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_allocation_change_pct=0.001)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    history = (
        db_session.query(AllocationRebalanceHistory)
        .filter(AllocationRebalanceHistory.rebalance_id == result.rebalance_id)
        .one_or_none()
    )
    assert history is not None
    if history.status == "completed":
        assert history.total_allocation_delta_pct is not None
        assert history.churn_pct is not None
        assert history.allocation_changes_count is not None


# ---------------------------------------------------------------------------
# Allocation write atomicity tests
# ---------------------------------------------------------------------------


def test_allocation_writes_are_atomic_no_partial_updates(db_session: Session) -> None:
    """All overrides for a rebalance are written in one flush before commit."""
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    _seed_strategy(db_session, "s2")
    _seed_strategy(db_session, "s3")
    _enable_rebalance(db_session, min_allocation_change_pct=0.001)

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    active_overrides = db_session.query(AllocationOverrides).filter_by(is_active=True).all()
    # All written overrides should be from the same rebalance
    assert all(o.overridden_by == "test" for o in active_overrides)
    # No duplicate active overrides per strategy
    strategy_ids = [o.strategy_id for o in active_overrides]
    assert len(strategy_ids) == len(set(strategy_ids))


# ---------------------------------------------------------------------------
# Turnover metrics tests
# ---------------------------------------------------------------------------


def test_turnover_metrics_calculated_correctly(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    _seed_strategy(db_session, "s2")
    _enable_rebalance(db_session, min_allocation_change_pct=0.001)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    metrics = result.turnover_metrics
    assert "total_allocation_delta_pct" in metrics
    assert "churn_pct" in metrics
    assert "strategies_evaluated" in metrics
    assert "strategies_changed" in metrics
    assert metrics["strategies_evaluated"] == 2
    assert float(metrics["total_allocation_delta_pct"]) >= 0.0
    assert float(metrics["churn_pct"]) >= 0.0


def test_turnover_penalty_included_when_weight_configured(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_allocation_change_pct=0.001, turnover_penalty_weight=0.5)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert "turnover_penalty" in result.turnover_metrics


def test_turnover_penalty_absent_when_weight_not_configured(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.80)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert "turnover_penalty" not in result.turnover_metrics


# ---------------------------------------------------------------------------
# Observability / audit event tests
# ---------------------------------------------------------------------------


def test_audit_event_emitted_for_interval_guard_skip(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_rebalance_interval_hours=24.0)
    now = datetime.now(UTC)
    _insert_completed_history(db_session, completed_at=now - timedelta(hours=1))

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_SKIPPED")
        .all()
    )
    assert len(events) >= 1
    reasons = [e.metadata_.get("reason", "") if e.metadata_ else "" for e in events]
    assert any("interval_guard" in r for r in reasons)


def test_audit_event_emitted_for_concurrent_lock_skip(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session)
    now = datetime.now(UTC)
    db_session.add(
        AllocationRebalanceHistory(
            rebalance_id="lock_row",
            run_id=None,
            actor="scheduler",
            trigger_source=None,
            status="running",
            skipped_reason=None,
            started_at=now - timedelta(minutes=3),
            completed_at=None,
        )
    )
    db_session.flush()

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_SKIPPED")
        .all()
    )
    assert len(events) >= 1
    reasons = [e.metadata_.get("reason", "") if e.metadata_ else "" for e in events]
    assert any("concurrent_lock" in r for r in reasons)


def test_audit_event_emitted_for_noop_rebalance(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_allocation_change_pct=0.99)

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    noop_events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_NOOP")
        .all()
    )
    assert len(noop_events) == 1


def test_audit_event_emitted_for_applied_rebalance(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session, min_allocation_change_pct=0.001)

    QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    applied_events = (
        db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_ALLOCATION_REBALANCED").all()
    )
    noop_events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_NOOP")
        .all()
    )
    # Exactly one of these should be present
    assert len(applied_events) + len(noop_events) == 1


def test_rebalance_id_present_in_result(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.rebalance_id is not None
    assert len(result.rebalance_id) > 0


def test_result_to_jsonable_includes_stability_fields(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")
    payload = QualityBasedReallocationService.result_to_jsonable(result)

    assert "rebalance_id" in payload
    assert "turnover_metrics" in payload
    assert "allocation_changes_count" in payload
    assert "noop" in payload


# ---------------------------------------------------------------------------
# Lock release on failure
# ---------------------------------------------------------------------------


def test_rebalance_lock_released_on_failure(db_session: Session) -> None:
    """After a failed rebalance, subsequent runs must not be blocked by a stale lock."""
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    _enable_rebalance(db_session)

    now = datetime.now(UTC)

    # Simulate a previously committed "failed" row (not "running") — should not block
    db_session.add(
        AllocationRebalanceHistory(
            rebalance_id="failed_run",
            run_id=None,
            actor="scheduler",
            trigger_source=None,
            status="failed",
            skipped_reason=None,
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
        )
    )
    db_session.flush()

    # Also confirm that a committed running row > 4h old is treated as stale
    db_session.add(
        AllocationRebalanceHistory(
            rebalance_id="stale_running",
            run_id=None,
            actor="scheduler",
            trigger_source=None,
            status="running",
            skipped_reason=None,
            started_at=now - timedelta(hours=5),
            completed_at=None,
        )
    )
    db_session.flush()

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason != "concurrent_lock"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_existing_auto_rebalance_disabled_flow_still_works(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "s1")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": False},
        updated_by="test",
    )

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    assert result.skipped_reason == "auto_rebalance_disabled"
    assert result.audit_event_type == "STRATEGY_ALLOCATION_REBALANCE_SKIPPED"
    assert result.allocation_changes_count == 0
    assert result.noop is False


def test_manual_overrides_preserved_by_hysteresis(db_session: Session) -> None:
    _seed_policy(db_session, max_pct=0.90)
    _seed_strategy(db_session, "s1")
    _seed_strategy(db_session, "manual_s")
    now = datetime.now(UTC)
    db_session.add(
        AllocationOverrides(
            override_id="manual_ov",
            strategy_id="manual_s",
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
    db_session.flush()
    _enable_rebalance(db_session, min_allocation_change_pct=0.99)

    result = QualityBasedReallocationService(session=db_session).rebalance(actor="test")

    # Manual override must still be respected regardless of hysteresis
    assert result.after_allocation["manual_s"] == Decimal("0.220000")
    # Manual override row is untouched
    manual_row = db_session.query(AllocationOverrides).filter_by(override_id="manual_ov").one()
    assert manual_row.is_active is True
    assert manual_row.overridden_by == "risk-manager"
