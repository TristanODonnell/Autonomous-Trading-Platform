"""Research domain replay hook (P1 — calendar-scheduled, not per-tick)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_catalog_service import (
    ExperimentCatalogService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    ResearchReplayResult,
    ResearchSummary,
)


def run_research_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    experiment_config,  # ExperimentDefinition — avoid hard import for optional dependency
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> ResearchReplayResult:
    """Run or resume a research experiment scoped to a timestamp window.

    Research is calendar-scheduled (weekly/monthly), not per-tick.
    The platform runner dispatches this on research_event days in the timeline.
    """
    base = dict(
        domain="research",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if dry_run or replay_context.dry_run:
        return ResearchReplayResult(
            **base,
            status="dry_run",
            experiment_id=getattr(experiment_config, "experiment_id", None),
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    try:
        from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
            build_simulation_context,
        )

        simulation_context = build_simulation_context(session=session)
        results, filter_outputs = (
            simulation_context.experiment_orchestration_service.run_experiment(experiment_config)
        )
    except Exception as exc:
        return ResearchReplayResult(**base, status="failed", errors=[str(exc)])

    total_runs = len(results)
    passed = len([o for o in filter_outputs if o.filter_result.passed])

    return ResearchReplayResult(
        **base,
        status="ok",
        experiment_id=experiment_config.experiment_id,
        total_runs=total_runs,
        passed_filters=passed,
        summary={
            "experiment_id": experiment_config.experiment_id,
            "total_runs": total_runs,
            "passed_filters": passed,
            "timestamp": timestamp.isoformat(),
        },
    )


def build_research_summary(*, session: Session) -> ResearchSummary:
    """Read latest research state for the platform artifact bundle."""
    try:
        svc = ExperimentCatalogService(session=session)
        rows = svc.list_experiments()
        if not rows:
            return ResearchSummary(
                experiment_id=None, total_runs=0, passed_filters=0, run_timestamp=None
            )
        latest = rows[0]
        return ResearchSummary(
            experiment_id=latest.get("experiment_name"),
            total_runs=latest.get("total_strategies", 0) or 0,
            passed_filters=latest.get("strategies_passed_filters", 0) or 0,
            run_timestamp=str(latest.get("created_at", "")),
        )
    except Exception:
        return ResearchSummary(
            experiment_id=None, total_runs=0, passed_filters=0, run_timestamp=None
        )
