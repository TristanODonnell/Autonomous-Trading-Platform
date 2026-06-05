"""Risk domain replay hook."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.drawdown_governance_service import (
    DrawdownGovernanceService,
)
from autonomous_trading_platform.application.services.risk_budgeting_service import (
    RiskBudgetConfig,
    RiskBudgetingService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    RiskReplayResult,
    RiskSummary,
)
from autonomous_trading_platform.contracts.runtime.risk_budget import AllocationMode
from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskSnapshotService,
)
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_state_repository import (
    DrawdownGovernanceLadderStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.risk_snapshot_repository import (
    RiskSnapshotRepository,
)


def run_risk_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dry_run: bool = False,
) -> RiskReplayResult:
    """Compute risk snapshot, evaluate drawdown ladder, run budget cycle."""
    base = dict(
        domain="risk",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if dry_run or replay_context.dry_run:
        return RiskReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    warnings: list[str] = []
    risk_snapshot_id: str | None = None
    is_blocked = False
    block_reasons: list[str] = []
    drawdown_pct: float | None = None
    drawdown_ladder_states: dict[str, str] = {}

    # 1. Compute risk snapshot
    try:
        snapshot = RiskSnapshotService().compute_snapshot(
            run_id=replay_context.run_id,
            timestamp=timestamp,
            position_snapshot=None,
            cash_snapshot=None,
        )
        row = RiskSnapshotRepository(session).upsert(snapshot)
        session.flush()
        risk_snapshot_id = str(row.snapshot_id)
        is_blocked = bool(row.is_blocked)
        block_reasons = list(row.block_reasons or [])
        drawdown_pct = row.drawdown_pct
        if is_blocked:
            warnings.append(f"Risk snapshot blocked: {block_reasons}")
    except Exception as exc:
        warnings.append(f"Risk snapshot failed: {exc}")

    # 2. Evaluate drawdown governance ladder
    try:
        dg_result = DrawdownGovernanceService(session=session).run(
            run_id=str(replay_context.run_id)
        )
        for transition in dg_result.transitions or []:
            strategy_id = transition.get("strategy_id", "?")
            to_state = transition.get("to_state", "?")
            drawdown_ladder_states[strategy_id] = to_state
    except Exception as exc:
        warnings.append(f"Drawdown governance evaluation failed: {exc}")

    # 3. Advisory risk budget
    try:
        RiskBudgetingService(
            session=session,
            config=RiskBudgetConfig(mode=AllocationMode.EQUAL_RISK_CONTRIBUTION),
        ).compute(strategy_ids=[])
        session.rollback()
    except Exception:
        session.rollback()

    # Read current drawdown states for summary
    try:
        all_states = DrawdownGovernanceLadderStateRepository(session).get_all()
        for row in all_states:
            drawdown_ladder_states.setdefault(row.strategy_id, row.ladder_state)
    except Exception:
        pass

    return RiskReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        risk_snapshot_id=risk_snapshot_id,
        is_blocked=is_blocked,
        block_reasons=block_reasons,
        drawdown_pct=drawdown_pct,
        drawdown_ladder_states=drawdown_ladder_states,
        summary={
            "risk_snapshot_id": risk_snapshot_id,
            "is_blocked": is_blocked,
            "block_reasons": block_reasons,
            "drawdown_pct": drawdown_pct,
            "drawdown_ladder_state_count": len(drawdown_ladder_states),
            "timestamp": timestamp.isoformat(),
        },
    )


def snapshot_risk_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> RiskReplayResult:
    """Read latest risk state without computing new values."""
    base = dict(
        domain="risk",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )
    repo = RiskSnapshotRepository(session)
    row = repo.get_latest()
    if row is None:
        return RiskReplayResult(**base, status="skipped", warnings=["No risk snapshot found"])

    ladder_repo = DrawdownGovernanceLadderStateRepository(session)
    ladder_states = {r.strategy_id: r.ladder_state for r in ladder_repo.get_all()}

    return RiskReplayResult(
        **base,
        status="ok",
        risk_snapshot_id=str(row.snapshot_id),
        is_blocked=bool(row.is_blocked),
        block_reasons=list(row.block_reasons or []),
        drawdown_pct=row.drawdown_pct,
        drawdown_ladder_states=ladder_states,
        summary={
            "risk_snapshot_id": str(row.snapshot_id),
            "is_blocked": row.is_blocked,
            "block_reasons": row.block_reasons,
            "drawdown_pct": row.drawdown_pct,
        },
    )


def build_risk_summary(*, session: Session) -> RiskSummary:
    """Read latest risk state for the platform artifact bundle."""
    repo = RiskSnapshotRepository(session)
    row = repo.get_latest()

    ladder_repo = DrawdownGovernanceLadderStateRepository(session)
    ladder_states = {r.strategy_id: r.ladder_state for r in ladder_repo.get_all()}

    budget_row = RiskBudgetingService(session=session).get_latest_snapshot()

    return RiskSummary(
        gross_exposure=str(row.gross_exposure) if row else None,
        net_exposure=str(row.net_exposure) if row else None,
        drawdown_pct=row.drawdown_pct if row else None,
        is_blocked=bool(row.is_blocked) if row else False,
        block_reasons=list(row.block_reasons or []) if row else [],
        drawdown_ladder_states=ladder_states,
        budget_mode=budget_row.mode if budget_row else None,
    )
