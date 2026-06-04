"""
Failure injection hooks for platform replay testing.

These functions write synthetic failure evidence into SOR tables so the
platform runner can test error-handling paths without requiring real broker
outages, stale data, or production incidents.

Rules:
  - Every function takes an explicit session (caller flushes/commits).
  - Every function records the replay_run_id for traceability.
  - None of these should run in production (APP_ENV check is caller's responsibility).
  - Injection adds rows — it never deletes real data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Ingestion failures
# ---------------------------------------------------------------------------


def inject_ingestion_missing_bars(
    *,
    session: Session,
    timestamp: datetime,
    symbols: list[str],
    dataset_version: str,
    replay_run_id: str,
    severity: str = "high",
) -> list[str]:
    """Inject MissingBarIncidents rows for the given symbols at timestamp.

    Returns the list of injected incident_ids.
    """
    from autonomous_trading_platform.storage.sor.models.missing_bar_incidents import (
        MissingBarIncidents,
    )

    incident_ids: list[str] = []
    now = datetime.now(UTC)

    for symbol in symbols:
        incident_id = f"inject-missing-{uuid.uuid4().hex[:12]}"
        row = MissingBarIncidents(
            incident_id=incident_id,
            symbol=symbol.upper(),
            bar_timestamp=timestamp,
            dataset_version=dataset_version,
            run_id=replay_run_id,
            ingestion_run_id=None,
            incident_type="missing_bar",
            severity=severity,
            message=f"[INJECTED] Missing bar for {symbol} at {timestamp.isoformat()} (replay_run_id={replay_run_id})",
            created_at=now,
            resolved_flag=False,
        )
        session.add(row)
        incident_ids.append(incident_id)

    session.flush()
    return incident_ids


def inject_ingestion_late_bars(
    *,
    session: Session,
    timestamp: datetime,
    symbols: list[str],
    dataset_version: str,
    replay_run_id: str,
) -> list[str]:
    """Inject partial-coverage incidents simulating late bar delivery."""
    from autonomous_trading_platform.storage.sor.models.missing_bar_incidents import (
        MissingBarIncidents,
    )

    incident_ids: list[str] = []
    now = datetime.now(UTC)

    for symbol in symbols:
        incident_id = f"inject-late-{uuid.uuid4().hex[:12]}"
        row = MissingBarIncidents(
            incident_id=incident_id,
            symbol=symbol.upper(),
            bar_timestamp=timestamp,
            dataset_version=dataset_version,
            run_id=replay_run_id,
            ingestion_run_id=None,
            incident_type="late_bar",
            severity="medium",
            message=f"[INJECTED] Late bar for {symbol} at {timestamp.isoformat()} (replay_run_id={replay_run_id})",
            created_at=now,
            resolved_flag=False,
        )
        session.add(row)
        incident_ids.append(incident_id)

    session.flush()
    return incident_ids


# ---------------------------------------------------------------------------
# Feature failures
# ---------------------------------------------------------------------------


def inject_feature_validation_failure(
    *,
    session: Session,
    feature_dataset_version_id: str,
    replay_run_id: str,
) -> bool:
    """Set validation_status='failed' on a feature dataset version row.

    Returns True if the row was found and updated.
    """
    from sqlalchemy import select

    from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
        FeatureDatasetVersions,
    )

    row = session.scalar(
        select(FeatureDatasetVersions).where(
            FeatureDatasetVersions.dataset_version_id == feature_dataset_version_id
        )
    )
    if row is None:
        return False

    row.validation_status = "failed"
    # Append injection note to metadata
    meta = dict(row.metadata_json or {})
    meta["injection"] = {
        "type": "feature_validation_failure",
        "replay_run_id": replay_run_id,
        "injected_at": datetime.now(UTC).isoformat(),
    }
    row.metadata_json = meta
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Risk failures
# ---------------------------------------------------------------------------


def inject_risk_limit_breach(
    *,
    session: Session,
    run_id: str,
    timestamp: datetime,
    breach_type: str = "gross_exposure_exceeded",
    replay_run_id: str,
) -> str:
    """Write a RiskSnapshot row with is_blocked=True.

    Returns the new snapshot_id.
    """
    from decimal import Decimal

    from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot

    snapshot_id = uuid.uuid4()
    row = RiskSnapshot(
        snapshot_id=snapshot_id,
        run_id=uuid.UUID(run_id) if isinstance(run_id, str) else run_id,
        timestamp=timestamp,
        gross_exposure=Decimal("999999"),
        net_exposure=Decimal("999999"),
        leverage=10.0,
        drawdown_pct=None,
        limits={},
        utilization={"injection": breach_type, "replay_run_id": replay_run_id},
        is_blocked=True,
        block_reasons=[breach_type],
    )
    session.add(row)
    session.flush()
    return str(snapshot_id)


def inject_drawdown_breach(
    *,
    session: Session,
    strategy_id: str,
    realized_drawdown: float,
    max_drawdown_allowed: float = 0.10,
    replay_run_id: str,
) -> str:
    """Write or update a DrawdownGovernanceLadderStateRow above threshold.

    Returns the ladder_state_id.
    """
    from autonomous_trading_platform.storage.sor.models.drawdown_governance_ladder_state import (
        DrawdownGovernanceLadderStateRow,
    )
    from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_state_repository import (
        DrawdownGovernanceLadderStateRepository,
    )

    now = datetime.now(UTC)
    repo = DrawdownGovernanceLadderStateRepository(session)
    existing = repo.get_for_strategy(strategy_id)

    ladder_state = "breached" if realized_drawdown >= max_drawdown_allowed else "warning"

    if existing is not None:
        existing.ladder_state = ladder_state
        existing.realized_drawdown = realized_drawdown
        existing.max_drawdown_allowed = max_drawdown_allowed
        existing.drawdown_utilization = (
            realized_drawdown / max_drawdown_allowed if max_drawdown_allowed else None
        )
        existing.transition_reason = f"[INJECTED] drawdown_breach replay_run_id={replay_run_id}"
        existing.operator_ack_required = True
        existing.last_transition_at = now
        existing.last_evaluated_at = now
        existing.updated_at = now
        session.flush()
        return str(existing.ladder_state_id)

    ladder_state_id = f"inject-ddg-{uuid.uuid4().hex[:12]}"
    row = DrawdownGovernanceLadderStateRow(
        ladder_state_id=ladder_state_id,
        strategy_id=strategy_id,
        ladder_state=ladder_state,
        previous_ladder_state="normal",
        transition_reason=f"[INJECTED] drawdown_breach replay_run_id={replay_run_id}",
        realized_drawdown=realized_drawdown,
        max_drawdown_allowed=max_drawdown_allowed,
        drawdown_utilization=realized_drawdown / max_drawdown_allowed
        if max_drawdown_allowed
        else None,
        allocation_scalar=0.0 if ladder_state == "breached" else 0.5,
        operator_ack_required=True,
        last_transition_at=now,
        last_evaluated_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return ladder_state_id


# ---------------------------------------------------------------------------
# Governance failures
# ---------------------------------------------------------------------------


def inject_governance_demotion_trigger(
    *,
    session: Session,
    strategy_id: str,
    target_sharpe: float = -0.5,
    target_drawdown: float = 0.30,
    replay_run_id: str,
) -> str:
    """Seed failing MetricsSummary so auto-demotion triggers on next governance tick.

    Returns the metrics_snapshot_id.
    """
    from sqlalchemy import select

    from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
    from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns

    # Find or create a simulation run row for this strategy
    run = session.scalar(
        select(SimulationRuns)
        .where(SimulationRuns.strategy_id == strategy_id)
        .order_by(SimulationRuns.start_time.desc())
        .limit(1)
    )

    run_id_str = str(run.run_id) if run else str(uuid.uuid4())
    metrics_id = f"inject-metrics-{uuid.uuid4().hex[:12]}"

    existing = session.scalar(
        select(MetricsSummary).where(MetricsSummary.run_id == run_id_str).limit(1)
    )
    now = datetime.now(UTC)

    if existing is not None:
        existing.sharpe_ratio = target_sharpe
        existing.max_drawdown = target_drawdown
        existing.metrics_json = {
            **(existing.metrics_json or {}),
            "injection": {"type": "demotion_trigger", "replay_run_id": replay_run_id},
        }
        session.flush()
        return str(existing.metrics_snapshot_id)

    row = MetricsSummary(
        metrics_snapshot_id=metrics_id,
        run_id=run_id_str,
        created_at=now,
        total_return=-0.15,
        sharpe_ratio=target_sharpe,
        max_drawdown=target_drawdown,
        trade_count=10,
        winning_trade_count=2,
        losing_trade_count=8,
        volatility=0.35,
        metrics_json={"injection": {"type": "demotion_trigger", "replay_run_id": replay_run_id}},
        metric_lineage_type="research",
        environment="test",
        calculation_version="injected",
    )
    session.add(row)
    session.flush()
    return metrics_id


# ---------------------------------------------------------------------------
# Execution failures
# ---------------------------------------------------------------------------


def inject_broker_reconciliation_mismatch(
    *,
    session: Session,
    run_id: str,
    symbol: str,
    delta_usd: float = 500.0,
    replay_run_id: str,
) -> str | None:
    """Write an audit log entry recording a reconciliation mismatch scenario.

    Returns the audit event_id or None on failure.
    """
    try:
        from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
        from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
            AuditLogRepository,
        )

        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        repo = AuditLogRepository(session)
        repo.add(
            AuditLogEvent(
                event_id=event_id,
                run_id=run_id,
                event_type="BROKER_RECONCILIATION_MISMATCH",
                component="execution.injection",
                event_timestamp=now,
                message=f"[INJECTED] Reconciliation mismatch for {symbol}: delta=${delta_usd:.2f}",
                metadata={
                    "symbol": symbol.upper(),
                    "delta_usd": delta_usd,
                    "replay_run_id": replay_run_id,
                    "injection": True,
                },
            )
        )
        session.flush()
        return event_id
    except Exception:
        return None


def inject_order_rejected(
    *,
    session: Session,
    run_id: str,
    symbol: str,
    reason: str = "insufficient_buying_power",
    replay_run_id: str,
) -> str | None:
    """Write an audit log entry recording a rejected order scenario.

    We write to the AuditLog rather than TrackedOrder directly because
    TrackedOrder requires a full intent/broker pipeline to be consistent.
    Returns the audit event_id or None on failure.
    """
    try:
        from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
        from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
            AuditLogRepository,
        )

        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        repo = AuditLogRepository(session)
        repo.add(
            AuditLogEvent(
                event_id=event_id,
                run_id=run_id,
                event_type="ORDER_REJECTED",
                component="execution.injection",
                event_timestamp=now,
                message=f"[INJECTED] Order rejected for {symbol}: {reason}",
                metadata={
                    "symbol": symbol.upper(),
                    "reason": reason,
                    "replay_run_id": replay_run_id,
                    "injection": True,
                },
            )
        )
        session.flush()
        return event_id
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Runtime failures
# ---------------------------------------------------------------------------


def inject_runtime_job_failure(
    *,
    session: Session,
    job_name: str,
    timestamp: datetime,
    replay_run_id: str,
    error_message: str = "Injected job failure for replay testing",
) -> str:
    """Write a RuntimeJobRuns row with status='failed'.

    Returns the job_run_id.
    """
    from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns

    job_run_id = f"inject-job-{uuid.uuid4().hex[:12]}"
    row = RuntimeJobRuns(
        job_run_id=job_run_id,
        job_name=job_name,
        parent_job_run_id=None,
        status="failed",
        trigger_type="injection",
        started_at=timestamp,
        completed_at=timestamp,
        duration_ms=0,
        error_message=error_message,
        correlation_id=replay_run_id,
        input_summary_json={"injection": True, "replay_run_id": replay_run_id},
        output_summary_json=None,
    )
    session.add(row)
    session.flush()
    return job_run_id
