from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_health_lifecycle_service import (
    HealthLifecycleConfig,
    LifecycleEvalMetrics,
    StrategyHealthLifecycleService,
    _apply_transition_rules,
    _cooldown_expiry,
    compute_allocation_penalty,
    compute_target_status,
)
from autonomous_trading_platform.contracts.governance.strategy_health import StrategyHealthStatus
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_health_state import (
    StrategyHealthStateRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)
from autonomous_trading_platform.storage.sor.models.strategy_quality_score_history import (
    StrategyQualityScoreHistory,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_state_repository import (
    StrategyHealthStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_transition_repository import (
    StrategyHealthTransitionRepository,
)

# ---------------------------------------------------------------------------
# Shared config for tests
# ---------------------------------------------------------------------------

_CONFIG = HealthLifecycleConfig(
    lifecycle_enabled=True,
    mode="alert",
    watch_quality_score_threshold=0.55,
    watch_sharpe_threshold=0.50,
    watch_win_rate_threshold=0.45,
    watch_drawdown_utilization=0.30,
    watch_quality_decline_cycles=3,
    watch_consecutive_negative_periods=3,
    degrading_quality_score_threshold=0.40,
    degrading_sharpe_threshold=0.00,
    degrading_win_rate_threshold=0.38,
    degrading_drawdown_utilization=0.55,
    degrading_quality_decline_cycles=5,
    degrading_consecutive_negative_periods=5,
    critical_quality_score_threshold=0.25,
    critical_sharpe_threshold=-0.50,
    critical_win_rate_threshold=0.28,
    critical_drawdown_utilization=0.85,
    critical_quality_decline_cycles=8,
    critical_consecutive_negative_periods=7,
    auto_suspend_enabled=False,
    suspended_consecutive_critical_cycles=3,
    min_observation_cycles=3,
    cooldown_after_watch_hours=6.0,
    cooldown_after_degrading_hours=12.0,
    cooldown_after_critical_hours=24.0,
    cooldown_after_suspension_hours=48.0,
    watch_allocation_penalty=0.20,
    degrading_allocation_penalty=0.50,
    critical_allocation_penalty=0.80,
    suspended_allocation_penalty=1.00,
    auto_demote_on_critical=False,
)

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_strategy(session: Session, strategy_id: str, state: str = "approved_live") -> None:
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
            experiment_id="test_exp",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="test",
        )
    )
    session.flush()


def _seed_quality_history(
    session: Session,
    strategy_id: str,
    scores: list[float],
    base_time: datetime | None = None,
) -> None:
    base = base_time or _NOW
    for i, score in enumerate(scores):
        session.add(
            StrategyQualityScoreHistory(
                score_id=str(uuid4()),
                strategy_id=strategy_id,
                rebalance_run_id=None,
                quality_score=score,
                governance_state="approved_live",
                computed_at=base - timedelta(hours=i),
            )
        )
    session.flush()


def _seed_live_snapshot(
    session: Session,
    strategy_id: str,
    *,
    rolling_sharpe: float | None = None,
    win_rate: float | None = None,
    realized_drawdown: float | None = None,
) -> None:
    session.add(
        StrategyLivePerformanceSnapshot(
            snapshot_id=str(uuid4()),
            strategy_id=strategy_id,
            computed_at=datetime.now(UTC),
            days_live=30,
            trade_count=50,
            rolling_sharpe=rolling_sharpe,
            live_win_rate=win_rate,
            realized_drawdown=realized_drawdown,
            realized_return=None,
            realized_volatility=None,
        )
    )
    session.flush()


def _seed_health_state(
    session: Session,
    strategy_id: str,
    health_status: str,
    *,
    cooldown_expires_at: datetime | None = None,
    consecutive_critical_count: int = 0,
    evaluation_count: int = 5,
    suspended_at: datetime | None = None,
    allocation_penalty: float | None = None,
) -> None:
    now = datetime.now(UTC)
    session.add(
        StrategyHealthStateRow(
            health_id=str(uuid4()),
            strategy_id=strategy_id,
            health_status=health_status,
            previous_health_status=None,
            health_reason="seeded",
            consecutive_decline_count=0,
            quality_score_trend=None,
            latest_quality_score=None,
            realized_drawdown=None,
            last_health_evaluated_at=now,
            last_transition_at=now,
            evaluation_count=evaluation_count,
            last_rebalance_run_id=None,
            created_at=now,
            updated_at=now,
            cooldown_expires_at=cooldown_expires_at,
            consecutive_critical_count=consecutive_critical_count,
            suspended_at=suspended_at,
            suspension_reason="test suspension" if suspended_at else None,
            operator_review_required=health_status == StrategyHealthStatus.SUSPENDED,
            allocation_penalty=allocation_penalty,
            lifecycle_mode="alert",
        )
    )
    session.flush()


# ===========================================================================
# 1. compute_target_status — pure function tests
# ===========================================================================


def _healthy_metrics() -> LifecycleEvalMetrics:
    return LifecycleEvalMetrics(
        quality_score=0.75,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=1.5,
        win_rate=0.60,
        realized_drawdown=-0.01,
        max_drawdown_allowed=0.20,
        drawdown_utilization=0.05,
    )


def test_compute_target_status_healthy() -> None:
    status, _ = compute_target_status(_healthy_metrics(), _CONFIG)
    assert status == StrategyHealthStatus.HEALTHY


def test_compute_target_status_watch_quality_score() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.50,
        consecutive_quality_decline=1,
        consecutive_negative_periods=0,
        rolling_sharpe=1.0,
        win_rate=0.60,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.WATCH


def test_compute_target_status_watch_sharpe() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.70,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=0.30,
        win_rate=0.60,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.WATCH


def test_compute_target_status_watch_win_rate() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.70,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=1.0,
        win_rate=0.40,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.WATCH


def test_compute_target_status_watch_drawdown_utilization() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.70,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=1.0,
        win_rate=0.60,
        realized_drawdown=-0.06,
        max_drawdown_allowed=0.20,
        drawdown_utilization=0.30,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.WATCH


def test_compute_target_status_degrading_quality_score() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.38,
        consecutive_quality_decline=2,
        consecutive_negative_periods=0,
        rolling_sharpe=0.80,
        win_rate=0.55,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.DEGRADING


def test_compute_target_status_degrading_sharpe() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.60,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=-0.10,
        win_rate=0.55,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.DEGRADING


def test_compute_target_status_degrading_decline_cycles() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.60,
        consecutive_quality_decline=5,
        consecutive_negative_periods=0,
        rolling_sharpe=0.80,
        win_rate=0.55,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.DEGRADING


def test_compute_target_status_critical_quality_score() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.20,
        consecutive_quality_decline=2,
        consecutive_negative_periods=0,
        rolling_sharpe=0.50,
        win_rate=0.55,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.CRITICAL


def test_compute_target_status_critical_drawdown() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.70,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=1.0,
        win_rate=0.60,
        realized_drawdown=-0.17,
        max_drawdown_allowed=0.20,
        drawdown_utilization=0.85,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.CRITICAL


def test_compute_target_status_critical_sharpe() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.60,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=-0.60,
        win_rate=0.55,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.CRITICAL


def test_compute_target_status_no_live_data_with_good_quality() -> None:
    m = LifecycleEvalMetrics(
        quality_score=0.80,
        consecutive_quality_decline=0,
        consecutive_negative_periods=0,
        rolling_sharpe=None,
        win_rate=None,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.HEALTHY


def test_compute_target_status_critical_overrides_degrading() -> None:
    """Critical threshold breach overrides degrading — highest severity wins."""
    m = LifecycleEvalMetrics(
        quality_score=0.20,  # critical
        consecutive_quality_decline=6,  # also degrading range
        consecutive_negative_periods=0,
        rolling_sharpe=None,
        win_rate=None,
        realized_drawdown=None,
        max_drawdown_allowed=None,
        drawdown_utilization=None,
    )
    status, _ = compute_target_status(m, _CONFIG)
    assert status == StrategyHealthStatus.CRITICAL


# ===========================================================================
# 2. Allocation penalty
# ===========================================================================


def test_allocation_penalty_healthy() -> None:
    assert compute_allocation_penalty(StrategyHealthStatus.HEALTHY, _CONFIG) == 0.0


def test_allocation_penalty_watch() -> None:
    assert compute_allocation_penalty(StrategyHealthStatus.WATCH, _CONFIG) == 0.20


def test_allocation_penalty_degrading() -> None:
    assert compute_allocation_penalty(StrategyHealthStatus.DEGRADING, _CONFIG) == 0.50


def test_allocation_penalty_critical() -> None:
    assert compute_allocation_penalty(StrategyHealthStatus.CRITICAL, _CONFIG) == 0.80


def test_allocation_penalty_suspended() -> None:
    assert compute_allocation_penalty(StrategyHealthStatus.SUSPENDED, _CONFIG) == 1.00


def test_allocation_scalar_healthy() -> None:
    assert 1.0 - compute_allocation_penalty(StrategyHealthStatus.HEALTHY, _CONFIG) == 1.0


def test_allocation_scalar_suspended() -> None:
    assert 1.0 - compute_allocation_penalty(StrategyHealthStatus.SUSPENDED, _CONFIG) == 0.0


# ===========================================================================
# 3. Anti-flapping: _apply_transition_rules
# ===========================================================================


def test_transition_no_change() -> None:
    actual, _, blocked = _apply_transition_rules(
        current_status=StrategyHealthStatus.HEALTHY,
        raw_target=StrategyHealthStatus.HEALTHY,
        cooldown_expires_at=None,
        evaluation_count=5,
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.HEALTHY
    assert not blocked


def test_escalation_allowed_immediately() -> None:
    actual, _, blocked = _apply_transition_rules(
        current_status=StrategyHealthStatus.WATCH,
        raw_target=StrategyHealthStatus.DEGRADING,
        cooldown_expires_at=None,
        evaluation_count=10,
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.DEGRADING
    assert not blocked


def test_escalation_bypasses_cooldown() -> None:
    """Escalation is always allowed even during cooldown."""
    future_cooldown = _NOW + timedelta(hours=100)
    actual, _, blocked = _apply_transition_rules(
        current_status=StrategyHealthStatus.WATCH,
        raw_target=StrategyHealthStatus.CRITICAL,
        cooldown_expires_at=future_cooldown,
        evaluation_count=10,
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.CRITICAL
    assert not blocked


def test_recovery_blocked_during_cooldown() -> None:
    future_cooldown = _NOW + timedelta(hours=6)
    actual, reason, blocked = _apply_transition_rules(
        current_status=StrategyHealthStatus.DEGRADING,
        raw_target=StrategyHealthStatus.WATCH,
        cooldown_expires_at=future_cooldown,
        evaluation_count=10,
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.DEGRADING
    assert blocked
    assert "cooldown" in reason.lower()


def test_recovery_allowed_after_cooldown() -> None:
    expired_cooldown = _NOW - timedelta(hours=1)
    actual, _, blocked = _apply_transition_rules(
        current_status=StrategyHealthStatus.DEGRADING,
        raw_target=StrategyHealthStatus.HEALTHY,
        cooldown_expires_at=expired_cooldown,
        evaluation_count=10,
        now=_NOW,
        config=_CONFIG,
    )
    # One step at a time: DEGRADING → WATCH
    assert actual == StrategyHealthStatus.WATCH
    assert not blocked


def test_recovery_one_step_at_a_time() -> None:
    """CRITICAL cannot jump directly to HEALTHY — must step through DEGRADING."""
    actual, _, _ = _apply_transition_rules(
        current_status=StrategyHealthStatus.CRITICAL,
        raw_target=StrategyHealthStatus.HEALTHY,
        cooldown_expires_at=None,
        evaluation_count=10,
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.DEGRADING


def test_min_observation_cycles_defers_first_escalation() -> None:
    actual, reason, blocked = _apply_transition_rules(
        current_status=StrategyHealthStatus.HEALTHY,
        raw_target=StrategyHealthStatus.WATCH,
        cooldown_expires_at=None,
        evaluation_count=1,  # below min_observation_cycles=3
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.HEALTHY
    assert blocked


def test_min_observation_cycles_allows_at_threshold() -> None:
    actual, _, blocked = _apply_transition_rules(
        current_status=StrategyHealthStatus.HEALTHY,
        raw_target=StrategyHealthStatus.WATCH,
        cooldown_expires_at=None,
        evaluation_count=3,
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.WATCH
    assert not blocked


def test_suspended_never_auto_recovered() -> None:
    actual, reason, _ = _apply_transition_rules(
        current_status=StrategyHealthStatus.SUSPENDED,
        raw_target=StrategyHealthStatus.HEALTHY,
        cooldown_expires_at=None,
        evaluation_count=100,
        now=_NOW,
        config=_CONFIG,
    )
    assert actual == StrategyHealthStatus.SUSPENDED
    assert "operator" in reason.lower()


# ===========================================================================
# 4. Cooldown expiry
# ===========================================================================


def test_cooldown_expiry_watch() -> None:
    assert _cooldown_expiry(StrategyHealthStatus.WATCH, _NOW, _CONFIG) == _NOW + timedelta(
        hours=6.0
    )


def test_cooldown_expiry_degrading() -> None:
    assert _cooldown_expiry(StrategyHealthStatus.DEGRADING, _NOW, _CONFIG) == _NOW + timedelta(
        hours=12.0
    )


def test_cooldown_expiry_critical() -> None:
    assert _cooldown_expiry(StrategyHealthStatus.CRITICAL, _NOW, _CONFIG) == _NOW + timedelta(
        hours=24.0
    )


def test_cooldown_expiry_suspended() -> None:
    assert _cooldown_expiry(StrategyHealthStatus.SUSPENDED, _NOW, _CONFIG) == _NOW + timedelta(
        hours=48.0
    )


def test_cooldown_expiry_healthy_is_none() -> None:
    assert _cooldown_expiry(StrategyHealthStatus.HEALTHY, _NOW, _CONFIG) is None


# ===========================================================================
# 5. Service: evaluate_strategy integration tests
# ===========================================================================


def test_evaluate_strategy_healthy(db_session: Session) -> None:
    _seed_strategy(db_session, "s_eval_healthy")
    _seed_quality_history(db_session, "s_eval_healthy", [0.80, 0.82, 0.78, 0.81])

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_eval_healthy", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.HEALTHY
    assert result.allocation_penalty == 0.0
    assert result.allocation_scalar == 1.0
    assert not result.operator_review_required


def test_evaluate_strategy_transitions_to_watch(db_session: Session) -> None:
    _seed_strategy(db_session, "s_eval_watch")
    _seed_quality_history(db_session, "s_eval_watch", [0.50, 0.52, 0.48, 0.51])
    _seed_health_state(db_session, "s_eval_watch", StrategyHealthStatus.HEALTHY, evaluation_count=5)

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_eval_watch", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.WATCH
    assert result.allocation_penalty == 0.20
    assert result.did_transition


def test_evaluate_strategy_transitions_to_degrading(db_session: Session) -> None:
    _seed_strategy(db_session, "s_eval_degrading")
    _seed_quality_history(db_session, "s_eval_degrading", [0.35, 0.38, 0.36, 0.40])
    _seed_live_snapshot(db_session, "s_eval_degrading", rolling_sharpe=-0.05, win_rate=0.36)
    _seed_health_state(
        db_session, "s_eval_degrading", StrategyHealthStatus.WATCH, evaluation_count=10
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_eval_degrading", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.DEGRADING
    assert result.allocation_penalty == 0.50
    assert result.did_transition


def test_evaluate_strategy_transitions_to_critical(db_session: Session) -> None:
    _seed_strategy(db_session, "s_eval_critical")
    _seed_quality_history(db_session, "s_eval_critical", [0.20, 0.22, 0.18, 0.21])
    _seed_health_state(
        db_session, "s_eval_critical", StrategyHealthStatus.DEGRADING, evaluation_count=10
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_eval_critical", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.CRITICAL
    assert result.allocation_penalty == 0.80


def test_evaluate_strategy_recovery_blocked_by_cooldown(db_session: Session) -> None:
    _seed_strategy(db_session, "s_eval_cooldown")
    _seed_quality_history(db_session, "s_eval_cooldown", [0.80, 0.82, 0.78])
    _seed_health_state(
        db_session,
        "s_eval_cooldown",
        StrategyHealthStatus.DEGRADING,
        cooldown_expires_at=_NOW + timedelta(hours=10),
        evaluation_count=10,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_eval_cooldown", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.DEGRADING
    assert result.transition_blocked_by_cooldown


def test_evaluate_strategy_recovery_after_cooldown(db_session: Session) -> None:
    _seed_strategy(db_session, "s_eval_recovery")
    _seed_quality_history(db_session, "s_eval_recovery", [0.80, 0.78, 0.82])
    _seed_health_state(
        db_session,
        "s_eval_recovery",
        StrategyHealthStatus.DEGRADING,
        cooldown_expires_at=datetime(2026, 5, 1, tzinfo=UTC),
        evaluation_count=10,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_eval_recovery", config=_CONFIG)

    # One-step recovery: DEGRADING → WATCH
    assert result.new_health_status == StrategyHealthStatus.WATCH
    assert result.did_transition


def test_evaluate_strategy_suspended_stays_suspended(db_session: Session) -> None:
    _seed_strategy(db_session, "s_eval_suspended")
    _seed_quality_history(db_session, "s_eval_suspended", [0.80, 0.78, 0.82])
    _seed_health_state(
        db_session,
        "s_eval_suspended",
        StrategyHealthStatus.SUSPENDED,
        suspended_at=_NOW - timedelta(hours=2),
        evaluation_count=10,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_eval_suspended", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.SUSPENDED
    assert result.allocation_penalty == 1.0
    assert result.operator_review_required
    assert not result.did_transition


def test_evaluate_strategy_no_governance_record_returns_skipped(db_session: Session) -> None:
    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_no_gov", config=_CONFIG)

    assert result.skipped
    assert result.skip_reason == "no_governance_record"


# ===========================================================================
# 6. Auto-suspension
# ===========================================================================


def test_auto_suspension_triggered_after_n_critical_cycles(db_session: Session) -> None:
    _seed_strategy(db_session, "s_auto_suspend")
    _seed_quality_history(db_session, "s_auto_suspend", [0.15, 0.18, 0.14, 0.16])
    _seed_health_state(
        db_session,
        "s_auto_suspend",
        StrategyHealthStatus.CRITICAL,
        consecutive_critical_count=2,
        evaluation_count=10,
    )
    config = HealthLifecycleConfig(
        lifecycle_enabled=True,
        mode="enforce",
        auto_suspend_enabled=True,
        suspended_consecutive_critical_cycles=3,
        critical_quality_score_threshold=0.25,
        watch_quality_score_threshold=0.55,
        degrading_quality_score_threshold=0.40,
        watch_allocation_penalty=0.20,
        degrading_allocation_penalty=0.50,
        critical_allocation_penalty=0.80,
        suspended_allocation_penalty=1.00,
        min_observation_cycles=3,
        cooldown_after_watch_hours=6.0,
        cooldown_after_degrading_hours=12.0,
        cooldown_after_critical_hours=24.0,
        cooldown_after_suspension_hours=48.0,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_auto_suspend", config=config)

    assert result.new_health_status == StrategyHealthStatus.SUSPENDED
    assert result.auto_suspended
    assert result.operator_review_required
    assert result.allocation_penalty == 1.0


def test_auto_suspension_not_triggered_in_observe_mode(db_session: Session) -> None:
    _seed_strategy(db_session, "s_no_auto_suspend_observe")
    _seed_quality_history(db_session, "s_no_auto_suspend_observe", [0.15, 0.18, 0.14, 0.16])
    _seed_health_state(
        db_session,
        "s_no_auto_suspend_observe",
        StrategyHealthStatus.CRITICAL,
        consecutive_critical_count=5,
        evaluation_count=10,
    )
    config = HealthLifecycleConfig(
        lifecycle_enabled=True,
        mode="observe",  # observe mode does not mutate state
        auto_suspend_enabled=True,
        suspended_consecutive_critical_cycles=3,
        critical_quality_score_threshold=0.25,
        watch_quality_score_threshold=0.55,
        degrading_quality_score_threshold=0.40,
        watch_allocation_penalty=0.20,
        degrading_allocation_penalty=0.50,
        critical_allocation_penalty=0.80,
        suspended_allocation_penalty=1.00,
        min_observation_cycles=3,
        cooldown_after_watch_hours=6.0,
        cooldown_after_degrading_hours=12.0,
        cooldown_after_critical_hours=24.0,
        cooldown_after_suspension_hours=48.0,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_no_auto_suspend_observe", config=config)

    assert result.new_health_status != StrategyHealthStatus.SUSPENDED


def test_auto_suspension_not_triggered_below_threshold(db_session: Session) -> None:
    _seed_strategy(db_session, "s_no_suspend_below_thresh")
    _seed_quality_history(db_session, "s_no_suspend_below_thresh", [0.15, 0.18, 0.14, 0.16])
    _seed_health_state(
        db_session,
        "s_no_suspend_below_thresh",
        StrategyHealthStatus.CRITICAL,
        consecutive_critical_count=1,
        evaluation_count=10,
    )
    config = HealthLifecycleConfig(
        lifecycle_enabled=True,
        mode="enforce",
        auto_suspend_enabled=True,
        suspended_consecutive_critical_cycles=3,
        critical_quality_score_threshold=0.25,
        watch_quality_score_threshold=0.55,
        degrading_quality_score_threshold=0.40,
        watch_allocation_penalty=0.20,
        degrading_allocation_penalty=0.50,
        critical_allocation_penalty=0.80,
        suspended_allocation_penalty=1.00,
        min_observation_cycles=3,
        cooldown_after_watch_hours=6.0,
        cooldown_after_degrading_hours=12.0,
        cooldown_after_critical_hours=24.0,
        cooldown_after_suspension_hours=48.0,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_no_suspend_below_thresh", config=config)

    assert result.new_health_status == StrategyHealthStatus.CRITICAL
    assert not result.auto_suspended


# ===========================================================================
# 7. Operator recovery (clear_suspension)
# ===========================================================================


def test_clear_suspension_succeeds(db_session: Session) -> None:
    _seed_strategy(db_session, "s_clear_suspension")
    _seed_health_state(
        db_session,
        "s_clear_suspension",
        StrategyHealthStatus.SUSPENDED,
        suspended_at=_NOW - timedelta(hours=6),
        evaluation_count=10,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.clear_suspension(
        "s_clear_suspension", actor="risk_manager_1", reason="metrics recovered"
    )

    assert result.new_health_status == StrategyHealthStatus.WATCH
    assert not result.operator_review_required
    assert result.did_transition
    assert result.allocation_penalty == _CONFIG.watch_allocation_penalty


def test_clear_suspension_records_transition(db_session: Session) -> None:
    _seed_strategy(db_session, "s_clear_audit")
    _seed_health_state(
        db_session,
        "s_clear_audit",
        StrategyHealthStatus.SUSPENDED,
        suspended_at=_NOW - timedelta(hours=2),
        evaluation_count=10,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    svc.clear_suspension("s_clear_audit", actor="operator_a", reason="manual review complete")

    transition_repo = StrategyHealthTransitionRepository(db_session)
    rows = transition_repo.get_recent_for_strategy("s_clear_audit")
    assert len(rows) >= 1
    latest = rows[0]
    assert latest.from_status == StrategyHealthStatus.SUSPENDED
    assert latest.to_status == StrategyHealthStatus.WATCH
    assert "operator:operator_a" in latest.triggered_by


def test_clear_suspension_fails_if_not_suspended(db_session: Session) -> None:
    _seed_strategy(db_session, "s_not_suspended")
    _seed_health_state(db_session, "s_not_suspended", StrategyHealthStatus.DEGRADING)

    svc = StrategyHealthLifecycleService(session=db_session)
    with pytest.raises(ValueError, match="not suspended"):
        svc.clear_suspension("s_not_suspended", actor="op", reason="test")


def test_clear_suspension_fails_if_no_state(db_session: Session) -> None:
    _seed_strategy(db_session, "s_no_state_for_clear")

    svc = StrategyHealthLifecycleService(session=db_session)
    with pytest.raises(ValueError, match="not suspended"):
        svc.clear_suspension("s_no_state_for_clear", actor="op", reason="test")


def test_clear_suspension_sets_cooldown(db_session: Session) -> None:
    _seed_strategy(db_session, "s_clear_cooldown_check")
    _seed_health_state(
        db_session,
        "s_clear_cooldown_check",
        StrategyHealthStatus.SUSPENDED,
        suspended_at=_NOW - timedelta(hours=1),
        evaluation_count=10,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.clear_suspension("s_clear_cooldown_check", actor="op", reason="cleared")

    assert result.cooldown_expires_at is not None


# ===========================================================================
# 8. Allocation penalty query
# ===========================================================================


def test_get_allocation_penalty_no_state_returns_zero(db_session: Session) -> None:
    svc = StrategyHealthLifecycleService(session=db_session)
    assert svc.get_allocation_penalty("s_penalty_no_state") == 0.0


def test_get_allocation_penalty_degrading(db_session: Session) -> None:
    _seed_health_state(
        db_session, "s_penalty_degrading", StrategyHealthStatus.DEGRADING, allocation_penalty=0.50
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    assert svc.get_allocation_penalty("s_penalty_degrading") == pytest.approx(0.50)


def test_get_allocation_scalar_suspended(db_session: Session) -> None:
    _seed_health_state(
        db_session,
        "s_scalar_suspended",
        StrategyHealthStatus.SUSPENDED,
        suspended_at=_NOW,
        allocation_penalty=1.0,
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    assert svc.get_allocation_scalar("s_scalar_suspended") == pytest.approx(0.0)


# ===========================================================================
# 9. Persistence: transition records
# ===========================================================================


def test_transition_recorded_on_state_change(db_session: Session) -> None:
    _seed_strategy(db_session, "s_transition_record")
    _seed_quality_history(db_session, "s_transition_record", [0.50, 0.52, 0.51, 0.49])
    _seed_health_state(
        db_session, "s_transition_record", StrategyHealthStatus.HEALTHY, evaluation_count=5
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_transition_record", config=_CONFIG)

    assert result.did_transition
    rows = StrategyHealthTransitionRepository(db_session).get_recent_for_strategy(
        "s_transition_record"
    )
    assert len(rows) == 1
    assert rows[0].from_status == StrategyHealthStatus.HEALTHY
    assert rows[0].to_status == StrategyHealthStatus.WATCH
    assert rows[0].triggered_by == "system"


def test_no_transition_recorded_when_status_unchanged(db_session: Session) -> None:
    _seed_strategy(db_session, "s_no_transition")
    _seed_quality_history(db_session, "s_no_transition", [0.50, 0.52, 0.49, 0.51])
    _seed_health_state(
        db_session, "s_no_transition", StrategyHealthStatus.WATCH, evaluation_count=10
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_no_transition", config=_CONFIG)

    assert not result.did_transition
    rows = StrategyHealthTransitionRepository(db_session).get_recent_for_strategy("s_no_transition")
    assert len(rows) == 0


def test_transition_includes_metric_snapshot(db_session: Session) -> None:
    _seed_strategy(db_session, "s_transition_metrics")
    _seed_quality_history(db_session, "s_transition_metrics", [0.20, 0.22, 0.19, 0.21])
    _seed_live_snapshot(db_session, "s_transition_metrics", rolling_sharpe=-0.60, win_rate=0.25)
    _seed_health_state(
        db_session, "s_transition_metrics", StrategyHealthStatus.DEGRADING, evaluation_count=10
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_transition_metrics", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.CRITICAL
    rows = StrategyHealthTransitionRepository(db_session).get_recent_for_strategy(
        "s_transition_metrics"
    )
    assert len(rows) == 1
    assert rows[0].quality_score is not None
    assert rows[0].triggering_metrics is not None


# ===========================================================================
# 10. Governance separation
# ===========================================================================


def test_governance_state_unchanged_when_health_degrades(db_session: Session) -> None:
    from sqlalchemy import select

    _seed_strategy(db_session, "s_gov_separation", state="approved_live")
    _seed_quality_history(db_session, "s_gov_separation", [0.20, 0.22, 0.19])
    _seed_health_state(
        db_session, "s_gov_separation", StrategyHealthStatus.HEALTHY, evaluation_count=5
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.evaluate_strategy("s_gov_separation", config=_CONFIG)

    assert result.new_health_status == StrategyHealthStatus.CRITICAL

    gov = db_session.scalars(
        select(StrategyGovernance)
        .where(StrategyGovernance.strategy_id == "s_gov_separation")
        .order_by(StrategyGovernance.updated_at.desc())
        .limit(1)
    ).one_or_none()
    assert gov is not None
    assert gov.current_state == "approved_live"


def test_approved_live_and_suspended_coexist(db_session: Session) -> None:
    from sqlalchemy import select

    _seed_strategy(db_session, "s_approved_live_suspended", state="approved_live")
    _seed_health_state(
        db_session,
        "s_approved_live_suspended",
        StrategyHealthStatus.SUSPENDED,
        suspended_at=_NOW,
        evaluation_count=10,
    )

    health_row = StrategyHealthStateRepository(db_session).get_for_strategy(
        "s_approved_live_suspended"
    )
    gov = db_session.scalars(
        select(StrategyGovernance)
        .where(StrategyGovernance.strategy_id == "s_approved_live_suspended")
        .limit(1)
    ).one_or_none()

    assert gov is not None and gov.current_state == "approved_live"
    assert health_row is not None and health_row.health_status == StrategyHealthStatus.SUSPENDED


# ===========================================================================
# 11. Full run() cycle
# ===========================================================================


def test_run_evaluates_all_monitorable_strategies(db_session: Session) -> None:
    for i in range(3):
        sid = f"s_run_{i}"
        _seed_strategy(db_session, sid, state="approved_live")
        _seed_quality_history(db_session, sid, [0.80, 0.78, 0.82, 0.76])
        _seed_health_state(db_session, sid, StrategyHealthStatus.HEALTHY, evaluation_count=5)

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.run(config=_CONFIG)

    assert result.lifecycle_enabled
    assert result.strategies_evaluated == 3
    assert result.mode == "alert"


def test_run_skips_non_monitorable_states(db_session: Session) -> None:
    _seed_strategy(db_session, "s_retired", state="retired")
    _seed_quality_history(db_session, "s_retired", [0.80, 0.78, 0.82])

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.run(config=_CONFIG)

    assert result.strategies_evaluated == 0


def test_run_disabled_returns_early(db_session: Session) -> None:
    config = HealthLifecycleConfig(lifecycle_enabled=False)
    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.run(config=config)

    assert not result.lifecycle_enabled
    assert result.strategies_evaluated == 0


def test_run_detects_transitions(db_session: Session) -> None:
    _seed_strategy(db_session, "s_run_transition", state="approved_live")
    _seed_quality_history(db_session, "s_run_transition", [0.38, 0.35, 0.40, 0.36])
    _seed_live_snapshot(db_session, "s_run_transition", rolling_sharpe=-0.02, win_rate=0.36)
    _seed_health_state(
        db_session, "s_run_transition", StrategyHealthStatus.WATCH, evaluation_count=10
    )

    svc = StrategyHealthLifecycleService(session=db_session)
    result = svc.run(config=_CONFIG)

    assert any(
        t["strategy_id"] == "s_run_transition" and t["to_status"] == StrategyHealthStatus.DEGRADING
        for t in result.transitions
    )


# ===========================================================================
# 12. Pending operator review
# ===========================================================================


def test_get_pending_reviews_returns_suspended_strategies(db_session: Session) -> None:
    for i in range(2):
        _seed_health_state(
            db_session,
            f"s_pending_{i}",
            StrategyHealthStatus.SUSPENDED,
            suspended_at=_NOW,
            allocation_penalty=1.0,
        )
    _seed_health_state(db_session, "s_not_pending", StrategyHealthStatus.DEGRADING)

    svc = StrategyHealthLifecycleService(session=db_session)
    pending = svc.get_pending_reviews()

    assert len(pending) == 2
    assert all(r.operator_review_required for r in pending)
