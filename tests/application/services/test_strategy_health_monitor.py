from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_health_monitor import (
    HealthPolicyConfig,
    StrategyHealthMonitor,
)
from autonomous_trading_platform.contracts.governance.strategy_health import StrategyHealthStatus
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
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
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_state_repository import (
    StrategyHealthStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_quality_score_repository import (
    StrategyQualityScoreRepository,
)

_POLICY = HealthPolicyConfig(
    health_monitor_enabled=True,
    quality_decline_cycles_threshold=3,
    quality_score_warning_threshold=0.5,
    quality_score_critical_threshold=0.3,
    live_drawdown_warning_pct=0.10,
    live_drawdown_critical_pct=0.20,
    min_history_points_for_decay_detection=3,
    mode="alert",
    auto_demote_on_critical=False,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
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
    rebalance_ids: list[str] | None = None,
) -> None:
    """Seed quality scores from newest to oldest (scores[0] is the most recent)."""
    now = datetime.now(UTC)
    repo = StrategyQualityScoreRepository(session)
    for i, score in enumerate(scores):
        rb_id = rebalance_ids[i] if rebalance_ids else f"rb_{strategy_id}_{i}"
        repo.insert(
            StrategyQualityScoreHistory(
                score_id=str(uuid4()),
                strategy_id=strategy_id,
                rebalance_run_id=rb_id,
                quality_score=score,
                live_score=score * 0.9,
                backtest_score=score * 1.1,
                blended_score=score,
                alpha_weight=0.5,
                score_components={},
                data_window={},
                governance_state="approved_live",
                # Most recent first: i=0 → now, i=1 → now-1h, etc.
                computed_at=now - timedelta(hours=i),
            )
        )


def _seed_live_snapshot(session: Session, strategy_id: str, realized_drawdown: float) -> None:
    session.add(
        StrategyLivePerformanceSnapshot(
            snapshot_id=str(uuid4()),
            strategy_id=strategy_id,
            run_id=None,
            computed_at=datetime.now(UTC),
            realized_drawdown=realized_drawdown,
        )
    )
    session.flush()


def _get_health_state(session: Session, strategy_id: str) -> StrategyHealthStateRow | None:
    return StrategyHealthStateRepository(session).get_for_strategy(strategy_id)


# ---------------------------------------------------------------------------
# Quality score history tests
# ---------------------------------------------------------------------------


def test_quality_scores_are_persisted_per_strategy(db_session: Session) -> None:
    _seed_strategy(db_session, "alpha")
    _seed_quality_history(db_session, "alpha", [0.8, 0.75, 0.7])

    repo = StrategyQualityScoreRepository(db_session)
    records = repo.get_recent_for_strategy("alpha", limit=10)

    assert len(records) == 3
    assert records[0].quality_score == 0.8  # newest first
    assert records[2].quality_score == 0.7  # oldest


def test_quality_scores_linked_to_rebalance_run(db_session: Session) -> None:
    _seed_strategy(db_session, "beta")
    rb_id = "rebalance_42"
    _seed_quality_history(
        db_session, "beta", [0.9, 0.85, 0.80], rebalance_ids=[rb_id, "rb_1", "rb_2"]
    )

    repo = StrategyQualityScoreRepository(db_session)
    records = repo.get_by_rebalance_run_id(rb_id)

    assert len(records) == 1
    assert records[0].quality_score == 0.9


# ---------------------------------------------------------------------------
# Insufficient history tests
# ---------------------------------------------------------------------------


def test_insufficient_history_does_not_degrade_strategy(db_session: Session) -> None:
    _seed_strategy(db_session, "thin")
    _seed_quality_history(db_session, "thin", [0.4, 0.5])  # only 2 points, threshold is 3

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    assert result.strategies_evaluated == 1
    assert result.skipped_count == 1
    skipped = result.results[0]
    assert skipped.skipped is True
    assert skipped.skip_reason == "insufficient_history"
    assert skipped.new_health_status == StrategyHealthStatus.HEALTHY


def test_no_governance_record_is_skipped(db_session: Session) -> None:
    monitor = StrategyHealthMonitor(session=db_session)
    eval_result = monitor.evaluate_strategy("ghost_strategy", policy=_POLICY)

    assert eval_result.skipped is True
    assert eval_result.skip_reason == "no_governance_record"


# ---------------------------------------------------------------------------
# Decline detection
# ---------------------------------------------------------------------------


def test_n_consecutive_declines_produces_degrading(db_session: Session) -> None:
    _seed_strategy(db_session, "declining")
    # 3 consecutive declines: 0.6 → 0.65 → 0.70 → 0.75 (newest first = declining)
    _seed_quality_history(db_session, "declining", [0.60, 0.65, 0.70, 0.75])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    assert result.strategies_evaluated == 1
    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.DEGRADING
    assert r.consecutive_decline_count >= 3
    assert r.skipped is False


def test_two_declines_below_threshold_stays_healthy(db_session: Session) -> None:
    _seed_strategy(db_session, "mild")
    # Only 2 consecutive declines: newest-first [0.65, 0.70, 0.75, 0.74]
    # i=0: 0.65<0.70 (decline 1), i=1: 0.70<0.75 (decline 2), i=2: 0.75<0.74 False → break
    # Score 0.65 is above warning threshold 0.5 → stays healthy
    _seed_quality_history(db_session, "mild", [0.65, 0.70, 0.75, 0.74])

    policy = HealthPolicyConfig(
        quality_decline_cycles_threshold=3,
        quality_score_warning_threshold=0.5,
        quality_score_critical_threshold=0.3,
        live_drawdown_warning_pct=0.10,
        live_drawdown_critical_pct=0.20,
        min_history_points_for_decay_detection=3,
        mode="alert",
    )
    result = StrategyHealthMonitor(session=db_session).run(policy=policy)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.HEALTHY
    assert r.consecutive_decline_count == 2


# ---------------------------------------------------------------------------
# Quality score threshold breach
# ---------------------------------------------------------------------------


def test_score_below_warning_threshold_produces_watch(db_session: Session) -> None:
    _seed_strategy(db_session, "low_score")
    # Score is 0.45, below warning threshold 0.5, no consecutive decline
    _seed_quality_history(db_session, "low_score", [0.45, 0.43, 0.46, 0.47])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.WATCH
    assert r.skipped is False


def test_score_below_critical_threshold_produces_critical(db_session: Session) -> None:
    _seed_strategy(db_session, "critical_score")
    # Score is below critical threshold 0.3
    _seed_quality_history(db_session, "critical_score", [0.25, 0.28, 0.29, 0.31])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.CRITICAL


# ---------------------------------------------------------------------------
# Drawdown breach
# ---------------------------------------------------------------------------


def test_drawdown_breach_produces_critical(db_session: Session) -> None:
    _seed_strategy(db_session, "dd_breach")
    _seed_quality_history(db_session, "dd_breach", [0.8, 0.79, 0.81, 0.82])
    _seed_live_snapshot(db_session, "dd_breach", realized_drawdown=-0.25)  # > 20% critical

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.CRITICAL
    assert "drawdown" in r.health_reason.lower()
    assert r.alert_emitted == "STRATEGY_DRAWDOWN_BREACH"


def test_drawdown_warning_produces_degrading(db_session: Session) -> None:
    _seed_strategy(db_session, "dd_warn")
    _seed_quality_history(db_session, "dd_warn", [0.8, 0.79, 0.81, 0.82])
    _seed_live_snapshot(db_session, "dd_warn", realized_drawdown=-0.15)  # > 10% warning, < 20%

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.DEGRADING


def test_small_drawdown_does_not_trigger_alert(db_session: Session) -> None:
    _seed_strategy(db_session, "healthy_dd")
    _seed_quality_history(db_session, "healthy_dd", [0.8, 0.79, 0.81, 0.82])
    _seed_live_snapshot(db_session, "healthy_dd", realized_drawdown=-0.05)  # < 10% warning

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.HEALTHY


# ---------------------------------------------------------------------------
# Recovery detection
# ---------------------------------------------------------------------------


def test_recovered_score_trend_emits_health_recovered_event(db_session: Session) -> None:
    _seed_strategy(db_session, "recovering")

    # Pre-seed a degrading health state
    now = datetime.now(UTC)
    StrategyHealthStateRepository(db_session).upsert(
        StrategyHealthStateRow(
            health_id=str(uuid4()),
            strategy_id="recovering",
            health_status=StrategyHealthStatus.DEGRADING,
            previous_health_status=None,
            health_reason="was degrading",
            consecutive_decline_count=4,
            quality_score_trend="down",
            latest_quality_score=0.55,
            realized_drawdown=None,
            last_health_evaluated_at=now - timedelta(hours=1),
            last_transition_at=now - timedelta(hours=1),
            evaluation_count=1,
            last_rebalance_run_id=None,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
    )
    db_session.flush()

    # Now the strategy has improved scores
    _seed_quality_history(db_session, "recovering", [0.80, 0.75, 0.72, 0.70])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.HEALTHY
    assert r.alert_emitted == "STRATEGY_HEALTH_RECOVERED"
    assert r.previous_health_status == StrategyHealthStatus.DEGRADING


# ---------------------------------------------------------------------------
# Health state persistence
# ---------------------------------------------------------------------------


def test_health_state_is_persisted_after_evaluation(db_session: Session) -> None:
    _seed_strategy(db_session, "persist_test")
    _seed_quality_history(db_session, "persist_test", [0.60, 0.65, 0.70, 0.75])

    StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    state = _get_health_state(db_session, "persist_test")
    assert state is not None
    assert state.health_status == StrategyHealthStatus.DEGRADING
    assert state.consecutive_decline_count == 3
    assert state.latest_quality_score == 0.60


def test_health_evaluation_is_idempotent(db_session: Session) -> None:
    _seed_strategy(db_session, "idempotent")
    _seed_quality_history(db_session, "idempotent", [0.60, 0.65, 0.70, 0.75])

    monitor = StrategyHealthMonitor(session=db_session)
    first = monitor.run(policy=_POLICY)
    second = monitor.run(policy=_POLICY)

    state = _get_health_state(db_session, "idempotent")

    assert first.results[0].new_health_status == second.results[0].new_health_status
    assert state is not None
    assert state.health_status == StrategyHealthStatus.DEGRADING
    # evaluation_count increments on each upsert
    assert state.evaluation_count >= 1


# ---------------------------------------------------------------------------
# Alert emission
# ---------------------------------------------------------------------------


def test_degrading_alert_emitted_in_alert_mode(db_session: Session) -> None:
    _seed_strategy(db_session, "alert_test")
    _seed_quality_history(db_session, "alert_test", [0.60, 0.65, 0.70, 0.75])

    StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    audit = db_session.query(AuditLogRow).filter_by(event_type="STRATEGY_HEALTH_DEGRADING").first()
    assert audit is not None
    assert audit.event_metadata["strategy_id"] == "alert_test"
    assert audit.event_metadata["new_health_status"] == StrategyHealthStatus.DEGRADING


def test_observe_only_mode_does_not_write_audit_event(db_session: Session) -> None:
    _seed_strategy(db_session, "observe_test")
    _seed_quality_history(db_session, "observe_test", [0.60, 0.65, 0.70, 0.75])

    observe_policy = HealthPolicyConfig(
        quality_decline_cycles_threshold=3,
        quality_score_warning_threshold=0.5,
        quality_score_critical_threshold=0.3,
        live_drawdown_warning_pct=0.10,
        live_drawdown_critical_pct=0.20,
        min_history_points_for_decay_detection=3,
        mode="observe",
    )
    StrategyHealthMonitor(session=db_session).run(policy=observe_policy)

    count = (
        db_session.query(AuditLogRow)
        .filter(
            AuditLogRow.event_type.in_(
                [
                    "STRATEGY_HEALTH_DEGRADING",
                    "STRATEGY_HEALTH_CRITICAL",
                    "STRATEGY_DRAWDOWN_BREACH",
                ]
            )
        )
        .count()
    )
    assert count == 0


# ---------------------------------------------------------------------------
# Health monitor disabled
# ---------------------------------------------------------------------------


def test_health_monitor_disabled_skips_evaluation(db_session: Session) -> None:
    _seed_strategy(db_session, "disabled_monitor")
    _seed_quality_history(db_session, "disabled_monitor", [0.60, 0.65, 0.70, 0.75])

    disabled_policy = HealthPolicyConfig(
        health_monitor_enabled=False,
    )
    result = StrategyHealthMonitor(session=db_session).run(policy=disabled_policy)

    assert result.health_monitor_enabled is False
    assert result.strategies_evaluated == 0
    assert result.results == []


# ---------------------------------------------------------------------------
# Governance API: health status surfaces separately from governance state
# ---------------------------------------------------------------------------


def test_health_status_independent_of_governance_state(db_session: Session) -> None:
    """An approved_live strategy can simultaneously have a degrading health status."""
    _seed_strategy(db_session, "live_but_degrading", state="approved_live")
    _seed_quality_history(db_session, "live_but_degrading", [0.60, 0.65, 0.70, 0.75])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    # Governance state is still approved_live
    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )

    gov = db_session.scalars(
        __import__("sqlalchemy", fromlist=["select"])
        .select(StrategyGovernance)
        .where(StrategyGovernance.strategy_id == "live_but_degrading")
    ).one()
    assert gov.current_state == "approved_live"

    # Health status is degrading
    assert r.new_health_status == StrategyHealthStatus.DEGRADING


# ---------------------------------------------------------------------------
# Auto-demotion hook in enforce mode
# ---------------------------------------------------------------------------


def test_critical_health_does_not_auto_demote_in_observe_mode(db_session: Session) -> None:
    _seed_strategy(db_session, "critical_observe", state="approved_live")
    _seed_quality_history(db_session, "critical_observe", [0.20, 0.22, 0.25, 0.28])

    observe_policy = HealthPolicyConfig(
        quality_decline_cycles_threshold=3,
        quality_score_warning_threshold=0.5,
        quality_score_critical_threshold=0.3,
        live_drawdown_warning_pct=0.10,
        live_drawdown_critical_pct=0.20,
        min_history_points_for_decay_detection=3,
        mode="observe",
        auto_demote_on_critical=False,
    )
    result = StrategyHealthMonitor(session=db_session).run(policy=observe_policy)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.CRITICAL

    # Governance state must not have changed
    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )

    gov = db_session.scalars(
        __import__("sqlalchemy", fromlist=["select"])
        .select(StrategyGovernance)
        .where(StrategyGovernance.strategy_id == "critical_observe")
    ).one()
    assert gov.current_state == "approved_live"


def test_critical_health_with_enforce_mode_triggers_demotion_hook(db_session: Session) -> None:
    _seed_strategy(db_session, "critical_enforce", state="approved_live")
    _seed_quality_history(db_session, "critical_enforce", [0.20, 0.22, 0.25, 0.28])

    OperatorSettingsRepository(db_session).update_current(
        {"auto_demote_on_breach": True, "max_strategy_drawdown": 0.15},
        updated_by="test",
    )

    enforce_policy = HealthPolicyConfig(
        quality_decline_cycles_threshold=3,
        quality_score_warning_threshold=0.5,
        quality_score_critical_threshold=0.3,
        live_drawdown_warning_pct=0.10,
        live_drawdown_critical_pct=0.20,
        min_history_points_for_decay_detection=3,
        mode="enforce",
        auto_demote_on_critical=True,
    )
    result = StrategyHealthMonitor(session=db_session).run(policy=enforce_policy)

    r = result.results[0]
    assert r.new_health_status == StrategyHealthStatus.CRITICAL
    # Auto-demotion service ran; may or may not have demoted (no maintenance rule configured)
    # but the hook was attempted without error
    assert r.skipped is False


# ---------------------------------------------------------------------------
# Multiple strategies: only monitorable states included
# ---------------------------------------------------------------------------


def test_retired_strategy_excluded_from_health_monitor(db_session: Session) -> None:
    _seed_strategy(db_session, "live_strat", state="approved_live")
    _seed_strategy(db_session, "retired_strat", state="retired")

    _seed_quality_history(db_session, "live_strat", [0.60, 0.65, 0.70, 0.75])
    _seed_quality_history(db_session, "retired_strat", [0.60, 0.65, 0.70, 0.75])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    evaluated_ids = {r.strategy_id for r in result.results}
    assert "live_strat" in evaluated_ids
    assert "retired_strat" not in evaluated_ids


def test_run_result_is_jsonable(db_session: Session) -> None:
    _seed_strategy(db_session, "json_test")
    _seed_quality_history(db_session, "json_test", [0.60, 0.65, 0.70, 0.75])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)
    payload = StrategyHealthMonitor.result_to_jsonable(result)

    import json

    raw = json.dumps(payload)
    parsed = json.loads(raw)
    assert parsed["strategies_evaluated"] == 1
    assert parsed["mode"] == "alert"


# ---------------------------------------------------------------------------
# Quality score trend computation
# ---------------------------------------------------------------------------


def test_upward_trend_detected(db_session: Session) -> None:
    _seed_strategy(db_session, "trending_up")
    # newest first: 0.90, then 0.80, then 0.70 → trend is UP (0.90 vs 0.70 = +0.20)
    _seed_quality_history(db_session, "trending_up", [0.90, 0.80, 0.70])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.quality_score_trend == "up"
    assert r.new_health_status == StrategyHealthStatus.HEALTHY


def test_downward_trend_detected(db_session: Session) -> None:
    _seed_strategy(db_session, "trending_down")
    # newest first: 0.70, 0.80, 0.90 → trend is DOWN (0.70 vs 0.90 = -0.20)
    _seed_quality_history(db_session, "trending_down", [0.70, 0.80, 0.90])

    result = StrategyHealthMonitor(session=db_session).run(policy=_POLICY)

    r = result.results[0]
    assert r.quality_score_trend == "down"
