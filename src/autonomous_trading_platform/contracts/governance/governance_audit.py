from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class TriggerSource(StrEnum):
    OPERATOR_MANUAL = "operator_manual"
    AUTO_PROMOTION = "auto_promotion"
    AUTO_DEMOTION = "auto_demotion"
    SYSTEM_RISK = "system_risk"
    HEALTH_MONITOR = "health_monitor"
    DRAWDOWN_GOVERNANCE = "drawdown_governance"
    ALLOCATION_GOVERNANCE = "allocation_governance"
    SHADOW_VALIDATION = "shadow_validation"


class DecisionOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    NO_ACTION = "no_action"
    ESCALATED = "escalated"
    FAILED = "failed"


@dataclass(frozen=True)
class GovernanceCriteriaEvaluation:
    criterion: str
    threshold: float | None
    actual: float | None
    passed: bool
    evaluated_at: datetime | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "threshold": self.threshold,
            "actual": self.actual,
            "passed": self.passed,
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
        }


@dataclass(frozen=True)
class GovernanceDecisionEvidence:
    """
    Complete, machine-readable evidence record for a governance decision.

    Every field is immutable after creation. The evidence must fully explain
    the decision without any external lookups - a complete snapshot of the
    information that drove the governance action.
    """

    governance_state_before: str | None
    governance_state_after: str | None
    criteria_evaluated: list[GovernanceCriteriaEvaluation] = field(default_factory=list)
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)

    # Source run lineage - must never be null for capital-bearing transitions
    source_run_id: str | None = None
    simulation_run_id: str | None = None
    experiment_run_id: str | None = None

    # Config lineage
    allocation_config_hash: str | None = None
    promotion_rule_version: str | None = None
    governance_policy_version: str | None = None
    evaluation_version: str | None = None

    # Snapshot IDs for reproducibility
    covariance_snapshot_id: str | None = None
    factor_snapshot_id: str | None = None

    # Health state at decision time
    health_status_before: str | None = None
    health_status_after: str | None = None

    # Allocation status at decision time
    allocation_status: str | None = None

    # Drawdown governance context
    drawdown_utilization: float | None = None
    ladder_state: str | None = None
    allocation_scalar: float | None = None

    # Shadow validation context (future)
    shadow_validation_status: str | None = None
    shadow_divergence_metrics: dict[str, Any] | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "governance_state_before": self.governance_state_before,
            "governance_state_after": self.governance_state_after,
            "criteria_evaluated": [c.to_jsonable() for c in self.criteria_evaluated],
            "metrics_snapshot": self.metrics_snapshot,
            "source_run_id": self.source_run_id,
            "simulation_run_id": self.simulation_run_id,
            "experiment_run_id": self.experiment_run_id,
            "allocation_config_hash": self.allocation_config_hash,
            "promotion_rule_version": self.promotion_rule_version,
            "governance_policy_version": self.governance_policy_version,
            "evaluation_version": self.evaluation_version,
            "covariance_snapshot_id": self.covariance_snapshot_id,
            "factor_snapshot_id": self.factor_snapshot_id,
            "health_status_before": self.health_status_before,
            "health_status_after": self.health_status_after,
            "allocation_status": self.allocation_status,
            "drawdown_utilization": self.drawdown_utilization,
            "ladder_state": self.ladder_state,
            "allocation_scalar": self.allocation_scalar,
            "shadow_validation_status": self.shadow_validation_status,
            "shadow_divergence_metrics": self.shadow_divergence_metrics,
        }


@dataclass(frozen=True)
class GovernanceAuditRecord:
    """Read-only view of a persisted governance audit event."""

    governance_audit_id: str
    strategy_id: str
    event_type: str
    trigger_source: str
    transition_from: str | None
    transition_to: str | None
    actor: str
    decision_outcome: str
    decision_rationale: str
    criteria_evaluated: list[dict[str, Any]]
    metrics_lineage: dict[str, Any]
    state_snapshot_before: dict[str, Any]
    state_snapshot_after: dict[str, Any]
    source_run_id: str | None
    simulation_run_id: str | None
    experiment_run_id: str | None
    allocation_config_hash: str | None
    covariance_snapshot_id: str | None
    factor_snapshot_id: str | None
    promotion_rule_version: str | None
    governance_policy_version: str | None
    evaluation_version: str | None
    shadow_validation_status: str | None
    shadow_divergence_metrics: dict[str, Any] | None
    superseded_by: str | None
    evaluation_run_id: str | None
    recorded_at: datetime


@dataclass(frozen=True)
class GovernanceAuditListResult:
    records: list[GovernanceAuditRecord]
    total: int
    page: int
    page_size: int
