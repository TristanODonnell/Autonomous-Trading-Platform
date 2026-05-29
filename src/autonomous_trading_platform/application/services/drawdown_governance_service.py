from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.governance_audit_service import (
    GovernanceAuditService,
)
from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
)
from autonomous_trading_platform.contracts.governance.drawdown_governance import (
    LADDER_HEALTH_SUGGESTIONS,
    DrawdownGovernanceLadderConfig,
    DrawdownGovernanceLadderEvaluation,
    DrawdownGovernanceLadderRunResult,
    DrawdownGovernanceState,
    ladder_severity,
    ladder_state_one_step_better,
)
from autonomous_trading_platform.contracts.governance.governance_audit import TriggerSource
from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    drawdown_governance_breaches_total,
    drawdown_governance_recoveries_total,
    drawdown_governance_state,
    drawdown_governance_utilization,
    strategy_allocation_scalar,
)
from autonomous_trading_platform.storage.sor.models.drawdown_governance_ladder_state import (
    DrawdownGovernanceLadderStateRow,
)
from autonomous_trading_platform.storage.sor.models.drawdown_governance_ladder_transition import (
    DrawdownGovernanceLadderTransitionRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_state_repository import (
    DrawdownGovernanceLadderStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_transition_repository import (
    DrawdownGovernanceLadderTransitionRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

logger = get_logger(__name__)

_LADDER_ACTOR = "system_drawdown_governance"

# Governance states eligible for drawdown ladder monitoring
_MONITORABLE_STATES = {
    "approved_for_paper_trading",
    "approved_paper",
    "approved_for_live_trading",
    "approved_live",
}

# Structured audit event names
EVENT_WARNING = "DRAWDOWN_WARNING_TRIGGERED"
EVENT_PROBATION = "DRAWDOWN_PROBATION_TRIGGERED"
EVENT_SUSPENDED = "DRAWDOWN_SUSPENDED"
EVENT_BREACHED = "DRAWDOWN_LIMIT_BREACHED"
EVENT_RECOVERY = "DRAWDOWN_RECOVERY_DETECTED"


# ---------------------------------------------------------------------------
# Pure helper functions (no I/O, fully testable)
# ---------------------------------------------------------------------------


def compute_drawdown_utilization(
    realized_drawdown: float,
    max_drawdown_allowed: float,
) -> float:
    """Compute drawdown_utilization = realized_drawdown / max_drawdown_allowed."""
    if max_drawdown_allowed <= 0.0:
        return 1.0  # treat as fully breached if limit is unconfigured
    return realized_drawdown / max_drawdown_allowed


def compute_target_ladder_state(
    utilization: float,
    config: DrawdownGovernanceLadderConfig,
) -> tuple[str, str]:
    """
    Pure function: classify ladder state from utilization.
    Returns (state, triggering_reason).
    Evaluated in severity order: BREACHED → SUSPENDED → PROBATION → WARNING → NORMAL.
    """
    if utilization >= config.breach_threshold:
        return (
            DrawdownGovernanceState.BREACHED,
            f"drawdown_utilization={utilization:.2%} >= breach={config.breach_threshold:.2%}",
        )
    if utilization >= config.suspension_threshold:
        return (
            DrawdownGovernanceState.SUSPENDED,
            f"drawdown_utilization={utilization:.2%} >= suspension={config.suspension_threshold:.2%}",
        )
    if utilization >= config.probation_threshold:
        return (
            DrawdownGovernanceState.PROBATION,
            f"drawdown_utilization={utilization:.2%} >= probation={config.probation_threshold:.2%}",
        )
    if utilization >= config.warning_threshold:
        return (
            DrawdownGovernanceState.WARNING,
            f"drawdown_utilization={utilization:.2%} >= warning={config.warning_threshold:.2%}",
        )
    return DrawdownGovernanceState.NORMAL, "drawdown_utilization within normal range"


def _recovery_threshold_for(state: str, config: DrawdownGovernanceLadderConfig) -> float:
    """
    Return the utilization that must be UNDERSHOT (with hysteresis) before
    recovery from `state` to the rung below it is permitted.
    """
    match state:
        case DrawdownGovernanceState.BREACHED:
            return config.breach_threshold - config.hysteresis_band
        case DrawdownGovernanceState.SUSPENDED:
            return config.suspension_threshold - config.hysteresis_band
        case DrawdownGovernanceState.PROBATION:
            return config.probation_threshold - config.hysteresis_band
        case DrawdownGovernanceState.WARNING:
            return config.warning_threshold - config.hysteresis_band
        case _:
            return 0.0


def _cooldown_expiry(
    to_state: str, now: datetime, config: DrawdownGovernanceLadderConfig
) -> datetime | None:
    hours = config.cooldown_hours_for(to_state)
    if hours <= 0.0:
        return None
    return now + timedelta(hours=hours)


def apply_transition_rules(
    current_state: str,
    raw_target: str,
    utilization: float,
    cooldown_expires_at: datetime | None,
    evaluation_count: int,
    operator_ack_required: bool,
    now: datetime,
    config: DrawdownGovernanceLadderConfig,
) -> tuple[str, str, bool]:
    """
    Apply anti-flapping and operator-ack rules; return (actual_state, reason, blocked).

    Rules:
    - Escalation: always allowed (safety > anti-flapping), subject to
      min_observation_cycles guard on the very first escalation from NORMAL.
    - BREACHED + breach_requires_operator_ack: recovery blocked until ack cleared.
    - Recovery: blocked during cooldown; requires hysteresis (utilization must
      fall below threshold − hysteresis_band); one step at a time.
    """
    if raw_target == current_state:
        return current_state, "no change", False

    is_escalation = ladder_severity(raw_target) > ladder_severity(current_state)
    in_cooldown = cooldown_expires_at is not None and now < cooldown_expires_at

    if is_escalation:
        if (
            current_state == DrawdownGovernanceState.NORMAL
            and evaluation_count < config.min_observation_cycles
        ):
            return (
                current_state,
                f"escalation deferred: observation_count={evaluation_count} < min={config.min_observation_cycles}",
                True,
            )
        return raw_target, f"utilization_threshold_breach → {raw_target}", False

    # Recovery path
    if current_state == DrawdownGovernanceState.BREACHED and operator_ack_required:
        return (
            current_state,
            "recovery blocked: breach requires operator acknowledgement",
            True,
        )

    if in_cooldown:
        cooldown_str = cooldown_expires_at.isoformat() if cooldown_expires_at else "unknown"
        return (
            current_state,
            f"recovery blocked: in_cooldown until {cooldown_str}",
            True,
        )

    recovery_threshold = _recovery_threshold_for(current_state, config)
    if utilization >= recovery_threshold:
        return (
            current_state,
            f"recovery blocked: utilization={utilization:.2%} >= recovery_threshold={recovery_threshold:.2%} (hysteresis)",
            True,
        )

    step_target = ladder_state_one_step_better(current_state)
    return step_target, f"recovery → {step_target} (utilization={utilization:.2%})", False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DrawdownGovernanceService:
    """
    Manages the per-strategy drawdown governance ladder independently from
    both the strategy health lifecycle and governance approval state.

    Governance approval answers:  "Is this strategy permitted to trade?"
    Health lifecycle answers:     "Is this strategy currently performing?"
    Drawdown ladder answers:      "How much capital risk should this strategy carry?"

    The three are strictly separated. This service:
      - evaluates drawdown utilization for each monitorable strategy
      - places the strategy on the appropriate ladder rung
      - computes an allocation_scalar that downstream services apply
      - emits structured audit events and OTel metrics
      - persists current rung state and an append-only transition history

    State machine (severity ascending):
        normal ← → warning ← → probation ← → suspended → breached

    Anti-flapping:
        - Cooldown windows block recovery transitions after any rung change
        - Escalation always allowed (safety > anti-flapping), subject to
          min_observation_cycles on the first escalation from NORMAL
        - Recovery requires hysteresis: utilization must fall below
          (threshold − hysteresis_band) before downward transition is allowed
        - Recovery is one step at a time
        - BREACHED recovery may require explicit operator acknowledgement
    """

    def __init__(
        self,
        *,
        session: Session,
        ladder_state_repo: DrawdownGovernanceLadderStateRepository | None = None,
        transition_repo: DrawdownGovernanceLadderTransitionRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
        operator_settings_repo: OperatorSettingsRepository | None = None,
        live_perf_service: LivePerformanceMetricsService | None = None,
        governance_audit_service: GovernanceAuditService | None = None,
    ) -> None:
        self._session = session
        self._ladder_state_repo = ladder_state_repo or DrawdownGovernanceLadderStateRepository(
            session
        )
        self._transition_repo = transition_repo or DrawdownGovernanceLadderTransitionRepository(
            session
        )
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._live_perf_service = live_perf_service or LivePerformanceMetricsService(session)
        self._governance_audit_service = governance_audit_service or GovernanceAuditService(
            session=session,
            audit_log_repo=self._audit_log_repo,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        run_id: str | None = None,
        config: DrawdownGovernanceLadderConfig | None = None,
    ) -> DrawdownGovernanceLadderRunResult:
        """Evaluate the drawdown governance ladder for all monitorable strategies."""
        start = perf_counter()
        now = datetime.now(UTC)
        run_id = run_id or str(uuid4())
        resolved_config = config or self._load_config()

        if not resolved_config.enabled:
            result = DrawdownGovernanceLadderRunResult(
                run_id=run_id,
                mode=resolved_config.mode,
                enabled=False,
                strategies_evaluated=0,
                transitions=[],
                warnings_triggered=0,
                probations_triggered=0,
                suspensions_triggered=0,
                breaches_triggered=0,
                recoveries_triggered=0,
                skipped_count=0,
                results=[],
                duration_seconds=perf_counter() - start,
            )
            self._audit_run(result=result, occurred_at=now)
            self._session.flush()
            self._session.commit()
            return result

        logger.info(
            "drawdown_governance_ladder.run_started",
            extra={"run_id": run_id, "mode": resolved_config.mode},
        )

        governance_rows = self._load_monitorable_strategies()
        evals: list[DrawdownGovernanceLadderEvaluation] = []
        transitions: list[dict[str, Any]] = []
        warnings = probations = suspensions = breaches = recoveries = 0

        for row in governance_rows:
            eval_result = self._evaluate_strategy(
                governance_row=row,
                run_id=run_id,
                config=resolved_config,
                now=now,
            )
            evals.append(eval_result)

            if eval_result.skipped:
                continue

            if eval_result.did_transition:
                new_s = eval_result.new_state
                prev_s = eval_result.previous_state
                is_recovery = ladder_severity(new_s) < ladder_severity(prev_s)
                if is_recovery:
                    recoveries += 1
                elif new_s == DrawdownGovernanceState.WARNING:
                    warnings += 1
                elif new_s == DrawdownGovernanceState.PROBATION:
                    probations += 1
                elif new_s == DrawdownGovernanceState.SUSPENDED:
                    suspensions += 1
                elif new_s == DrawdownGovernanceState.BREACHED:
                    breaches += 1

                transitions.append(
                    {
                        "strategy_id": eval_result.strategy_id,
                        "from_state": eval_result.previous_state,
                        "to_state": eval_result.new_state,
                        "reason": eval_result.transition_reason,
                        "drawdown_utilization": eval_result.drawdown_utilization,
                        "allocation_scalar": eval_result.allocation_scalar,
                    }
                )

        result = DrawdownGovernanceLadderRunResult(
            run_id=run_id,
            mode=resolved_config.mode,
            enabled=True,
            strategies_evaluated=len(evals),
            transitions=transitions,
            warnings_triggered=warnings,
            probations_triggered=probations,
            suspensions_triggered=suspensions,
            breaches_triggered=breaches,
            recoveries_triggered=recoveries,
            skipped_count=sum(1 for e in evals if e.skipped),
            results=evals,
            duration_seconds=perf_counter() - start,
        )

        self._audit_run(result=result, occurred_at=now)
        self._session.flush()
        self._session.commit()

        logger.info(
            "drawdown_governance_ladder.run_completed",
            extra={
                "run_id": run_id,
                "strategies_evaluated": result.strategies_evaluated,
                "transitions": len(transitions),
                "breaches": breaches,
                "recoveries": recoveries,
                "duration_seconds": result.duration_seconds,
            },
        )
        return result

    def acknowledge_breach(
        self,
        strategy_id: str,
        *,
        operator: str,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        """
        Allow a human operator to acknowledge a BREACHED strategy so that the
        cooldown/recovery path can proceed.  Returns True if the ack was applied.
        """
        now = now or datetime.now(UTC)
        row = self._ladder_state_repo.get_for_strategy(strategy_id)
        if row is None or not row.operator_ack_required:
            return False

        row.operator_ack_required = False
        row.operator_acknowledged_at = now
        row.operator_acknowledged_by = operator
        row.updated_at = now
        self._session.flush()

        self._insert_transition(
            strategy_id=strategy_id,
            from_state=row.ladder_state,
            to_state=row.ladder_state,
            reason=f"operator_ack by {operator}: {reason}",
            triggered_by=f"operator:{operator}",
            drawdown_utilization=row.drawdown_utilization,
            realized_drawdown=row.realized_drawdown,
            max_drawdown_allowed=row.max_drawdown_allowed,
            allocation_scalar_after=row.allocation_scalar,
            cooldown_expires_at=row.cooldown_expires_at,
            run_id=None,
            now=now,
        )
        self._session.flush()
        self._session.commit()
        logger.info(
            "drawdown_governance_ladder.breach_acknowledged",
            extra={"strategy_id": strategy_id, "operator": operator},
        )
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_monitorable_strategies(self) -> list[StrategyGovernance]:
        return list(
            self._session.scalars(
                select(StrategyGovernance).where(
                    StrategyGovernance.current_state.in_(_MONITORABLE_STATES)
                )
            ).all()
        )

    def _load_config(self) -> DrawdownGovernanceLadderConfig:
        """Load ladder config from operator settings; fall back to defaults."""
        try:
            settings = self._operator_settings_repo.get_current()
            if settings is None:
                return DrawdownGovernanceLadderConfig()

            return DrawdownGovernanceLadderConfig(
                enabled=getattr(settings, "drawdown_ladder_enabled", True),
                mode=getattr(settings, "drawdown_ladder_mode", "observe"),
                warning_threshold=getattr(settings, "drawdown_ladder_warning_threshold", 0.50),
                probation_threshold=getattr(settings, "drawdown_ladder_probation_threshold", 0.75),
                suspension_threshold=getattr(
                    settings, "drawdown_ladder_suspension_threshold", 0.90
                ),
                breach_threshold=getattr(settings, "drawdown_ladder_breach_threshold", 1.00),
                hysteresis_band=getattr(settings, "drawdown_ladder_hysteresis_band", 0.05),
                cooldown_after_warning_hours=getattr(
                    settings, "drawdown_ladder_cooldown_warning_hours", 6.0
                ),
                cooldown_after_probation_hours=getattr(
                    settings, "drawdown_ladder_cooldown_probation_hours", 12.0
                ),
                cooldown_after_suspension_hours=getattr(
                    settings, "drawdown_ladder_cooldown_suspension_hours", 24.0
                ),
                cooldown_after_breach_hours=getattr(
                    settings, "drawdown_ladder_cooldown_breach_hours", 48.0
                ),
                breach_requires_operator_ack=getattr(
                    settings, "drawdown_ladder_breach_requires_operator_ack", True
                ),
                auto_demote_on_breach=getattr(
                    settings, "drawdown_ladder_auto_demote_on_breach", False
                ),
            )
        except Exception:
            return DrawdownGovernanceLadderConfig()

    def _evaluate_strategy(
        self,
        governance_row: StrategyGovernance,
        run_id: str,
        config: DrawdownGovernanceLadderConfig,
        now: datetime,
    ) -> DrawdownGovernanceLadderEvaluation:
        strategy_id = governance_row.strategy_id

        try:
            return self._do_evaluate_strategy(
                governance_row=governance_row,
                run_id=run_id,
                config=config,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "drawdown_governance_ladder.strategy_eval_error",
                extra={"strategy_id": strategy_id, "error": str(exc)},
            )
            return DrawdownGovernanceLadderEvaluation(
                strategy_id=strategy_id,
                realized_drawdown=0.0,
                max_drawdown_allowed=0.0,
                drawdown_utilization=0.0,
                previous_state=DrawdownGovernanceState.NORMAL,
                new_state=DrawdownGovernanceState.NORMAL,
                allocation_scalar=1.0,
                suggested_health_status=LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.NORMAL],
                did_transition=False,
                transition_reason=f"eval_error: {exc}",
                transition_blocked_by_cooldown=False,
                cooldown_expires_at=None,
                operator_ack_required=False,
                auto_demotion_triggered=False,
                skipped=True,
                skip_reason=f"eval_error: {type(exc).__name__}",
                evaluated_at=now,
            )

    def _do_evaluate_strategy(
        self,
        governance_row: StrategyGovernance,
        run_id: str,
        config: DrawdownGovernanceLadderConfig,
        now: datetime,
    ) -> DrawdownGovernanceLadderEvaluation:
        strategy_id = governance_row.strategy_id

        live_snapshot = self._live_perf_service.get_latest(strategy_id)
        raw_realized_drawdown: float | None = (
            live_snapshot.realized_drawdown if live_snapshot is not None else None
        )
        # Use absolute value — snapshots may store drawdown as negative (loss convention)
        realized_drawdown: float | None = (
            abs(raw_realized_drawdown) if raw_realized_drawdown is not None else None
        )

        settings = self._operator_settings_repo.get_or_create_default()
        max_drawdown_allowed: float | None = (
            float(settings.max_strategy_drawdown) if settings is not None else None
        )

        if realized_drawdown is None or max_drawdown_allowed is None or max_drawdown_allowed <= 0:
            return DrawdownGovernanceLadderEvaluation(
                strategy_id=strategy_id,
                realized_drawdown=realized_drawdown or 0.0,
                max_drawdown_allowed=max_drawdown_allowed or 0.0,
                drawdown_utilization=0.0,
                previous_state=DrawdownGovernanceState.NORMAL,
                new_state=DrawdownGovernanceState.NORMAL,
                allocation_scalar=config.allocation_scalar_for(DrawdownGovernanceState.NORMAL),
                suggested_health_status=LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.NORMAL],
                did_transition=False,
                transition_reason="no_drawdown_data",
                transition_blocked_by_cooldown=False,
                cooldown_expires_at=None,
                operator_ack_required=False,
                auto_demotion_triggered=False,
                skipped=True,
                skip_reason="missing_drawdown_data",
                evaluated_at=now,
            )

        utilization = compute_drawdown_utilization(realized_drawdown, max_drawdown_allowed)

        # Load or initialise current state row
        current_row = self._ladder_state_repo.get_for_strategy(strategy_id)
        current_state = current_row.ladder_state if current_row else DrawdownGovernanceState.NORMAL
        cooldown_expires_at = current_row.cooldown_expires_at if current_row else None
        evaluation_count = current_row.evaluation_count if current_row else 0
        operator_ack_required = current_row.operator_ack_required if current_row else False

        raw_target, raw_reason = compute_target_ladder_state(utilization, config)

        actual_state, reason, blocked = apply_transition_rules(
            current_state=current_state,
            raw_target=raw_target,
            utilization=utilization,
            cooldown_expires_at=cooldown_expires_at,
            evaluation_count=evaluation_count,
            operator_ack_required=operator_ack_required,
            now=now,
            config=config,
        )

        did_transition = actual_state != current_state
        new_cooldown_expires_at = cooldown_expires_at  # carry forward unless transition
        if did_transition:
            new_cooldown_expires_at = _cooldown_expiry(actual_state, now, config)

        # BREACHED: set operator ack flag if configured
        new_operator_ack_required = operator_ack_required
        if actual_state == DrawdownGovernanceState.BREACHED and config.breach_requires_operator_ack:
            new_operator_ack_required = True

        allocation_scalar = config.allocation_scalar_for(actual_state)
        suggested_health = LADDER_HEALTH_SUGGESTIONS[actual_state]

        # Determine auto-demotion trigger
        auto_demotion = (
            did_transition
            and actual_state == DrawdownGovernanceState.BREACHED
            and config.auto_demote_on_breach
            and config.mode == "enforce"
        )

        # Persist state if in enforce mode
        if config.mode == "enforce":
            self._persist_state(
                strategy_id=strategy_id,
                current_row=current_row,
                actual_state=actual_state,
                previous_state=current_state,
                reason=reason,
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                drawdown_utilization=utilization,
                allocation_scalar=allocation_scalar,
                new_cooldown_expires_at=new_cooldown_expires_at,
                operator_ack_required=new_operator_ack_required,
                did_transition=did_transition,
                run_id=run_id,
                now=now,
            )

        # Emit OTel metrics regardless of mode
        drawdown_governance_utilization.record(utilization, {"strategy_id": strategy_id})
        drawdown_governance_state.record(
            ladder_severity(actual_state), {"strategy_id": strategy_id, "state": actual_state}
        )
        strategy_allocation_scalar.record(
            allocation_scalar, {"strategy_id": strategy_id, "source": "drawdown_ladder"}
        )
        if did_transition and actual_state == DrawdownGovernanceState.BREACHED:
            drawdown_governance_breaches_total.add(1, {"strategy_id": strategy_id})
        if did_transition and ladder_severity(actual_state) < ladder_severity(current_state):
            drawdown_governance_recoveries_total.add(1, {"strategy_id": strategy_id})

        # Emit structured audit events for state changes
        if did_transition:
            self._emit_transition_event(
                strategy_id=strategy_id,
                from_state=current_state,
                to_state=actual_state,
                reason=reason,
                utilization=utilization,
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                allocation_scalar=allocation_scalar,
                now=now,
            )
            self._governance_audit_service.record_drawdown_transition(
                strategy_id=strategy_id,
                from_state=current_state,
                to_state=actual_state,
                actor=_LADDER_ACTOR,
                trigger_source=TriggerSource.DRAWDOWN_GOVERNANCE,
                transition_reason=reason,
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                drawdown_utilization=utilization,
                allocation_scalar_after=allocation_scalar,
                evaluation_run_id=run_id,
                now=now,
            )

        return DrawdownGovernanceLadderEvaluation(
            strategy_id=strategy_id,
            realized_drawdown=realized_drawdown,
            max_drawdown_allowed=max_drawdown_allowed,
            drawdown_utilization=utilization,
            previous_state=current_state,
            new_state=actual_state,
            allocation_scalar=allocation_scalar,
            suggested_health_status=suggested_health,
            did_transition=did_transition,
            transition_reason=reason,
            transition_blocked_by_cooldown=blocked,
            cooldown_expires_at=new_cooldown_expires_at,
            operator_ack_required=new_operator_ack_required,
            auto_demotion_triggered=auto_demotion,
            skipped=False,
            skip_reason=None,
            evaluated_at=now,
        )

    def _persist_state(
        self,
        *,
        strategy_id: str,
        current_row: DrawdownGovernanceLadderStateRow | None,
        actual_state: str,
        previous_state: str,
        reason: str,
        realized_drawdown: float,
        max_drawdown_allowed: float,
        drawdown_utilization: float,
        allocation_scalar: float,
        new_cooldown_expires_at: datetime | None,
        operator_ack_required: bool,
        did_transition: bool,
        run_id: str,
        now: datetime,
    ) -> None:
        if current_row is None:
            new_row = DrawdownGovernanceLadderStateRow(
                ladder_state_id=str(uuid4()),
                strategy_id=strategy_id,
                ladder_state=actual_state,
                previous_ladder_state=None,
                transition_reason=reason,
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                drawdown_utilization=drawdown_utilization,
                allocation_scalar=allocation_scalar,
                cooldown_expires_at=new_cooldown_expires_at,
                evaluation_count=1,
                operator_ack_required=operator_ack_required,
                operator_acknowledged_at=None,
                operator_acknowledged_by=None,
                last_transition_at=now if did_transition else None,
                last_evaluated_at=now,
                created_at=now,
                updated_at=now,
            )
            self._ladder_state_repo.upsert(new_row)
        else:
            current_row.ladder_state = actual_state
            current_row.previous_ladder_state = previous_state
            current_row.transition_reason = reason
            current_row.realized_drawdown = realized_drawdown
            current_row.max_drawdown_allowed = max_drawdown_allowed
            current_row.drawdown_utilization = drawdown_utilization
            current_row.allocation_scalar = allocation_scalar
            current_row.cooldown_expires_at = new_cooldown_expires_at
            current_row.operator_ack_required = operator_ack_required
            current_row.evaluation_count = (current_row.evaluation_count or 0) + 1
            if did_transition:
                current_row.last_transition_at = now
            current_row.last_evaluated_at = now
            current_row.updated_at = now
            self._session.flush()

        if did_transition:
            self._insert_transition(
                strategy_id=strategy_id,
                from_state=previous_state,
                to_state=actual_state,
                reason=reason,
                triggered_by=_LADDER_ACTOR,
                drawdown_utilization=drawdown_utilization,
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                allocation_scalar_after=allocation_scalar,
                cooldown_expires_at=new_cooldown_expires_at,
                run_id=run_id,
                now=now,
            )

    def _insert_transition(
        self,
        *,
        strategy_id: str,
        from_state: str | None,
        to_state: str,
        reason: str,
        triggered_by: str,
        drawdown_utilization: float | None,
        realized_drawdown: float | None,
        max_drawdown_allowed: float | None,
        allocation_scalar_after: float | None,
        cooldown_expires_at: datetime | None,
        run_id: str | None,
        now: datetime,
    ) -> None:
        row = DrawdownGovernanceLadderTransitionRow(
            transition_id=str(uuid4()),
            strategy_id=strategy_id,
            from_state=from_state,
            to_state=to_state,
            transition_reason=reason,
            triggered_by=triggered_by,
            realized_drawdown=realized_drawdown,
            max_drawdown_allowed=max_drawdown_allowed,
            drawdown_utilization=drawdown_utilization,
            allocation_scalar_after=allocation_scalar_after,
            cooldown_expires_at=cooldown_expires_at,
            triggering_metrics={
                "realized_drawdown": realized_drawdown,
                "max_drawdown_allowed": max_drawdown_allowed,
                "drawdown_utilization": drawdown_utilization,
            },
            evaluation_run_id=run_id,
            created_at=now,
        )
        self._transition_repo.insert(row)

    def _emit_transition_event(
        self,
        *,
        strategy_id: str,
        from_state: str,
        to_state: str,
        reason: str,
        utilization: float,
        realized_drawdown: float,
        max_drawdown_allowed: float,
        allocation_scalar: float,
        now: datetime,
    ) -> None:
        is_recovery = ladder_severity(to_state) < ladder_severity(from_state)
        _event_type_map: dict[str, str] = {
            DrawdownGovernanceState.WARNING: EVENT_WARNING,
            DrawdownGovernanceState.PROBATION: EVENT_PROBATION,
            DrawdownGovernanceState.SUSPENDED: EVENT_SUSPENDED,
            DrawdownGovernanceState.BREACHED: EVENT_BREACHED,
        }
        event_type = EVENT_RECOVERY if is_recovery else _event_type_map.get(to_state, EVENT_WARNING)

        try:
            self._audit_log_repo.add(
                AuditLogEvent(
                    event_id=str(uuid4()),
                    event_type=event_type,
                    component=_LADDER_ACTOR,
                    event_timestamp=now,
                    message=f"drawdown_governance ladder transition {from_state} → {to_state} for {strategy_id}",
                    metadata={
                        "strategy_id": strategy_id,
                        "from_state": from_state,
                        "to_state": to_state,
                        "transition_reason": reason,
                        "drawdown_utilization": utilization,
                        "realized_drawdown": realized_drawdown,
                        "max_drawdown_allowed": max_drawdown_allowed,
                        "allocation_scalar": allocation_scalar,
                    },
                )
            )
        except Exception as exc:
            logger.warning(
                "drawdown_governance_ladder.audit_emit_failed",
                extra={"strategy_id": strategy_id, "error": str(exc)},
            )

    def _audit_run(self, result: DrawdownGovernanceLadderRunResult, occurred_at: datetime) -> None:
        try:
            self._audit_log_repo.add(
                AuditLogEvent(
                    event_id=str(uuid4()),
                    event_type="DRAWDOWN_GOVERNANCE_LADDER_RUN",
                    component=_LADDER_ACTOR,
                    event_timestamp=occurred_at,
                    message=f"drawdown_governance ladder run completed ({result.strategies_evaluated} strategies)",
                    metadata={
                        "run_id": result.run_id,
                        "mode": result.mode,
                        "enabled": result.enabled,
                        "strategies_evaluated": result.strategies_evaluated,
                        "transitions_count": len(result.transitions),
                        "breaches": result.breaches_triggered,
                        "recoveries": result.recoveries_triggered,
                        "duration_seconds": result.duration_seconds,
                    },
                )
            )
        except Exception as exc:
            logger.warning(
                "drawdown_governance_ladder.run_audit_failed",
                extra={"run_id": result.run_id, "error": str(exc)},
            )

    @staticmethod
    def result_to_jsonable(result: DrawdownGovernanceLadderRunResult) -> dict[str, Any]:
        return {
            "run_id": result.run_id,
            "mode": result.mode,
            "enabled": result.enabled,
            "strategies_evaluated": result.strategies_evaluated,
            "transitions": result.transitions,
            "warnings_triggered": result.warnings_triggered,
            "probations_triggered": result.probations_triggered,
            "suspensions_triggered": result.suspensions_triggered,
            "breaches_triggered": result.breaches_triggered,
            "recoveries_triggered": result.recoveries_triggered,
            "skipped_count": result.skipped_count,
            "duration_seconds": result.duration_seconds,
        }
