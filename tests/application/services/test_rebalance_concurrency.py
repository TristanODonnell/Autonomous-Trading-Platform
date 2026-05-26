from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    _REBALANCE_ADVISORY_LOCK_KEY,
    QualityBasedReallocationService,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.allocation_rebalance_history import (
    AllocationRebalanceHistory,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
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


def _running_row(
    rebalance_id: str = "running-lock-1", run_id: str | None = None
) -> AllocationRebalanceHistory:
    return AllocationRebalanceHistory(
        rebalance_id=rebalance_id,
        run_id=run_id,
        actor="auto_rebalance",
        trigger_source="scheduler",
        status="running",
        skipped_reason=None,
        started_at=datetime.now(UTC),
        completed_at=None,
    )


def _make_service(session: Session) -> QualityBasedReallocationService:
    return QualityBasedReallocationService(session=session)


# ---------------------------------------------------------------------------
# Lock acquisition
# ---------------------------------------------------------------------------


def test_first_rebalance_acquires_lock_and_succeeds(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    result = _make_service(db_session).rebalance(actor="test")

    assert result.lock_acquired is True
    assert result.skipped_reason is None or result.skipped_reason == "all_changes_below_threshold"


def test_advisory_lock_key_is_stable_positive_int64() -> None:
    assert isinstance(_REBALANCE_ADVISORY_LOCK_KEY, int)
    assert 0 < _REBALANCE_ADVISORY_LOCK_KEY <= 0x7FFFFFFFFFFFFFFF


def test_try_acquire_advisory_lock_returns_true_for_sqlite(db_session: Session) -> None:
    # SQLite dialect → advisory lock function transparently returns True.
    acquired = QualityBasedReallocationService._try_acquire_advisory_lock(db_session)
    assert acquired is True


# ---------------------------------------------------------------------------
# Concurrent lock detection (row-based guard; primary for SQLite tests)
# ---------------------------------------------------------------------------


def test_concurrent_rebalance_is_skipped_when_running_row_exists(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )
    db_session.add(_running_row())
    db_session.flush()

    result = _make_service(db_session).rebalance(actor="test")

    assert result.skipped_reason == "concurrent_lock"
    assert result.lock_acquired is False


def test_concurrent_rebalance_does_not_mutate_overrides(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )
    db_session.add(_running_row())
    db_session.flush()

    _make_service(db_session).rebalance(actor="test")

    active_overrides = db_session.scalars(
        select(AllocationOverrides).where(AllocationOverrides.is_active.is_(True))
    ).all()
    assert active_overrides == []


def test_only_one_active_override_set_after_concurrent_attempts(db_session: Session) -> None:
    """Simulate A wins, then B sees A's running row and skips.
    Final state: A's overrides are the only active ones."""
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    _seed_strategy(db_session, "beta")
    # min_rebalance_interval_hours=0 disables the interval guard so both calls
    # reach the concurrent lock check within the same test.
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "min_rebalance_interval_hours": 0.0},
        updated_by="test",
    )

    # Rebalance A runs first.
    result_a = _make_service(db_session).rebalance(actor="test", run_id="run-A")
    assert result_a.skipped_reason != "concurrent_lock"

    # Insert a fresh running row to simulate another in-flight process.
    db_session.add(_running_row(rebalance_id="concurrent-B"))
    db_session.flush()

    result_b = _make_service(db_session).rebalance(actor="test", run_id="run-B")
    assert result_b.skipped_reason == "concurrent_lock"

    # Only the overrides from run A exist (run B wrote nothing).
    active_overrides = db_session.scalars(
        select(AllocationOverrides).where(AllocationOverrides.is_active.is_(True))
    ).all()
    for override in active_overrides:
        assert "run-B" not in override.override_reason


def test_stale_running_row_does_not_block_rebalance(db_session: Session) -> None:
    """A running row older than 4 hours is treated as stale and does not block."""
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )
    stale = AllocationRebalanceHistory(
        rebalance_id="stale-lock",
        run_id=None,
        actor="auto_rebalance",
        trigger_source=None,
        status="running",
        skipped_reason=None,
        started_at=datetime.now(UTC) - timedelta(hours=5),
        completed_at=None,
    )
    db_session.add(stale)
    db_session.flush()

    result = _make_service(db_session).rebalance(actor="test")

    # Stale lock is ignored; rebalance proceeds.
    assert result.skipped_reason != "concurrent_lock"


# ---------------------------------------------------------------------------
# Advisory lock mock path
# ---------------------------------------------------------------------------


def test_advisory_lock_unavailable_skips_rebalance(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with patch.object(
        QualityBasedReallocationService,
        "_try_acquire_advisory_lock",
        return_value=False,
    ):
        result = _make_service(db_session).rebalance(actor="test")

    assert result.skipped_reason == "concurrent_lock"
    assert result.lock_acquired is False


def test_advisory_lock_unavailable_emits_skipped_concurrent_audit(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with patch.object(
        QualityBasedReallocationService,
        "_try_acquire_advisory_lock",
        return_value=False,
    ):
        _make_service(db_session).rebalance(actor="test")

    skipped_events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_SKIPPED")
        .all()
    )
    assert len(skipped_events) == 1
    metadata = skipped_events[0].metadata_
    assert metadata is not None
    assert metadata.get("skipped_reason") == "concurrent_lock"


def test_advisory_lock_unavailable_does_not_write_overrides(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with patch.object(
        QualityBasedReallocationService,
        "_try_acquire_advisory_lock",
        return_value=False,
    ):
        _make_service(db_session).rebalance(actor="test")

    assert db_session.query(AllocationOverrides).count() == 0


# ---------------------------------------------------------------------------
# Failure handling and rollback
# ---------------------------------------------------------------------------


def test_failed_rebalance_rolls_back_override_mutations(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with (
        patch.object(
            QualityBasedReallocationService,
            "_compute_proposals",
            side_effect=RuntimeError("injected failure"),
        ),
        pytest.raises(RuntimeError, match="injected failure"),
    ):
        _make_service(db_session).rebalance(actor="test")

    # No active overrides from the failed run.
    assert db_session.query(AllocationOverrides).filter_by(is_active=True).count() == 0


def test_failed_rebalance_writes_failed_history_row(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with (
        patch.object(
            QualityBasedReallocationService,
            "_compute_proposals",
            side_effect=RuntimeError("injected failure"),
        ),
        pytest.raises(RuntimeError),
    ):
        _make_service(db_session).rebalance(actor="test", run_id="failing-run")

    failed_rows = db_session.query(AllocationRebalanceHistory).filter_by(status="failed").all()
    assert len(failed_rows) == 1
    assert failed_rows[0].run_id == "failing-run"


def test_failed_rebalance_emits_failure_audit_event(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with (
        patch.object(
            QualityBasedReallocationService,
            "_compute_proposals",
            side_effect=RuntimeError("injected failure"),
        ),
        pytest.raises(RuntimeError),
    ):
        _make_service(db_session).rebalance(actor="test")

    failure_events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_FAILED")
        .all()
    )
    assert len(failure_events) == 1


def test_lock_releases_after_failure_so_next_run_can_proceed(db_session: Session) -> None:
    """After a failed run the lock row is rolled back, so a subsequent run is not blocked."""
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with (
        patch.object(
            QualityBasedReallocationService,
            "_compute_proposals",
            side_effect=RuntimeError("injected failure"),
        ),
        pytest.raises(RuntimeError),
    ):
        _make_service(db_session).rebalance(actor="test", run_id="fail-1")

    # The failed run rolled back its "running" row; the next run should not be blocked.
    result = _make_service(db_session).rebalance(actor="test", run_id="retry-after-fail")
    assert result.skipped_reason != "concurrent_lock"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_retry_with_same_rebalance_run_id_returns_retry_detected(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )
    fixed_id = "idempotent-rebalance-001"

    # First run succeeds.
    result_first = _make_service(db_session).rebalance(actor="test", rebalance_run_id=fixed_id)
    assert result_first.skipped_reason != "retry_detected"

    # Second run with the same ID → retry detected, no new overrides.
    result_retry = _make_service(db_session).rebalance(actor="test", rebalance_run_id=fixed_id)
    assert result_retry.skipped_reason == "retry_detected"


def test_retry_does_not_create_duplicate_active_overrides(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    _seed_strategy(db_session, "beta")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )
    fixed_id = "idempotent-rebalance-002"

    _make_service(db_session).rebalance(actor="test", rebalance_run_id=fixed_id)
    count_after_first = db_session.query(AllocationOverrides).filter_by(is_active=True).count()

    _make_service(db_session).rebalance(actor="test", rebalance_run_id=fixed_id)
    count_after_retry = db_session.query(AllocationOverrides).filter_by(is_active=True).count()

    assert count_after_first == count_after_retry


def test_retry_emits_retry_detected_audit_event(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )
    fixed_id = "idempotent-rebalance-003"

    _make_service(db_session).rebalance(actor="test", rebalance_run_id=fixed_id)
    _make_service(db_session).rebalance(actor="test", rebalance_run_id=fixed_id)

    retry_events = (
        db_session.query(AuditLogRow)
        .filter_by(event_type="STRATEGY_ALLOCATION_REBALANCE_RETRY_DETECTED")
        .all()
    )
    assert len(retry_events) == 1


def test_unique_rebalance_run_ids_do_not_interfere(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True, "min_rebalance_interval_hours": 0.0},
        updated_by="test",
    )

    result_a = _make_service(db_session).rebalance(actor="test", rebalance_run_id="run-unique-A")
    result_b = _make_service(db_session).rebalance(actor="test", rebalance_run_id="run-unique-B")

    assert result_a.skipped_reason != "retry_detected"
    assert result_b.skipped_reason != "retry_detected"


# ---------------------------------------------------------------------------
# Scheduler and manual entrypoint parity
# ---------------------------------------------------------------------------


def test_scheduler_path_uses_locked_service(db_session: Session) -> None:
    """run_allocation_rebalance_cycle uses QualityBasedReallocationService.rebalance,
    which includes the lock guard."""
    from unittest.mock import patch as _patch

    from autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle import (
        run_allocation_rebalance_cycle,
    )

    captured: list[dict] = []

    original_rebalance = QualityBasedReallocationService.rebalance

    def spy_rebalance(self, **kwargs):
        result = original_rebalance(self, **kwargs)
        captured.append({"trigger_source": kwargs.get("trigger_source")})
        return result

    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    with (
        _patch(
            "autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle.get_session",
            return_value=db_session,
        ),
        _patch.object(QualityBasedReallocationService, "rebalance", spy_rebalance),
    ):
        run_allocation_rebalance_cycle(trigger_source="scheduler")

    assert len(captured) == 1
    assert captured[0]["trigger_source"] == "scheduler"


def test_manual_rebalance_also_uses_locked_service_path(db_session: Session) -> None:
    """Direct service calls go through the same lock-guarded rebalance() method."""
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )

    _make_service(db_session).rebalance(
        actor="operator", trigger_source="manual", run_id="manual-001"
    )

    # Manual call flows through the same path; history row records trigger_source.
    history = db_session.scalars(
        select(AllocationRebalanceHistory).where(AllocationRebalanceHistory.run_id == "manual-001")
    ).all()
    assert len(history) >= 1
    assert any(row.trigger_source == "manual" for row in history)


# ---------------------------------------------------------------------------
# Rebalance_id attached to overrides
# ---------------------------------------------------------------------------


def test_rebalance_id_embedded_in_override_reason(db_session: Session) -> None:
    _seed_policy(db_session)
    _seed_strategy(db_session, "alpha")
    OperatorSettingsRepository(db_session).update_current(
        {"auto_rebalance_enabled": True}, updated_by="test"
    )
    fixed_id = "trace-rebalance-001"

    result = _make_service(db_session).rebalance(actor="test", rebalance_run_id=fixed_id)
    if result.noop:
        pytest.skip("No overrides written in noop scenario")

    overrides = db_session.query(AllocationOverrides).filter_by(is_active=True).all()
    for override in overrides:
        assert fixed_id in override.override_reason
