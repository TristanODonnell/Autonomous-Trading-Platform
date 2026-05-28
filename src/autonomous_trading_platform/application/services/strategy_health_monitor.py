from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
)
from autonomous_trading_platform.contracts.governance.strategy_health import StrategyHealthStatus
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    strategy_health_critical_total,
    strategy_health_degrading_total,
    strategy_health_evaluation_duration_seconds,
    strategy_health_quality_decline_streak,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_health_state import (
    StrategyHealthStateRow,
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
from autonomous_trading_platform.storage.sor.repositories.core.strategy_quality_score_repository import (
    StrategyQualityScoreRepository,
)

logger = get_logger(__name__)

HEALTH_MONITOR_ACTOR = "system_health_monitor"

HEALTH_EVENT_DEGRADING = "STRATEGY_HEALTH_DEGRADING"
HEALTH_EVENT_CRITICAL = "STRATEGY_HEALTH_CRITICAL"
HEALTH_EVENT_DRAWDOWN_BREACH = "STRATEGY_DRAWDOWN_BREACH"
HEALTH_EVENT_RECOVERED = "STRATEGY_HEALTH_RECOVERED"

# Governance states eligible for health monitoring
_MONITORABLE_STATES = {
    "approved_for_paper_trading",
    "approved_paper",
    "approved_for_live_trading",
    "approved_live",
}

_TREND_UP = "up"
_TREND_DOWN = "down"
_TREND_FLAT = "flat"
_TREND_INSUFFICIENT = "insufficient_data"


@dataclass(frozen=True)
class HealthPolicyConfig:
    health_monitor_enabled: bool = True
    quality_decline_cycles_threshold: int = 3
    quality_score_warning_threshold: float = 0.5
    quality_score_critical_threshold: float = 0.3
    live_drawdown_warning_pct: float = 0.10
    live_drawdown_critical_pct: float = 0.20
    min_history_points_for_decay_detection: int = 3
    # observe | alert | enforce
    mode: str = "observe"
    auto_demote_on_critical: bool = False


@dataclass(frozen=True)
class HealthEvaluationResult:
    strategy_id: str
    previous_health_status: str | None
    new_health_status: str
    health_reason: str
    quality_score: float | None
    quality_score_trend: str
    consecutive_decline_count: int
    realized_drawdown: float | None
    alert_emitted: str | None
    skipped: bool
    skip_reason: str | None
    evaluated_at: datetime
    rebalance_run_id: str | None


@dataclass(frozen=True)
class HealthMonitorRunResult:
    run_id: str | None
    mode: str
    health_monitor_enabled: bool
    strategies_evaluated: int
    transitions: list[dict[str, Any]]
    alerts_emitted: int
    skipped_count: int
    results: list[HealthEvaluationResult]
    audit_event_type: str
    duration_seconds: float


class StrategyHealthMonitor:
    def __init__(
        self,
        *,
        session: Session,
        quality_score_repo: StrategyQualityScoreRepository | None = None,
        health_state_repo: StrategyHealthStateRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
        operator_settings_repo: OperatorSettingsRepository | None = None,
        live_perf_service: LivePerformanceMetricsService | None = None,
    ) -> None:
        self._session = session
        self._quality_score_repo = quality_score_repo or StrategyQualityScoreRepository(session)
        self._health_state_repo = health_state_repo or StrategyHealthStateRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._live_perf_service = live_perf_service or LivePerformanceMetricsService(session)

    def run(
        self,
        *,
        run_id: str | None = None,
        rebalance_run_id: str | None = None,
        policy: HealthPolicyConfig | None = None,
    ) -> HealthMonitorRunResult:
        start = perf_counter()
        now = datetime.now(UTC)
        run_id = run_id or str(uuid4())

        resolved_policy = policy or self._load_policy()

        if not resolved_policy.health_monitor_enabled:
            result = HealthMonitorRunResult(
                run_id=run_id,
                mode=resolved_policy.mode,
                health_monitor_enabled=False,
                strategies_evaluated=0,
                transitions=[],
                alerts_emitted=0,
                skipped_count=0,
                results=[],
                audit_event_type="STRATEGY_HEALTH_MONITOR_SKIPPED",
                duration_seconds=perf_counter() - start,
            )
            self._audit_run(result=result, occurred_at=now)
            self._session.flush()
            self._session.commit()
            return result

        logger.info(
            "strategy_health_monitor.run_started",
            extra={"run_id": run_id, "mode": resolved_policy.mode},
        )

        governance_rows = self._load_monitorable_strategies()
        results: list[HealthEvaluationResult] = []
        transitions: list[dict[str, Any]] = []
        alerts_emitted = 0

        for row in governance_rows:
            eval_result = self._evaluate_strategy(
                governance=row,
                policy=resolved_policy,
                now=now,
                rebalance_run_id=rebalance_run_id,
            )
            results.append(eval_result)

            if eval_result.skipped:
                continue

            self._persist_health_state(eval_result=eval_result, now=now)

            if eval_result.previous_health_status != eval_result.new_health_status:
                transitions.append(
                    {
                        "strategy_id": eval_result.strategy_id,
                        "from_status": eval_result.previous_health_status,
                        "to_status": eval_result.new_health_status,
                        "reason": eval_result.health_reason,
                    }
                )

            if eval_result.alert_emitted is not None:
                self._emit_health_alert(eval_result=eval_result, policy=resolved_policy, now=now)
                alerts_emitted += 1

            if (
                eval_result.new_health_status == StrategyHealthStatus.CRITICAL
                and resolved_policy.auto_demote_on_critical
                and resolved_policy.mode == "enforce"
            ):
                self._trigger_auto_demotion_hook(eval_result=eval_result, now=now)

        skipped_count = sum(1 for r in results if r.skipped)
        duration_seconds = perf_counter() - start

        strategy_health_evaluation_duration_seconds.record(duration_seconds)
        logger.info(
            "strategy_health_monitor.run_completed",
            extra={
                "run_id": run_id,
                "strategies_evaluated": len(governance_rows),
                "transitions": len(transitions),
                "alerts_emitted": alerts_emitted,
                "skipped_count": skipped_count,
                "mode": resolved_policy.mode,
                "duration_seconds": duration_seconds,
            },
        )

        run_result = HealthMonitorRunResult(
            run_id=run_id,
            mode=resolved_policy.mode,
            health_monitor_enabled=True,
            strategies_evaluated=len(governance_rows),
            transitions=transitions,
            alerts_emitted=alerts_emitted,
            skipped_count=skipped_count,
            results=results,
            audit_event_type="STRATEGY_HEALTH_MONITOR_COMPLETED",
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
        policy: HealthPolicyConfig | None = None,
        rebalance_run_id: str | None = None,
    ) -> HealthEvaluationResult:
        """Evaluate a single strategy; does not persist or emit alerts."""
        resolved_policy = policy or self._load_policy()
        governance = self._latest_governance(strategy_id)
        if governance is None:
            now = datetime.now(UTC)
            return HealthEvaluationResult(
                strategy_id=strategy_id,
                previous_health_status=None,
                new_health_status=StrategyHealthStatus.HEALTHY,
                health_reason="no governance record found",
                quality_score=None,
                quality_score_trend=_TREND_INSUFFICIENT,
                consecutive_decline_count=0,
                realized_drawdown=None,
                alert_emitted=None,
                skipped=True,
                skip_reason="no_governance_record",
                evaluated_at=now,
                rebalance_run_id=rebalance_run_id,
            )
        return self._evaluate_strategy(
            governance=governance,
            policy=resolved_policy,
            now=datetime.now(UTC),
            rebalance_run_id=rebalance_run_id,
        )

    # ------------------------------------------------------------------
    # Core evaluation logic

    def _evaluate_strategy(
        self,
        *,
        governance: StrategyGovernance,
        policy: HealthPolicyConfig,
        now: datetime,
        rebalance_run_id: str | None,
    ) -> HealthEvaluationResult:
        strategy_id = governance.strategy_id
        existing_state = self._health_state_repo.get_for_strategy(strategy_id)
        previous_status = existing_state.health_status if existing_state is not None else None

        history = self._quality_score_repo.get_recent_for_strategy(
            strategy_id, limit=max(policy.min_history_points_for_decay_detection + 2, 10)
        )

        if len(history) < policy.min_history_points_for_decay_detection:
            return HealthEvaluationResult(
                strategy_id=strategy_id,
                previous_health_status=previous_status,
                new_health_status=StrategyHealthStatus.HEALTHY,
                health_reason=(
                    f"insufficient history: {len(history)} points < "
                    f"{policy.min_history_points_for_decay_detection} required"
                ),
                quality_score=history[0].quality_score if history else None,
                quality_score_trend=_TREND_INSUFFICIENT,
                consecutive_decline_count=0,
                realized_drawdown=None,
                alert_emitted=None,
                skipped=True,
                skip_reason="insufficient_history",
                evaluated_at=now,
                rebalance_run_id=rebalance_run_id,
            )

        latest = history[0]
        quality_score = latest.quality_score
        trend = self._compute_trend(history)
        consecutive_decline = self._consecutive_decline_count(history)

        live_snapshot = self._live_perf_service.get_latest(strategy_id)
        realized_drawdown = live_snapshot.realized_drawdown if live_snapshot is not None else None

        strategy_health_quality_decline_streak.record(
            consecutive_decline, {"strategy_id": strategy_id}
        )

        new_status, reason, alert_type = self._classify_health(
            strategy_id=strategy_id,
            quality_score=quality_score,
            consecutive_decline=consecutive_decline,
            trend=trend,
            realized_drawdown=realized_drawdown,
            policy=policy,
            previous_status=previous_status,
        )

        # Metrics
        if new_status == StrategyHealthStatus.DEGRADING:
            strategy_health_degrading_total.add(1, {"strategy_id": strategy_id})
        elif new_status == StrategyHealthStatus.CRITICAL:
            strategy_health_critical_total.add(1, {"strategy_id": strategy_id})

        return HealthEvaluationResult(
            strategy_id=strategy_id,
            previous_health_status=previous_status,
            new_health_status=new_status,
            health_reason=reason,
            quality_score=quality_score,
            quality_score_trend=trend,
            consecutive_decline_count=consecutive_decline,
            realized_drawdown=realized_drawdown,
            alert_emitted=alert_type,
            skipped=False,
            skip_reason=None,
            evaluated_at=now,
            rebalance_run_id=rebalance_run_id,
        )

    def _classify_health(
        self,
        *,
        strategy_id: str,
        quality_score: float,
        consecutive_decline: int,
        trend: str,
        realized_drawdown: float | None,
        policy: HealthPolicyConfig,
        previous_status: str | None,
    ) -> tuple[str, str, str | None]:
        # --- Drawdown critical breach ---
        if (
            realized_drawdown is not None
            and abs(realized_drawdown) > policy.live_drawdown_critical_pct
        ):
            return (
                StrategyHealthStatus.CRITICAL,
                (
                    f"realized drawdown {realized_drawdown:.2%} exceeds critical threshold "
                    f"{policy.live_drawdown_critical_pct:.2%}"
                ),
                HEALTH_EVENT_DRAWDOWN_BREACH,
            )

        # --- Quality score critical threshold ---
        if quality_score < policy.quality_score_critical_threshold:
            return (
                StrategyHealthStatus.CRITICAL,
                (
                    f"quality score {quality_score:.4f} below critical threshold "
                    f"{policy.quality_score_critical_threshold}"
                ),
                HEALTH_EVENT_CRITICAL,
            )

        # --- Drawdown warning ---
        if (
            realized_drawdown is not None
            and abs(realized_drawdown) > policy.live_drawdown_warning_pct
        ):
            return (
                StrategyHealthStatus.DEGRADING,
                (
                    f"realized drawdown {realized_drawdown:.2%} exceeds warning threshold "
                    f"{policy.live_drawdown_warning_pct:.2%}"
                ),
                HEALTH_EVENT_DEGRADING,
            )

        # --- Consecutive decline ---
        if consecutive_decline >= policy.quality_decline_cycles_threshold:
            return (
                StrategyHealthStatus.DEGRADING,
                (
                    f"quality score declined for {consecutive_decline} consecutive cycles "
                    f"(threshold: {policy.quality_decline_cycles_threshold})"
                ),
                HEALTH_EVENT_DEGRADING,
            )

        # --- Quality score warning threshold ---
        if quality_score < policy.quality_score_warning_threshold:
            return (
                StrategyHealthStatus.WATCH,
                (
                    f"quality score {quality_score:.4f} below warning threshold "
                    f"{policy.quality_score_warning_threshold}"
                ),
                None,
            )

        # --- Recovery detection ---
        was_degraded = previous_status in (
            StrategyHealthStatus.DEGRADING,
            StrategyHealthStatus.CRITICAL,
            StrategyHealthStatus.WATCH,
        )
        alert_type = HEALTH_EVENT_RECOVERED if was_degraded else None

        return (
            StrategyHealthStatus.HEALTHY,
            f"quality score {quality_score:.4f} within healthy range; trend={trend}",
            alert_type,
        )

    # ------------------------------------------------------------------
    # Helpers

    def _compute_trend(self, history: list[StrategyQualityScoreHistory]) -> str:
        if len(history) < 2:
            return _TREND_INSUFFICIENT
        newest = history[0].quality_score
        oldest = history[-1].quality_score
        delta = newest - oldest
        if delta > 0.01:
            return _TREND_UP
        if delta < -0.01:
            return _TREND_DOWN
        return _TREND_FLAT

    def _consecutive_decline_count(self, history: list[StrategyQualityScoreHistory]) -> int:
        # history is ordered newest-first; count leading declines
        count = 0
        for i in range(len(history) - 1):
            if history[i].quality_score < history[i + 1].quality_score:
                count += 1
            else:
                break
        return count

    def _persist_health_state(
        self,
        *,
        eval_result: HealthEvaluationResult,
        now: datetime,
    ) -> None:
        existing = self._health_state_repo.get_for_strategy(eval_result.strategy_id)
        transition_at = (
            now
            if eval_result.previous_health_status != eval_result.new_health_status
            else (existing.last_transition_at if existing is not None else now)
        )
        row = StrategyHealthStateRow(
            health_id=str(uuid4()),
            strategy_id=eval_result.strategy_id,
            health_status=eval_result.new_health_status,
            previous_health_status=eval_result.previous_health_status,
            health_reason=eval_result.health_reason,
            consecutive_decline_count=eval_result.consecutive_decline_count,
            quality_score_trend=eval_result.quality_score_trend,
            latest_quality_score=eval_result.quality_score,
            realized_drawdown=eval_result.realized_drawdown,
            last_health_evaluated_at=eval_result.evaluated_at,
            last_transition_at=transition_at,
            evaluation_count=1,
            last_rebalance_run_id=eval_result.rebalance_run_id,
            created_at=now,
            updated_at=now,
        )
        self._health_state_repo.upsert(row)

    def _emit_health_alert(
        self,
        *,
        eval_result: HealthEvaluationResult,
        policy: HealthPolicyConfig,
        now: datetime,
    ) -> None:
        if policy.mode == "observe":
            logger.info(
                "strategy_health_monitor.alert_observe_only",
                extra={
                    "strategy_id": eval_result.strategy_id,
                    "event_type": eval_result.alert_emitted,
                    "health_status": eval_result.new_health_status,
                },
            )
            return

        metadata: dict[str, Any] = {
            "strategy_id": eval_result.strategy_id,
            "previous_health_status": eval_result.previous_health_status,
            "new_health_status": eval_result.new_health_status,
            "quality_score": eval_result.quality_score,
            "score_trend": eval_result.quality_score_trend,
            "consecutive_decline_count": eval_result.consecutive_decline_count,
            "realized_drawdown": eval_result.realized_drawdown,
            "threshold": None,
            "evaluated_at": eval_result.evaluated_at.isoformat(),
            "rebalance_run_id": eval_result.rebalance_run_id,
            "mode": policy.mode,
        }
        self._audit_log_repo.record_operator_action(
            action=eval_result.alert_emitted or HEALTH_EVENT_DEGRADING,
            actor=HEALTH_MONITOR_ACTOR,
            reason=eval_result.health_reason,
            occurred_at=now,
            component="governance",
            metadata=metadata,
        )

    def _trigger_auto_demotion_hook(
        self,
        *,
        eval_result: HealthEvaluationResult,
        now: datetime,
    ) -> None:
        from autonomous_trading_platform.application.services.auto_demotion_service import (
            AutoDemotionService,
        )

        logger.warning(
            "strategy_health_monitor.critical_health_demotion_hook",
            extra={
                "strategy_id": eval_result.strategy_id,
                "health_reason": eval_result.health_reason,
            },
        )
        try:
            AutoDemotionService(session=self._session).run(
                run_id=eval_result.rebalance_run_id,
                actor=HEALTH_MONITOR_ACTOR,
            )
        except Exception as exc:
            logger.warning(
                "strategy_health_monitor.demotion_hook_failed",
                extra={"strategy_id": eval_result.strategy_id, "error": str(exc)},
                exc_info=True,
            )

    def _load_monitorable_strategies(self) -> list[StrategyGovernance]:
        rows = self._session.scalars(
            select(StrategyGovernance)
            .where(StrategyGovernance.current_state.in_(_MONITORABLE_STATES))
            .order_by(StrategyGovernance.strategy_id.asc(), StrategyGovernance.updated_at.desc())
        ).all()
        latest_by_strategy: dict[str, StrategyGovernance] = {}
        for row in rows:
            latest_by_strategy.setdefault(row.strategy_id, row)
        return list(latest_by_strategy.values())

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

    def _load_policy(self) -> HealthPolicyConfig:
        settings = self._operator_settings_repo.get_or_create_default()
        auto_demote = bool(getattr(settings, "auto_demote_on_breach", False))
        return HealthPolicyConfig(
            health_monitor_enabled=True,
            auto_demote_on_critical=auto_demote,
        )

    def _audit_run(
        self,
        *,
        result: HealthMonitorRunResult,
        occurred_at: datetime,
    ) -> None:
        self._audit_log_repo.record_operator_action(
            action=result.audit_event_type,
            actor=HEALTH_MONITOR_ACTOR,
            reason=result.audit_event_type.lower().replace("_", " "),
            occurred_at=occurred_at,
            component="governance",
            metadata=self.result_to_jsonable(result),
        )

    @staticmethod
    def result_to_jsonable(result: HealthMonitorRunResult) -> dict[str, Any]:
        return {
            "run_id": result.run_id,
            "mode": result.mode,
            "health_monitor_enabled": result.health_monitor_enabled,
            "strategies_evaluated": result.strategies_evaluated,
            "transitions": result.transitions,
            "alerts_emitted": result.alerts_emitted,
            "skipped_count": result.skipped_count,
            "duration_seconds": result.duration_seconds,
            "results": [
                {
                    "strategy_id": r.strategy_id,
                    "previous_health_status": r.previous_health_status,
                    "new_health_status": r.new_health_status,
                    "health_reason": r.health_reason,
                    "quality_score": r.quality_score,
                    "quality_score_trend": r.quality_score_trend,
                    "consecutive_decline_count": r.consecutive_decline_count,
                    "realized_drawdown": r.realized_drawdown,
                    "alert_emitted": r.alert_emitted,
                    "skipped": r.skipped,
                    "skip_reason": r.skip_reason,
                }
                for r in result.results
            ],
        }
