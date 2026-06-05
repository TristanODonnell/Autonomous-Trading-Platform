"""Strategy catalog domain replay hook (P2 — read-only catalog snapshot)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_strategies_service import (
    ActiveStrategiesService,
)
from autonomous_trading_platform.application.services.strategy_catalog_service import (
    StrategyCatalogService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    StrategyCatalogReplayResult,
    StrategyCatalogSummary,
)


def snapshot_strategy_catalog_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> StrategyCatalogReplayResult:
    """Read active strategies, governance state, and current metrics."""
    base = dict(
        domain="strategy_catalog",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    warnings: list[str] = []
    total_active = 0
    paper_count = 0
    live_count = 0

    try:
        svc = ActiveStrategiesService(session=session)
        strategies = svc.list_active_strategies()
        total_active = len(strategies)
        paper_count = sum(
            1 for s in strategies if s.get("approval_status") == "approved_for_paper_trading"
        )
        live_count = sum(
            1 for s in strategies if s.get("approval_status") == "approved_for_live_trading"
        )
    except Exception as exc:
        warnings.append(f"Strategy catalog read failed: {exc}")

    return StrategyCatalogReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        total_active=total_active,
        paper_count=paper_count,
        live_count=live_count,
        summary={
            "total_active": total_active,
            "paper_count": paper_count,
            "live_count": live_count,
            "timestamp": timestamp.isoformat(),
        },
    )


def build_strategy_catalog_summary(*, session: Session) -> StrategyCatalogSummary:
    """Read current strategy catalog for the platform artifact bundle."""
    total_active = 0
    paper_count = 0
    live_count = 0
    strategy_types: list[str] = []

    try:
        svc = StrategyCatalogService(session=session)
        strategies = svc.list_strategies()
        active = [s for s in strategies if s.get("status") in ("paper", "live")]
        total_active = len(active)
        paper_count = sum(1 for s in active if s.get("status") == "paper")
        live_count = sum(1 for s in active if s.get("status") == "live")
        strategy_types = sorted(
            {s.get("strategy_type", "") for s in active if s.get("strategy_type")}
        )
    except Exception:
        pass

    return StrategyCatalogSummary(
        total_active=total_active,
        paper_count=paper_count,
        live_count=live_count,
        strategy_types=strategy_types,
    )
