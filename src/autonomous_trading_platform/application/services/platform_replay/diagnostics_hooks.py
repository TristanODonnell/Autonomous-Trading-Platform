"""Diagnostics domain replay hook (P1 — read-only snapshot)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_snapshot_service import (
    RuntimeSnapshotService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    DiagnosticsReplayResult,
    DiagnosticsSummary,
    PlatformReplayContext,
)
from autonomous_trading_platform.contracts.runtime.runtime_snapshot import RuntimeSnapshot


def snapshot_diagnostics_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    sections: frozenset[str] | None = None,
    write_to_artifact_dir: bool = False,
) -> DiagnosticsReplayResult:
    """Capture a full runtime snapshot. Wraps RuntimeSnapshotService.capture()."""
    base = dict(
        domain="diagnostics",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    try:
        svc = RuntimeSnapshotService(session=session, local_only=True)
        snapshot: RuntimeSnapshot = svc.capture(sections=sections)
    except Exception as exc:
        return DiagnosticsReplayResult(**base, status="failed", errors=[str(exc)])

    captured_sections = (
        list(sections)
        if sections
        else [
            "controls",
            "settings",
            "portfolio",
            "allocations",
            "datasets",
            "experiments",
            "activity",
        ]
    )

    snapshot_path: str | None = None
    if write_to_artifact_dir and replay_context.artifact_dir:
        import json

        out = (
            replay_context.artifact_dir / f"diagnostics_{timestamp.strftime('%Y%m%dT%H%M%S')}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(snapshot.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
        snapshot_path = str(out)

    return DiagnosticsReplayResult(
        **base,
        status="ok",
        sections_captured=captured_sections,
        snapshot_path=snapshot_path,
        artifact_refs=[snapshot_path] if snapshot_path else [],
        summary={
            "sections_captured": captured_sections,
            "snapshot_path": snapshot_path,
            "timestamp": timestamp.isoformat(),
        },
    )


def build_diagnostics_summary(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> DiagnosticsSummary:
    """Lightweight diagnostics summary for the platform artifact bundle."""
    portfolio_value: str | None = None
    active_strategies = 0
    kill_switch_active = False

    try:
        svc = RuntimeSnapshotService(session=session, local_only=True)
        snap = svc.capture(sections=frozenset({"portfolio", "controls"}))

        if snap.operator_controls:
            kill_switch_active = snap.operator_controls.kill_switch_active

        if snap.portfolio and snap.portfolio.summary:
            portfolio_value = str(snap.portfolio.summary.current_portfolio_value)

        active_strategies = len(snap.strategy_controls)
    except Exception:
        pass

    return DiagnosticsSummary(
        snapshot_timestamp=timestamp.isoformat(),
        sections_captured=["portfolio", "controls"],
        portfolio_value=portfolio_value,
        active_strategies=active_strategies,
        kill_switch_active=kill_switch_active,
    )
