from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ResearchTaskType(StrEnum):
    GENERATION = "generation"
    SIMULATION = "simulation"
    WALK_FORWARD_FOLD = "walk_forward_fold"
    MONTE_CARLO_TRIAL = "monte_carlo_trial"
    VALIDATION = "validation"
    REGIME_ANALYSIS = "regime_analysis"
    INTELLIGENCE = "intelligence"


class ResearchCheckpointStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CACHE_HIT = "cache_hit"


@dataclass(frozen=True)
class ResearchCheckpointIdentity:
    """Stable research-unit identity used for checkpoint and restart planning."""

    experiment_id: str
    stage_name: str
    task_type: ResearchTaskType
    pipeline_id: str | None = None
    window_role: str | None = None
    strategy_id: str | None = None
    config_hash: str | None = None
    dataset_version: str | None = None
    price_basis: str | None = None
    universe_version: str | None = None
    feature_dataset_ids: dict[str, str] = field(default_factory=dict)
    regime_dataset_id: str | None = None
    unit_id: str | None = None

    def __post_init__(self) -> None:
        if not self.experiment_id:
            raise ValueError("experiment_id must not be empty")
        if not self.stage_name:
            raise ValueError("stage_name must not be empty")

    @property
    def checkpoint_id(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_hash": self.config_hash,
            "dataset_version": self.dataset_version,
            "experiment_id": self.experiment_id,
            "feature_dataset_ids": {
                key: self.feature_dataset_ids[key] for key in sorted(self.feature_dataset_ids)
            },
            "pipeline_id": self.pipeline_id,
            "price_basis": self.price_basis,
            "regime_dataset_id": self.regime_dataset_id,
            "stage_name": self.stage_name,
            "strategy_id": self.strategy_id,
            "task_type": self.task_type.value,
            "universe_version": self.universe_version,
            "unit_id": self.unit_id,
            "window_role": self.window_role,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ResearchCheckpointIdentity:
        return cls(
            experiment_id=payload["experiment_id"],
            pipeline_id=payload.get("pipeline_id"),
            stage_name=payload["stage_name"],
            window_role=payload.get("window_role"),
            strategy_id=payload.get("strategy_id"),
            config_hash=payload.get("config_hash"),
            dataset_version=payload.get("dataset_version"),
            price_basis=payload.get("price_basis"),
            universe_version=payload.get("universe_version"),
            feature_dataset_ids=dict(payload.get("feature_dataset_ids") or {}),
            regime_dataset_id=payload.get("regime_dataset_id"),
            task_type=ResearchTaskType(payload["task_type"]),
            unit_id=payload.get("unit_id"),
        )


@dataclass
class ResearchCheckpoint:
    identity: ResearchCheckpointIdentity
    status: ResearchCheckpointStatus = ResearchCheckpointStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_message: str | None = None
    artifact_uri: str | None = None
    cache_key: str | None = None
    cached_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def checkpoint_id(self) -> str:
        return self.identity.checkpoint_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_uri": self.artifact_uri,
            "cache_key": self.cache_key,
            "cached_run_id": self.cached_run_id,
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at.isoformat(),
            "error_message": self.error_message,
            "identity": self.identity.to_dict(),
            "metadata": self.metadata,
            "status": self.status.value,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ResearchCheckpoint:
        return cls(
            identity=ResearchCheckpointIdentity.from_dict(payload["identity"]),
            status=ResearchCheckpointStatus(payload["status"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
            error_message=payload.get("error_message"),
            artifact_uri=payload.get("artifact_uri"),
            cache_key=payload.get("cache_key"),
            cached_run_id=payload.get("cached_run_id"),
            metadata=dict(payload.get("metadata") or {}),
        )
