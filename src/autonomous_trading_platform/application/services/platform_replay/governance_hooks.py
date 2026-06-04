"""Governance domain replay hook."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.auto_demotion_service import (
    AutoDemotionService,
)
from autonomous_trading_platform.application.services.auto_promotion_service import (
    AutoPromotionService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.application.services.strategy_health_lifecycle_service import (
    StrategyHealthLifecycleService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    GovernanceReplayResult,
    GovernanceSummary,
    GovernanceTimelineEvent,
    PlatformReplayContext,
)


def run_governance_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> GovernanceReplayResult:
    """Run auto-promotion, auto-demotion, and health lifecycle for this tick."""
    base = dict(
        domain="governance",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if dry_run or replay_context.dry_run:
        return GovernanceReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    warnings: list[str] = []
    promotions_executed = 0
    demotions_executed = 0
    health_transitions = 0
    strategies_in_breach: list[str] = []
    strategies_evaluated = 0

    run_id_str = str(replay_context.run_id)

    # 1. Auto-promotion
    try:
        promo_result = AutoPromotionService(session=session).run(
            actor=replay_context.actor,
            run_id=run_id_str,
        )
        promotions_executed = len(promo_result.promotions_executed or [])
    except Exception as exc:
        warnings.append(f"Auto-promotion failed: {exc}")

    # 2. Auto-demotion
    try:
        demotion_result = AutoDemotionService(session=session).run(
            actor=replay_context.actor,
            run_id=run_id_str,
            dry_run=False,
        )
        demotions_executed = len(demotion_result.demotions_executed or [])
        for candidate in demotion_result.candidates or []:
            if getattr(candidate, "breach", False):
                strategies_in_breach.append(getattr(candidate, "strategy_id", "?"))
    except Exception as exc:
        warnings.append(f"Auto-demotion failed: {exc}")

    # 3. Health lifecycle
    try:
        health_result = StrategyHealthLifecycleService(session=session).run(
            run_id=run_id_str,
        )
        strategies_evaluated = health_result.strategies_evaluated or 0
        health_transitions = len(health_result.transitions or [])
    except Exception as exc:
        warnings.append(f"Health lifecycle failed: {exc}")

    return GovernanceReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        strategies_evaluated=strategies_evaluated,
        promotions_executed=promotions_executed,
        demotions_executed=demotions_executed,
        health_transitions=health_transitions,
        strategies_in_breach=strategies_in_breach,
        summary={
            "strategies_evaluated": strategies_evaluated,
            "promotions_executed": promotions_executed,
            "demotions_executed": demotions_executed,
            "health_transitions": health_transitions,
            "strategies_in_breach": strategies_in_breach,
            "timestamp": timestamp.isoformat(),
        },
    )


def apply_governance_event(
    *,
    session: Session,
    timestamp: datetime,
    event: GovernanceTimelineEvent,
    replay_context: PlatformReplayContext,
) -> GovernanceReplayResult:
    """Apply a manual governance transition timeline event."""
    base = dict(
        domain="governance",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if replay_context.dry_run:
        return GovernanceReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "event_type": event.event_type},
        )

    if event.event_type == "governance_manual_transition":
        if not event.strategy_id or not event.to_state:
            return GovernanceReplayResult(
                **base,
                status="failed",
                errors=["governance_manual_transition requires strategy_id and to_state"],
            )
        try:
            StrategyGovernanceService(session=session).transition(
                strategy_id=event.strategy_id,
                to_state=event.to_state,
                reason=event.reason,
                updated_by=event.actor,
                actor_role=event.actor_role,
                source_run_id=event.source_run_id,
            )
        except Exception as exc:
            return GovernanceReplayResult(
                **base,
                status="failed",
                errors=[str(exc)],
            )
        return GovernanceReplayResult(
            **base,
            status="ok",
            summary={
                "event_type": event.event_type,
                "strategy_id": event.strategy_id,
                "to_state": event.to_state,
            },
        )

    if event.event_type == "health_review_acknowledged":
        if not event.strategy_id:
            return GovernanceReplayResult(
                **base,
                status="failed",
                errors=["health_review_acknowledged requires strategy_id"],
            )
        try:
            StrategyHealthLifecycleService(session=session).clear_suspension(
                event.strategy_id,
                actor=event.actor,
                reason=event.reason,
            )
        except Exception as exc:
            return GovernanceReplayResult(
                **base,
                status="failed",
                errors=[str(exc)],
            )
        return GovernanceReplayResult(
            **base,
            status="ok",
            summary={"event_type": event.event_type, "strategy_id": event.strategy_id},
        )

    return GovernanceReplayResult(
        **base,
        status="skipped",
        warnings=[f"Unhandled governance event type: {event.event_type}"],
    )


def build_governance_summary(*, session: Session) -> GovernanceSummary:
    """Read current governance state for the platform artifact bundle."""
    from sqlalchemy import select

    from autonomous_trading_platform.storage.sor.models.strategy_governance import (
        StrategyGovernance,
    )
    from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_state_repository import (
        DrawdownGovernanceLadderStateRepository,
    )

    all_rows = list(session.scalars(select(StrategyGovernance)).all())
    in_breach = [
        row.strategy_id
        for row in DrawdownGovernanceLadderStateRepository(session).get_all()
        if row.ladder_state not in ("normal", "warning")
    ]

    return GovernanceSummary(
        strategies_evaluated=len(all_rows),
        promotions_executed=0,
        demotions_executed=0,
        health_transitions=0,
        strategies_in_breach=in_breach,
    )
