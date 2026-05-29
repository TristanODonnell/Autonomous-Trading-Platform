from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class GovernanceAuditEventRow(Base):
    """
    Immutable, append-only governance decision record.

    Every governance action - promotion, demotion, health transition, drawdown
    escalation, operator override - produces one row here. Rows are never
    updated. Amendments are expressed as new rows whose superseded_by column
    points at the row they correct.

    This table is the authoritative answer to: "Why did this happen, what
    evidence was evaluated, who/what triggered it, and which data run was used?"
    """

    __tablename__ = "governance_audit_events"

    governance_audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)

    event_type: Mapped[str] = mapped_column(String(128), nullable=False)

    # Who/what triggered the decision (enum: TriggerSource)
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False)

    # Governance state transition
    transition_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transition_to: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Actor (service name, operator username, system component)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)

    # Machine-readable outcome (enum: DecisionOutcome)
    decision_outcome: Mapped[str] = mapped_column(String(32), nullable=False)

    # Human-readable explanation - supplements machine evidence
    decision_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Machine-readable criteria results: [{criterion, threshold, actual, passed}, ...]
    criteria_evaluated: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Source run / simulation run / experiment lineage
    source_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    simulation_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    experiment_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    allocation_config_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Optional snapshot IDs for full reproducibility
    covariance_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    factor_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Complete before/after metric snapshots for governance replay
    metrics_lineage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    state_snapshot_before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    state_snapshot_after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Policy/rule versioning
    promotion_rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    governance_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluation_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Shadow validation integration (future)
    shadow_validation_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shadow_divergence_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Amendment chain - points at the governance_audit_id this record supersedes
    superseded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Link to the evaluation run that produced this decision
    evaluation_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    recorded_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        Index("ix_gov_audit_strategy_id", "strategy_id"),
        Index("ix_gov_audit_trigger_source", "trigger_source"),
        Index("ix_gov_audit_event_type", "event_type"),
        Index("ix_gov_audit_decision_outcome", "decision_outcome"),
        Index("ix_gov_audit_recorded_at", "recorded_at"),
        Index("ix_gov_audit_source_run_id", "source_run_id"),
        Index("ix_gov_audit_strategy_recorded", "strategy_id", "recorded_at"),
        Index("ix_gov_audit_superseded_by", "superseded_by"),
    )
