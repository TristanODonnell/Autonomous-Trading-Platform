from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
)
from autonomous_trading_platform.contracts.governance.strategy_health import StrategyHealthStatus
from autonomous_trading_platform.contracts.governance.strategy_health_lifecycle import (
    HealthLifecycleConfig,
    LifecycleEvalMetrics,
    LifecycleRunResult,
    LifecycleStrategyResult,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    strategy_health_lifecycle_allocation_penalty,
    strategy_health_lifecycle_critical_total,
    strategy_health_lifecycle_degrading_total,
    strategy_health_lifecycle_duration_seconds,
    strategy_health_lifecycle_suspended_total,
    strategy_health_lifecycle_watch_total,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_health_state import (
    StrategyHealthStateRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_health_transitions import (
    StrategyHealthTransitionRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_quality_score_history import (
    StrategyQualityScoreHistory,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_state_repository import (
    StrategyHealthStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_transition_repository import (
    StrategyHealthTransitionRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_quality_score_repository import (
    StrategyQualityScoreRepository,
)

logger = get_logger(__name__)

_LIFECYCLE_ACTOR = "system_health_lifecycle"

# Structured audit event names
LIFECYCLE_EVENT_WATCH = "STRATEGY_HEALTH_WATCH"
LIFECYCLE_EVENT_DEGRADING = "STRATEGY_HEALTH_DEGRADING"
LIFECYCLE_EVENT_CRITICAL = "STRATEGY_HEALTH_CRITICAL"
LIFECYCLE_EVENT_SUSPENDED = "STRATEGY_HEALTH_SUSPENDED"
LIFECYCLE_EVENT_RECOVERED = "STRATEGY_HEALTH_RECOVERED"

_MONITORABLE_STATES = {
    "approved_for_paper_trading",
    "approved_paper",
    "approved_for_live_trading",
    "approved_live",
}

# Ordered severity ranks for transition logic (higher = worse)
_SEVERITY: dict[str, int] = {
    StrategyHealthStatus.HEALTHY: 0,
    StrategyHealthStatus.WATCH: 1,
    StrategyHealthStatus.DEGRADING: 2,
    StrategyHealthStatus.CRITICAL: 3,
    StrategyHealthStatus.SUSPENDED: 4,
}

_STATUS_BY_RANK: dict[int, str] = {v: k for k, v in _SEVERITY.items()}


def _rank(status: str) -> int:
    return _SEVERITY.get(status, 0)


def _status_one_step_better(status: str) -> str:
    """Return the status one rank lower (recovery is one step at a time)."""
    r = _rank(status)
    return _STATUS_BY_RANK.get(max(0, r - 1), StrategyHealthStatus.HEALTHY)


def compute_allocation_penalty(status: str, config: HealthLifecycleConfig) -> float:
    """Return the fraction [0.0, 1.0] of allocation to REMOVE for a given health status."""
    match status:
        case StrategyHealthStatus.WATCH:
            return config.watch_allocation_penalty
        case StrategyHealthStatus.DEGRADING:
            return config.degrading_allocation_penalty
        case StrategyHealthStatus.CRITICAL:
            return config.critical_allocation_penalty
        case StrategyHealthStatus.SUSPENDED:
            return config.suspended_allocation_penalty
        case _:  # HEALTHY
            return 0.0


def compute_target_status(
    metrics: LifecycleEvalMetrics,
    config: HealthLifecycleConfig,
) -> tuple[str, str]:
    """
    Pure function: classify health status from metrics and config thresholds.
    Returns (status, triggering_reason).
    Evaluated in severity order: CRITICAL → DEGRADING → WATCH → HEALTHY.
    """
    # -------------------------------------------------------------------
    # CRITICAL
    # -------------------------------------------------------------------
    if (
        metrics.drawdown_utilization is not None
        and metrics.drawdown_utilization >= config.critical_drawdown_utilization
    ):
        return (
            StrategyHealthStatus.CRITICAL,
            f"drawdown_utilization={metrics.drawdown_utilization:.2%} >= critical={config.critical_drawdown_utilization:.2%}",
        )
    if (
        metrics.quality_score is not None
        and metrics.quality_score < config.critical_quality_score_threshold
    ):
        return (
            StrategyHealthStatus.CRITICAL,
            f"quality_score={metrics.quality_score:.4f} < critical={config.critical_quality_score_threshold}",
        )
    if (
        metrics.rolling_sharpe is not None
        and metrics.rolling_sharpe < config.critical_sharpe_threshold
    ):
        return (
            StrategyHealthStatus.CRITICAL,
            f"rolling_sharpe={metrics.rolling_sharpe:.3f} < critical={config.critical_sharpe_threshold}",
        )
    if metrics.win_rate is not None and metrics.win_rate < config.critical_win_rate_threshold:
        return (
            StrategyHealthStatus.CRITICAL,
            f"win_rate={metrics.win_rate:.2%} < critical={config.critical_win_rate_threshold:.2%}",
        )
    if metrics.consecutive_quality_decline >= config.critical_quality_decline_cycles:
        return (
            StrategyHealthStatus.CRITICAL,
            f"consecutive_quality_decline={metrics.consecutive_quality_decline} >= critical={config.critical_quality_decline_cycles}",
        )
    if metrics.consecutive_negative_periods >= config.critical_consecutive_negative_periods:
        return (
            StrategyHealthStatus.CRITICAL,
            f"consecutive_negative_periods={metrics.consecutive_negative_periods} >= critical={config.critical_consecutive_negative_periods}",
        )

    # -------------------------------------------------------------------
    # DEGRADING
    # -------------------------------------------------------------------
    if (
        metrics.drawdown_utilization is not None
        and metrics.drawdown_utilization >= config.degrading_drawdown_utilization
    ):
        return (
            StrategyHealthStatus.DEGRADING,
            f"drawdown_utilization={metrics.drawdown_utilization:.2%} >= degrading={config.degrading_drawdown_utilization:.2%}",
        )
    if (
        metrics.quality_score is not None
        and metrics.quality_score < config.degrading_quality_score_threshold
    ):
        return (
            StrategyHealthStatus.DEGRADING,
            f"quality_score={metrics.quality_score:.4f} < degrading={config.degrading_quality_score_threshold}",
        )
    if (
        metrics.rolling_sharpe is not None
        and metrics.rolling_sharpe < config.degrading_sharpe_threshold
    ):
        return (
            StrategyHealthStatus.DEGRADING,
            f"rolling_sharpe={metrics.rolling_sharpe:.3f} < degrading={config.degrading_sharpe_threshold}",
        )
    if metrics.win_rate is not None and metrics.win_rate < config.degrading_win_rate_threshold:
        return (
            StrategyHealthStatus.DEGRADING,
            f"win_rate={metrics.win_rate:.2%} < degrading={config.degrading_win_rate_threshold:.2%}",
        )
    if metrics.consecutive_quality_decline >= config.degrading_quality_decline_cycles:
        return (
            StrategyHealthStatus.DEGRADING,
            f"consecutive_quality_decline={metrics.consecutive_quality_decline} >= degrading={config.degrading_quality_decline_cycles}",
        )
    if metrics.consecutive_negative_periods >= config.degrading_consecutive_negative_periods:
        return (
            StrategyHealthStatus.DEGRADING,
            f"consecutive_negative_periods={metrics.consecutive_negative_periods} >= degrading={config.degrading_consecutive_negative_periods}",
        )

    # -------------------------------------------------------------------
    # WATCH
    # -------------------------------------------------------------------
    if (
        metrics.drawdown_utilization is not None
        and metrics.drawdown_utilization >= config.watch_drawdown_utilization
    ):
        return (
            StrategyHealthStatus.WATCH,
            f"drawdown_utilization={metrics.drawdown_utilization:.2%} >= watch={config.watch_drawdown_utilization:.2%}",
        )
    if (
        metrics.quality_score is not None
        and metrics.quality_score < config.watch_quality_score_threshold
    ):
        return (
            StrategyHealthStatus.WATCH,
            f"quality_score={metrics.quality_score:.4f} < watch={config.watch_quality_score_threshold}",
        )
    if (
        metrics.rolling_sharpe is not None
        and metrics.rolling_sharpe < config.watch_sharpe_threshold
    ):
        return (
            StrategyHealthStatus.WATCH,
            f"rolling_sharpe={metrics.rolling_sharpe:.3f} < watch={config.watch_sharpe_threshold}",
        )
    if metrics.win_rate is not None and metrics.win_rate < config.watch_win_rate_threshold:
        return (
            StrategyHealthStatus.WATCH,
            f"win_rate={metrics.win_rate:.2%} < watch={config.watch_win_rate_threshold:.2%}",
        )
    if metrics.consecutive_quality_decline >= config.watch_quality_decline_cycles:
        return (
            StrategyHealthStatus.WATCH,
            f"consecutive_quality_decline={metrics.consecutive_quality_decline} >= watch={config.watch_quality_decline_cycles}",
        )
    if metrics.consecutive_negative_periods >= config.watch_consecutive_negative_periods:
        return (
            StrategyHealthStatus.WATCH,
            f"consecutive_negative_periods={metrics.consecutive_negative_periods} >= watch={config.watch_consecutive_negative_periods}",
        )

    return StrategyHealthStatus.HEALTHY, "all metrics within healthy range"


def _cooldown_expiry(
    to_status: str, now: datetime, config: HealthLifecycleConfig
) -> datetime | None:
    """Compute the cooldown timestamp after transitioning to to_status."""
    match to_status:
        case StrategyHealthStatus.WATCH:
            hours = config.cooldown_after_watch_hours
        case StrategyHealthStatus.DEGRADING:
            hours = config.cooldown_after_degrading_hours
        case StrategyHealthStatus.CRITICAL:
            hours = config.cooldown_after_critical_hours
        case StrategyHealthStatus.SUSPENDED:
            hours = config.cooldown_after_suspension_hours
        case _:
            return None
    return now + timedelta(hours=hours)


def _apply_transition_rules(
    current_status: str,
    raw_target: str,
    cooldown_expires_at: datetime | None,
    evaluation_count: int,
    now: datetime,
    config: HealthLifecycleConfig,
) -> tuple[str, str, bool]:
    """
    Apply anti-flapping rules and return (actual_new_status, reason, blocked).

    Rules:
    - SUSPENDED → only operator can recover (never auto-recovered here)
    - Escalation: always allowed, subject to min_observation_cycles from HEALTHY
    - Recovery: blocked during cooldown; one step at a time
    """
    if current_status == StrategyHealthStatus.SUSPENDED:
        return StrategyHealthStatus.SUSPENDED, "suspended: operator review required", False

    if raw_target == current_status:
        return current_status, "no change", False

    is_escalation = _rank(raw_target) > _rank(current_status)
    in_cooldown = cooldown_expires_at is not None and now < cooldown_expires_at

    if is_escalation:
        # Require min_observation_cycles before the very first escalation from HEALTHY
        if (
            current_status == StrategyHealthStatus.HEALTHY
            and evaluation_count < config.min_observation_cycles
        ):
            return (
                current_status,
                f"escalation deferred: observation_count={evaluation_count} < min={config.min_observation_cycles}",
                True,
            )
        return raw_target, f"threshold_breach → {raw_target}", False

    # Recovery path
    if in_cooldown:
        cooldown_str = cooldown_expires_at.isoformat() if cooldown_expires_at else "unknown"
        return (
            current_status,
            f"recovery deferred: in_cooldown until {cooldown_str}",
            True,
        )

    # One step at a time: always recover exactly one level regardless of how healthy metrics look
    step_target = _status_one_step_better(current_status)
    return step_target, f"recovery → {step_target}", False


class StrategyHealthLifecycleService:
    """
    Manages the strategy health lifecycle independently from governance approval state.

    Governance state answers: "Is this strategy approved?"
    Health lifecycle answers:  "Is this strategy currently healthy?"

    The two are strictly separated: this service never reads or mutates governance
    approval state.  A strategy can be approved_live + degrading, approved_live +
    suspended, etc.

    State machine (severity ascending):
        healthy ← → watch ← → degrading ← → critical → [suspended]

    Anti-flapping:
        - Cooldown windows block recovery transitions after any state change
        - Escalation always allowed (safety > anti-flapping), subject only to
          min_observation_cycles guard on first escalation from HEALTHY
        - Recovery is one step at a time (CRITICAL → DEGRADING, not CRITICAL → HEALTHY)
        - SUSPENDED requires operator action to clear

    Allocation integration:
        health_status → allocation_scalar (1.0 - penalty):
            healthy   → 1.00 (full allocation)
            watch     → 0.80 (20% reduction)
            degrading → 0.50 (50% reduction)
            critical  → 0.20 (80% reduction)
            suspended → 0.00 (no allocation)
    """

    def __init__(
        self,
        *,
        session: Session,
        quality_score_repo: StrategyQualityScoreRepository | None = None,
        health_state_repo: StrategyHealthStateRepository | None = None,
        transition_repo: StrategyHealthTransitionRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
        operator_settings_repo: OperatorSettingsRepository | None = None,
        live_perf_service: LivePerformanceMetricsService | None = None,
    ) -> None:
        self._session = session
        self._quality_score_repo = quality_score_repo or StrategyQualityScoreRepository(session)
        self._health_state_repo = health_state_repo or StrategyHealthStateRepository(session)
        self._transition_repo = transition_repo or StrategyHealthTransitionRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._live_perf_service = live_perf_service or LivePerformanceMetricsService(session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        run_id: str | None = None,
        rebalance_run_id: str | None = None,
        config: HealthLifecycleConfig | None = None,
    ) -> LifecycleRunResult:
        """Evaluate health lifecycle for all monitorable strategies."""
        start = perf_counter()
        now = datetime.now(UTC)
        run_id = run_id or str(uuid4())
        resolved_config = config or self._load_config()

        if not resolved_config.lifecycle_enabled:
            result = LifecycleRunResult(
                run_id=run_id,
                mode=resolved_config.mode,
                lifecycle_enabled=False,
                strategies_evaluated=0,
                transitions=[],
                alerts_emitted=0,
                suspensions=0,
                skipped_count=0,
                results=[],
                audit_event_type="STRATEGY_HEALTH_LIFECYCLE_SKIPPED",
                duration_seconds=perf_counter() - start,
            )
            self._audit_run(result=result, occurred_at=now)
            self._session.flush()
            self._session.commit()
            return result

        logger.info(
            "strategy_health_lifecycle.run_started",
            extra={"run_id": run_id, "mode": resolved_config.mode},
        )

        governance_rows = self._load_monitorable_strategies()
        results: list[LifecycleStrategyResult] = []
        transitions: list[dict[str, Any]] = []
        alerts_emitted = 0
        suspensions = 0

        for row in governance_rows:
            eval_result = self._evaluate_strategy(
                governance=row,
                config=resolved_config,
                now=now,
                rebalance_run_id=rebalance_run_id,
            )
            results.append(eval_result)

            if eval_result.skipped:
                continue

            if eval_result.did_transition:
                transitions.append(
                    {
                        "strategy_id": eval_result.strategy_id,
                        "from_status": eval_result.previous_health_status,
                        "to_status": eval_result.new_health_status,
                        "reason": eval_result.transition_reason,
                        "allocation_penalty": eval_result.allocation_penalty,
                    }
                )
                if eval_result.new_health_status != eval_result.previous_health_status:
                    alerts_emitted += 1
                if eval_result.auto_suspended:
                    suspensions += 1

        skipped_count = sum(1 for r in results if r.skipped)
        duration_seconds = perf_counter() - start
        strategy_health_lifecycle_duration_seconds.record(duration_seconds)

        logger.info(
            "strategy_health_lifecycle.run_completed",
            extra={
                "run_id": run_id,
                "evaluated": len(governance_rows),
                "transitions": len(transitions),
                "suspensions": suspensions,
                "mode": resolved_config.mode,
                "duration_seconds": duration_seconds,
            },
        )

        run_result = LifecycleRunResult(
            run_id=run_id,
            mode=resolved_config.mode,
            lifecycle_enabled=True,
            strategies_evaluated=len(governance_rows),
            transitions=transitions,
            alerts_emitted=alerts_emitted,
            suspensions=suspensions,
            skipped_count=skipped_count,
            results=results,
            audit_event_type="STRATEGY_HEALTH_LIFECYCLE_COMPLETED",
            duration_seconds=duration_seconds,
        )
        self._audit_run(result=run_result, occurred_at=now)
        self._session.flush()
        self._session.commit()
        return run_result

    def evaluate_strategy(
        self,
        strategy_id: str,
        *,
        config: HealthLifecycleConfig | None = None,
        rebalance_run_id: str | None = None,
    ) -> LifecycleStrategyResult:
        """Evaluate and persist lifecycle state for a single strategy."""
        resolved_config = config or self._load_config()
        governance = self._latest_governance(strategy_id)
        if governance is None:
            now = datetime.now(UTC)
            return LifecycleStrategyResult(
                strategy_id=strategy_id,
                governance_state="unknown",
                previous_health_status=None,
                new_health_status=StrategyHealthStatus.HEALTHY,
                transition_reason="no governance record",
                did_transition=False,
                transition_blocked_by_cooldown=False,
                allocation_penalty=0.0,
                allocation_scalar=1.0,
                consecutive_critical_count=0,
                cooldown_expires_at=None,
                operator_review_required=False,
                auto_suspended=False,
                skipped=True,
                skip_reason="no_governance_record",
                eval_metrics=None,
                evaluated_at=now,
            )
        return self._evaluate_strategy(
            governance=governance,
            config=resolved_config,
            now=datetime.now(UTC),
            rebalance_run_id=rebalance_run_id,
        )

    def clear_suspension(
        self,
        strategy_id: str,
        *,
        actor: str,
        reason: str,
    ) -> LifecycleStrategyResult:
        """
        Operator-initiated recovery from SUSPENDED state.
        Clears suspension and transitions to WATCH (conservative recovery).
        """
        now = datetime.now(UTC)
        existing = self._health_state_repo.get_for_strategy(strategy_id)

        if existing is None or existing.health_status != StrategyHealthStatus.SUSPENDED:
            status = existing.health_status if existing else "not_found"
            raise ValueError(
                f"Strategy {strategy_id!r} is not suspended (current_status={status!r})"
            )

        config = self._load_config()
        new_status = StrategyHealthStatus.WATCH
        penalty = compute_allocation_penalty(new_status, config)
        cooldown = _cooldown_expiry(new_status, now, config)
        triggered_by = f"operator:{actor}"

        # Persist transition
        self._record_transition(
            strategy_id=strategy_id,
            from_status=StrategyHealthStatus.SUSPENDED,
            to_status=new_status,
            reason=f"operator cleared suspension: {reason}",
            triggered_by=triggered_by,
            metrics=None,
            penalty=penalty,
            cooldown=cooldown,
            run_id=None,
            now=now,
        )

        # Update health state row
        existing.health_status = new_status
        existing.previous_health_status = StrategyHealthStatus.SUSPENDED
        existing.health_reason = f"suspension cleared by operator: {reason}"
        existing.suspended_at = None
        existing.suspension_reason = None
        existing.operator_review_required = False
        existing.cooldown_expires_at = cooldown
        existing.consecutive_critical_count = 0
        existing.allocation_penalty = penalty
        existing.lifecycle_mode = config.mode
        existing.last_transition_at = now
        existing.last_health_evaluated_at = now
        existing.updated_at = now
        self._session.flush()

        self._audit_log_repo.record_operator_action(
            action=LIFECYCLE_EVENT_RECOVERED,
            actor=triggered_by,
            reason=reason,
            occurred_at=now,
            component="governance",
            metadata={
                "strategy_id": strategy_id,
                "previous_status": StrategyHealthStatus.SUSPENDED,
                "new_status": new_status,
                "allocation_penalty": penalty,
            },
        )
        self._session.flush()
        self._session.commit()

        logger.info(
            "strategy_health_lifecycle.suspension_cleared",
            extra={"strategy_id": strategy_id, "actor": actor, "new_status": new_status},
        )

        return LifecycleStrategyResult(
            strategy_id=strategy_id,
            governance_state="unknown",
            previous_health_status=StrategyHealthStatus.SUSPENDED,
            new_health_status=new_status,
            transition_reason=f"operator cleared suspension: {reason}",
            did_transition=True,
            transition_blocked_by_cooldown=False,
            allocation_penalty=penalty,
            allocation_scalar=1.0 - penalty,
            consecutive_critical_count=0,
            cooldown_expires_at=cooldown,
            operator_review_required=False,
            auto_suspended=False,
            skipped=False,
            skip_reason=None,
            eval_metrics=None,
            evaluated_at=now,
        )

    def get_allocation_penalty(self, strategy_id: str) -> float:
        """
        Return the current allocation penalty [0.0, 1.0] for a strategy.
        0.0 = full allocation; 1.0 = no allocation.
        """
        state = self._health_state_repo.get_for_strategy(strategy_id)
        if state is None:
            return 0.0
        return float(state.allocation_penalty or 0.0)

    def get_allocation_scalar(self, strategy_id: str) -> float:
        """Return the allocation multiplier (1.0 - penalty)."""
        return 1.0 - self.get_allocation_penalty(strategy_id)

    def get_pending_reviews(self) -> list[StrategyHealthStateRow]:
        """Return all strategies currently requiring operator review."""
        return self._health_state_repo.get_pending_operator_review()

    # ------------------------------------------------------------------
    # Core evaluation logic
    # ------------------------------------------------------------------

    def _evaluate_strategy(
        self,
        *,
        governance: StrategyGovernance,
        config: HealthLifecycleConfig,
        now: datetime,
        rebalance_run_id: str | None,
    ) -> LifecycleStrategyResult:
        strategy_id = governance.strategy_id
        existing = self._health_state_repo.get_for_strategy(strategy_id)
        current_status = existing.health_status if existing else StrategyHealthStatus.HEALTHY
        evaluation_count = existing.evaluation_count if existing else 0
        cooldown_expires_at = existing.cooldown_expires_at if existing else None
        consecutive_critical = existing.consecutive_critical_count if existing else 0

        # If suspended, stay suspended (operator-only recovery)
        if current_status == StrategyHealthStatus.SUSPENDED:
            penalty = compute_allocation_penalty(StrategyHealthStatus.SUSPENDED, config)
            self._emit_otel(StrategyHealthStatus.SUSPENDED, strategy_id)
            return LifecycleStrategyResult(
                strategy_id=strategy_id,
                governance_state=governance.current_state,
                previous_health_status=StrategyHealthStatus.SUSPENDED,
                new_health_status=StrategyHealthStatus.SUSPENDED,
                transition_reason="suspended: operator review required",
                did_transition=False,
                transition_blocked_by_cooldown=False,
                allocation_penalty=penalty,
                allocation_scalar=1.0 - penalty,
                consecutive_critical_count=consecutive_critical,
                cooldown_expires_at=cooldown_expires_at,
                operator_review_required=True,
                auto_suspended=False,
                skipped=False,
                skip_reason=None,
                eval_metrics=None,
                evaluated_at=now,
            )

        # Gather metrics
        metrics = self._collect_metrics(strategy_id)

        # Compute raw target status from metrics
        raw_target, target_reason = compute_target_status(metrics, config)

        # Apply anti-flapping rules
        actual_status, transition_reason, blocked = _apply_transition_rules(
            current_status=current_status,
            raw_target=raw_target,
            cooldown_expires_at=cooldown_expires_at,
            evaluation_count=evaluation_count,
            now=now,
            config=config,
        )

        # Check for auto-suspension (only when in enforce mode)
        auto_suspended = False
        if (
            actual_status == StrategyHealthStatus.CRITICAL
            and config.auto_suspend_enabled
            and config.mode == "enforce"
        ):
            new_critical_count = consecutive_critical + 1
            if new_critical_count >= config.suspended_consecutive_critical_cycles:
                actual_status = StrategyHealthStatus.SUSPENDED
                transition_reason = (
                    f"auto-suspended after {new_critical_count} consecutive critical cycles "
                    f"(threshold={config.suspended_consecutive_critical_cycles})"
                )
                consecutive_critical = new_critical_count
                auto_suspended = True
            else:
                consecutive_critical = new_critical_count
        elif actual_status == StrategyHealthStatus.CRITICAL:
            consecutive_critical = consecutive_critical + 1
        else:
            consecutive_critical = 0

        did_transition = actual_status != current_status
        penalty = compute_allocation_penalty(actual_status, config)
        new_cooldown = (
            _cooldown_expiry(actual_status, now, config) if did_transition else cooldown_expires_at
        )

        # Persist new state (in both enforce and alert modes; observe = log only)
        if config.mode in ("alert", "enforce"):
            self._persist_state(
                strategy_id=strategy_id,
                existing=existing,
                new_status=actual_status,
                previous_status=current_status,
                reason=transition_reason,
                metrics=metrics,
                penalty=penalty,
                cooldown=new_cooldown,
                consecutive_critical=consecutive_critical,
                auto_suspended=auto_suspended,
                config=config,
                now=now,
                rebalance_run_id=rebalance_run_id,
            )

            if did_transition:
                self._record_transition(
                    strategy_id=strategy_id,
                    from_status=current_status,
                    to_status=actual_status,
                    reason=transition_reason,
                    triggered_by="system",
                    metrics=self._metrics_to_dict(metrics),
                    penalty=penalty,
                    cooldown=new_cooldown,
                    run_id=rebalance_run_id,
                    now=now,
                )
                self._emit_lifecycle_alert(
                    strategy_id=strategy_id,
                    from_status=current_status,
                    to_status=actual_status,
                    reason=transition_reason,
                    metrics=metrics,
                    penalty=penalty,
                    now=now,
                )

        self._emit_otel(actual_status, strategy_id)

        logger.info(
            "strategy_health_lifecycle.evaluated",
            extra={
                "strategy_id": strategy_id,
                "current_status": current_status,
                "new_status": actual_status,
                "did_transition": did_transition,
                "blocked": blocked,
                "allocation_penalty": penalty,
                "mode": config.mode,
            },
        )

        # Optional governance demotion hook
        if (
            actual_status == StrategyHealthStatus.CRITICAL
            and config.auto_demote_on_critical
            and config.mode == "enforce"
        ):
            self._trigger_demotion_hook(strategy_id=strategy_id, reason=transition_reason, now=now)

        return LifecycleStrategyResult(
            strategy_id=strategy_id,
            governance_state=governance.current_state,
            previous_health_status=current_status,
            new_health_status=actual_status,
            transition_reason=transition_reason,
            did_transition=did_transition,
            transition_blocked_by_cooldown=blocked,
            allocation_penalty=penalty,
            allocation_scalar=1.0 - penalty,
            consecutive_critical_count=consecutive_critical,
            cooldown_expires_at=new_cooldown,
            operator_review_required=auto_suspended
            or (actual_status == StrategyHealthStatus.SUSPENDED),
            auto_suspended=auto_suspended,
            skipped=False,
            skip_reason=None,
            eval_metrics=metrics,
            evaluated_at=now,
        )

    # ------------------------------------------------------------------
    # Metrics collection
    # ------------------------------------------------------------------

    def _collect_metrics(self, strategy_id: str) -> LifecycleEvalMetrics:
        history = self._quality_score_repo.get_recent_for_strategy(strategy_id, limit=12)
        quality_score = history[0].quality_score if history else None
        consecutive_decline = self._consecutive_decline_count(history)
        consecutive_negative = self._consecutive_negative_periods(history)

        live_snapshot = self._live_perf_service.get_latest(strategy_id)
        rolling_sharpe: float | None = None
        win_rate: float | None = None
        realized_drawdown: float | None = None
        if live_snapshot is not None:
            rolling_sharpe = live_snapshot.rolling_sharpe
            win_rate = live_snapshot.live_win_rate
            realized_drawdown = live_snapshot.realized_drawdown

        settings = self._operator_settings_repo.get_or_create_default()
        max_drawdown_allowed = float(settings.max_strategy_drawdown) if settings else None

        drawdown_utilization: float | None = None
        if realized_drawdown is not None and max_drawdown_allowed and max_drawdown_allowed > 0:
            drawdown_utilization = abs(realized_drawdown) / max_drawdown_allowed

        return LifecycleEvalMetrics(
            quality_score=quality_score,
            consecutive_quality_decline=consecutive_decline,
            consecutive_negative_periods=consecutive_negative,
            rolling_sharpe=rolling_sharpe,
            win_rate=win_rate,
            realized_drawdown=realized_drawdown,
            max_drawdown_allowed=max_drawdown_allowed,
            drawdown_utilization=drawdown_utilization,
        )

    def _consecutive_decline_count(self, history: list[StrategyQualityScoreHistory]) -> int:
        count = 0
        for i in range(len(history) - 1):
            if history[i].quality_score < history[i + 1].quality_score:
                count += 1
            else:
                break
        return count

    def _consecutive_negative_periods(self, history: list[StrategyQualityScoreHistory]) -> int:
        """Count consecutive recent cycles where quality_score < 0.5 (rough proxy for loss periods)."""
        count = 0
        for record in history:
            if record.quality_score < 0.50:
                count += 1
            else:
                break
        return count

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_state(
        self,
        *,
        strategy_id: str,
        existing: StrategyHealthStateRow | None,
        new_status: str,
        previous_status: str,
        reason: str,
        metrics: LifecycleEvalMetrics,
        penalty: float,
        cooldown: datetime | None,
        consecutive_critical: int,
        auto_suspended: bool,
        config: HealthLifecycleConfig,
        now: datetime,
        rebalance_run_id: str | None,
    ) -> None:
        did_transition = new_status != previous_status
        transition_at = (
            now if did_transition else (existing.last_transition_at if existing else now)
        )
        suspended_at = (
            now
            if new_status == StrategyHealthStatus.SUSPENDED and auto_suspended
            else (existing.suspended_at if existing else None)
        )
        suspension_reason = (
            reason
            if new_status == StrategyHealthStatus.SUSPENDED and auto_suspended
            else (existing.suspension_reason if existing else None)
        )
        if (
            new_status != StrategyHealthStatus.SUSPENDED
            and existing is not None
            and previous_status != StrategyHealthStatus.SUSPENDED
        ):
            suspended_at = None
            suspension_reason = None

        row = StrategyHealthStateRow(
            health_id=str(uuid4()),
            strategy_id=strategy_id,
            health_status=new_status,
            previous_health_status=previous_status,
            health_reason=reason,
            consecutive_decline_count=metrics.consecutive_quality_decline,
            quality_score_trend=None,
            latest_quality_score=metrics.quality_score,
            realized_drawdown=metrics.realized_drawdown,
            last_health_evaluated_at=now,
            last_transition_at=transition_at,
            evaluation_count=1,
            last_rebalance_run_id=rebalance_run_id,
            created_at=now,
            updated_at=now,
            suspended_at=suspended_at,
            suspension_reason=suspension_reason,
            operator_review_required=new_status == StrategyHealthStatus.SUSPENDED,
            cooldown_expires_at=cooldown,
            consecutive_critical_count=consecutive_critical,
            allocation_penalty=penalty,
            lifecycle_mode=config.mode,
        )
        self._health_state_repo.upsert(row)

    def _record_transition(
        self,
        *,
        strategy_id: str,
        from_status: str | None,
        to_status: str,
        reason: str,
        triggered_by: str,
        metrics: dict[str, Any] | None,
        penalty: float,
        cooldown: datetime | None,
        run_id: str | None,
        now: datetime,
    ) -> None:
        sharpe = None
        win_rate = None
        drawdown_utilization = None
        quality_score = None
        if metrics:
            sharpe = metrics.get("rolling_sharpe")
            win_rate = metrics.get("win_rate")
            drawdown_utilization = metrics.get("drawdown_utilization")
            quality_score = metrics.get("quality_score")

        row = StrategyHealthTransitionRow(
            transition_id=str(uuid4()),
            strategy_id=strategy_id,
            from_status=from_status,
            to_status=to_status,
            transition_reason=reason,
            triggered_by=triggered_by,
            triggering_metrics=metrics,
            drawdown_utilization=drawdown_utilization,
            quality_score=quality_score,
            rolling_sharpe=sharpe,
            win_rate=win_rate,
            allocation_penalty_after=penalty,
            cooldown_expires_at=cooldown,
            evaluation_run_id=run_id,
            created_at=now,
        )
        self._transition_repo.insert(row)

    def _emit_lifecycle_alert(
        self,
        *,
        strategy_id: str,
        from_status: str,
        to_status: str,
        reason: str,
        metrics: LifecycleEvalMetrics,
        penalty: float,
        now: datetime,
    ) -> None:
        event_map: dict[str, str] = {
            StrategyHealthStatus.WATCH: LIFECYCLE_EVENT_WATCH,
            StrategyHealthStatus.DEGRADING: LIFECYCLE_EVENT_DEGRADING,
            StrategyHealthStatus.CRITICAL: LIFECYCLE_EVENT_CRITICAL,
            StrategyHealthStatus.SUSPENDED: LIFECYCLE_EVENT_SUSPENDED,
        }
        was_degraded = _rank(from_status) > _rank(to_status)
        event: str | None = event_map.get(to_status) or (
            LIFECYCLE_EVENT_RECOVERED if was_degraded else None
        )
        if event is None:
            return

        self._audit_log_repo.record_operator_action(
            action=event,
            actor=_LIFECYCLE_ACTOR,
            reason=reason,
            occurred_at=now,
            component="governance",
            metadata={
                "strategy_id": strategy_id,
                "previous_status": from_status,
                "new_status": to_status,
                "quality_score": metrics.quality_score,
                "rolling_sharpe": metrics.rolling_sharpe,
                "win_rate": metrics.win_rate,
                "drawdown_utilization": metrics.drawdown_utilization,
                "realized_drawdown": metrics.realized_drawdown,
                "allocation_penalty": penalty,
                "allocation_scalar": 1.0 - penalty,
                "evaluated_at": now.isoformat(),
            },
        )

    def _emit_otel(self, status: str, strategy_id: str) -> None:
        attrs = {"strategy_id": strategy_id}
        match status:
            case StrategyHealthStatus.WATCH:
                strategy_health_lifecycle_watch_total.add(1, attrs)
            case StrategyHealthStatus.DEGRADING:
                strategy_health_lifecycle_degrading_total.add(1, attrs)
            case StrategyHealthStatus.CRITICAL:
                strategy_health_lifecycle_critical_total.add(1, attrs)
            case StrategyHealthStatus.SUSPENDED:
                strategy_health_lifecycle_suspended_total.add(1, attrs)

        penalty = compute_allocation_penalty(
            status,
            HealthLifecycleConfig(),  # defaults for metric label only
        )
        strategy_health_lifecycle_allocation_penalty.record(penalty, attrs)

    def _trigger_demotion_hook(
        self,
        *,
        strategy_id: str,
        reason: str,
        now: datetime,
    ) -> None:
        from autonomous_trading_platform.application.services.auto_demotion_service import (
            AutoDemotionService,
        )

        logger.warning(
            "strategy_health_lifecycle.critical_demotion_hook",
            extra={"strategy_id": strategy_id, "reason": reason},
        )
        try:
            AutoDemotionService(session=self._session).run(
                run_id=None,
                actor=_LIFECYCLE_ACTOR,
            )
        except Exception as exc:
            logger.warning(
                "strategy_health_lifecycle.demotion_hook_failed",
                extra={"strategy_id": strategy_id, "error": str(exc)},
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def _load_monitorable_strategies(self) -> list[StrategyGovernance]:
        rows = self._session.scalars(
            select(StrategyGovernance)
            .where(StrategyGovernance.current_state.in_(_MONITORABLE_STATES))
            .order_by(StrategyGovernance.strategy_id.asc(), StrategyGovernance.updated_at.desc())
        ).all()
        latest: dict[str, StrategyGovernance] = {}
        for row in rows:
            latest.setdefault(row.strategy_id, row)
        return list(latest.values())

    def _latest_governance(self, strategy_id: str) -> StrategyGovernance | None:
        return cast(
            StrategyGovernance | None,
            self._session.scalars(
                select(StrategyGovernance)
                .where(StrategyGovernance.strategy_id == strategy_id)
                .order_by(StrategyGovernance.updated_at.desc())
                .limit(1)
            ).one_or_none(),
        )

    def _load_config(self) -> HealthLifecycleConfig:
        settings = self._operator_settings_repo.get_or_create_default()
        auto_demote = bool(getattr(settings, "auto_demote_on_breach", False))
        return HealthLifecycleConfig(auto_demote_on_critical=auto_demote)

    def _audit_run(self, *, result: LifecycleRunResult, occurred_at: datetime) -> None:
        self._audit_log_repo.record_operator_action(
            action=result.audit_event_type,
            actor=_LIFECYCLE_ACTOR,
            reason=result.audit_event_type.lower().replace("_", " "),
            occurred_at=occurred_at,
            component="governance",
            metadata={
                "run_id": result.run_id,
                "mode": result.mode,
                "strategies_evaluated": result.strategies_evaluated,
                "transitions": len(result.transitions),
                "suspensions": result.suspensions,
                "skipped_count": result.skipped_count,
                "duration_seconds": result.duration_seconds,
            },
        )

    @staticmethod
    def _metrics_to_dict(metrics: LifecycleEvalMetrics) -> dict[str, Any]:
        return {
            "quality_score": metrics.quality_score,
            "consecutive_quality_decline": metrics.consecutive_quality_decline,
            "consecutive_negative_periods": metrics.consecutive_negative_periods,
            "rolling_sharpe": metrics.rolling_sharpe,
            "win_rate": metrics.win_rate,
            "realized_drawdown": metrics.realized_drawdown,
            "max_drawdown_allowed": metrics.max_drawdown_allowed,
            "drawdown_utilization": metrics.drawdown_utilization,
        }

    @staticmethod
    def result_to_jsonable(result: LifecycleRunResult) -> dict[str, Any]:
        return {
            "run_id": result.run_id,
            "mode": result.mode,
            "lifecycle_enabled": result.lifecycle_enabled,
            "strategies_evaluated": result.strategies_evaluated,
            "transitions": result.transitions,
            "suspensions": result.suspensions,
            "skipped_count": result.skipped_count,
            "duration_seconds": result.duration_seconds,
            "results": [
                {
                    "strategy_id": r.strategy_id,
                    "governance_state": r.governance_state,
                    "previous_health_status": r.previous_health_status,
                    "new_health_status": r.new_health_status,
                    "did_transition": r.did_transition,
                    "allocation_penalty": r.allocation_penalty,
                    "allocation_scalar": r.allocation_scalar,
                    "auto_suspended": r.auto_suspended,
                    "operator_review_required": r.operator_review_required,
                }
                for r in result.results
            ],
        }
