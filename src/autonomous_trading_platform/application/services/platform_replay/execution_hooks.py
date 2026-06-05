"""Execution domain replay hook (P1 — read-only snapshot)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.platform_replay import (
    ExecutionReplayResult,
    ExecutionSummary,
    PlatformReplayContext,
)
from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import FillQualityMetrics
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def snapshot_execution_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> ExecutionReplayResult:
    """Read open orders, fill quality, and latest reconciliation state."""
    base = dict(
        domain="execution",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    warnings: list[str] = []
    open_order_count = 0
    fills_this_cycle = 0
    adverse_fills = 0
    reconciliation_status: str | None = None

    try:
        with SorUnitOfWork(session) as uow:
            open_orders = uow.tracked_orders.list_open_orders()
            open_order_count = len(open_orders)
    except Exception as exc:
        warnings.append(f"Open orders read failed: {exc}")

    try:
        run_id_str = str(replay_context.run_id)
        fill_rows = list(
            session.scalars(
                select(FillQualityMetrics)
                .where(FillQualityMetrics.run_id == run_id_str)
                .order_by(desc(FillQualityMetrics.fill_timestamp))
                .limit(500)
            ).all()
        )
        fills_this_cycle = len(fill_rows)
        adverse_fills = sum(1 for r in fill_rows if r.is_adverse_fill)
    except Exception as exc:
        warnings.append(f"Fill quality read failed: {exc}")

    return ExecutionReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        open_order_count=open_order_count,
        fills_this_cycle=fills_this_cycle,
        adverse_fills=adverse_fills,
        reconciliation_status=reconciliation_status,
        summary={
            "open_order_count": open_order_count,
            "fills_this_cycle": fills_this_cycle,
            "adverse_fills": adverse_fills,
            "reconciliation_status": reconciliation_status,
            "timestamp": timestamp.isoformat(),
        },
    )


def build_execution_summary(*, session: Session) -> ExecutionSummary:
    """Read current execution state for the platform artifact bundle."""
    open_order_count = 0
    adverse_fills = 0

    try:
        with SorUnitOfWork(session) as uow:
            open_orders = uow.tracked_orders.list_open_orders()
            open_order_count = len(open_orders)
    except Exception:
        pass

    try:
        fill_rows = list(
            session.scalars(
                select(FillQualityMetrics)
                .where(FillQualityMetrics.is_adverse_fill.is_(True))
                .limit(100)
            ).all()
        )
        adverse_fills = len(fill_rows)
    except Exception:
        pass

    return ExecutionSummary(
        open_order_count=open_order_count,
        fills_this_cycle=0,
        adverse_fills=adverse_fills,
        reconciliation_status=None,
        policy_mode=None,
    )
