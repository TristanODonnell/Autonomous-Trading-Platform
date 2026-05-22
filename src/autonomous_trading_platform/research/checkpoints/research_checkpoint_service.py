from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from autonomous_trading_platform.research.cache.cache_key_builder import build_simulation_cache_key
from autonomous_trading_platform.research.cache.cache_lookup_result import CacheLookupResult
from autonomous_trading_platform.research.cache.simulation_result_cache import SimulationResultCache
from autonomous_trading_platform.research.checkpoints.research_checkpoint import (
    ResearchCheckpoint,
    ResearchCheckpointIdentity,
    ResearchCheckpointStatus,
    ResearchTaskType,
)
from autonomous_trading_platform.research.checkpoints.research_restart_plan import (
    RestartPlan,
    RestartPlanService,
)
from autonomous_trading_platform.research.config.simulation_run_config import SimulationRunConfig
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


class ResumeMode(StrEnum):
    ALL = "all"
    FAILED_ONLY = "failed_only"
    MISSING_ONLY = "missing_only"


class SimulationRunnerLike(Protocol):
    def run(self, request: SimulationRunRequest) -> SimulationRunResult: ...


@dataclass(frozen=True)
class ResearchUnitExecution:
    status: ResearchCheckpointStatus
    result: SimulationRunResult | None = None
    checkpoint: ResearchCheckpoint | None = None
    skipped: bool = False


class ResearchCheckpointService:
    """Small checkpoint store for research experiments and pipeline units."""

    def __init__(
        self,
        *,
        persist_path: Path | str | None = None,
        simulation_cache: SimulationResultCache | None = None,
    ) -> None:
        self._persist_path = Path(persist_path) if persist_path else None
        self._checkpoints: dict[str, ResearchCheckpoint] = {}
        self._simulation_cache = simulation_cache
        if self._persist_path and self._persist_path.exists():
            self._load()

    def list_checkpoints(self) -> list[ResearchCheckpoint]:
        return [self._checkpoints[key] for key in sorted(self._checkpoints)]

    def get(self, identity: ResearchCheckpointIdentity) -> ResearchCheckpoint | None:
        return self._checkpoints.get(identity.checkpoint_id)

    def mark_running(self, identity: ResearchCheckpointIdentity) -> ResearchCheckpoint:
        return self._upsert(identity=identity, status=ResearchCheckpointStatus.RUNNING)

    def mark_completed(
        self,
        identity: ResearchCheckpointIdentity,
        *,
        artifact_uri: str | None = None,
        cache_key: str | None = None,
        cached_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResearchCheckpoint:
        return self._upsert(
            identity=identity,
            status=ResearchCheckpointStatus.COMPLETED,
            artifact_uri=artifact_uri,
            cache_key=cache_key,
            cached_run_id=cached_run_id,
            metadata=metadata,
        )

    def mark_failed(
        self,
        identity: ResearchCheckpointIdentity,
        *,
        error_message: str,
    ) -> ResearchCheckpoint:
        return self._upsert(
            identity=identity,
            status=ResearchCheckpointStatus.FAILED,
            error_message=error_message,
        )

    def mark_cache_hit(
        self,
        identity: ResearchCheckpointIdentity,
        *,
        cache_key: str,
        cached_run_id: str | None,
    ) -> ResearchCheckpoint:
        return self._upsert(
            identity=identity,
            status=ResearchCheckpointStatus.CACHE_HIT,
            cache_key=cache_key,
            cached_run_id=cached_run_id,
        )

    def plan_restart(
        self,
        expected_units: list[ResearchCheckpointIdentity],
        *,
        dry_run: bool = True,
        resume_failed: bool = True,
        resume_missing: bool = True,
        force_rerun: bool = False,
        cache_lookup: dict[str, CacheLookupResult] | None = None,
    ) -> RestartPlan:
        return RestartPlanService(
            checkpoints=dict(self._checkpoints),
            cache_lookup=cache_lookup,
        ).plan(
            expected_units,
            dry_run=dry_run,
            resume_failed=resume_failed,
            resume_missing=resume_missing,
            force_rerun=force_rerun,
        )

    def run_simulation_unit(
        self,
        *,
        request: SimulationRunRequest,
        runner: SimulationRunnerLike,
        task_type: ResearchTaskType = ResearchTaskType.SIMULATION,
        pipeline_id: str | None = None,
        resume_mode: ResumeMode = ResumeMode.ALL,
        force_rerun: bool = False,
        dry_run: bool = False,
    ) -> ResearchUnitExecution:
        identity = simulation_request_checkpoint_identity(
            request,
            task_type=task_type,
            pipeline_id=pipeline_id,
        )

        cache_key = None
        if self._simulation_cache is not None:
            simulation_cache_key = simulation_request_cache_key(request)
            cache_key = simulation_cache_key.key_id
            cache_result = self._simulation_cache.lookup(simulation_cache_key)
            if not force_rerun and cache_result.hit and cache_result.metadata:
                checkpoint = self.mark_cache_hit(
                    identity,
                    cache_key=cache_result.metadata.cache_key,
                    cached_run_id=cache_result.metadata.cached_run_id,
                )
                return ResearchUnitExecution(
                    status=ResearchCheckpointStatus.CACHE_HIT,
                    checkpoint=checkpoint,
                    skipped=True,
                )

        existing_checkpoint = self.get(identity)
        if existing_checkpoint is not None and not force_rerun:
            if existing_checkpoint.status in (
                ResearchCheckpointStatus.COMPLETED,
                ResearchCheckpointStatus.CACHE_HIT,
            ):
                return ResearchUnitExecution(
                    status=existing_checkpoint.status,
                    checkpoint=existing_checkpoint,
                    skipped=True,
                )
            if (
                existing_checkpoint.status == ResearchCheckpointStatus.FAILED
                and resume_mode == ResumeMode.MISSING_ONLY
            ):
                return ResearchUnitExecution(
                    status=existing_checkpoint.status,
                    checkpoint=existing_checkpoint,
                    skipped=True,
                )
            if (
                existing_checkpoint.status != ResearchCheckpointStatus.FAILED
                and resume_mode == ResumeMode.FAILED_ONLY
            ):
                return ResearchUnitExecution(
                    status=existing_checkpoint.status,
                    checkpoint=existing_checkpoint,
                    skipped=True,
                )

        if dry_run:
            checkpoint = self._upsert(identity=identity, status=ResearchCheckpointStatus.PENDING)
            return ResearchUnitExecution(
                status=ResearchCheckpointStatus.PENDING,
                checkpoint=checkpoint,
                skipped=True,
            )

        self.mark_running(identity)
        try:
            result = runner.run(request)
        except Exception as exc:
            self.mark_failed(identity, error_message=str(exc))
            raise

        if self._simulation_cache is not None:
            self._simulation_cache.record(
                simulation_request_cache_key(request),
                run_id=str(result.run_id),
                strategy_type=request.strategy_config.get("type"),
            )
        checkpoint = self.mark_completed(
            identity,
            cache_key=cache_key,
            cached_run_id=str(result.run_id),
            metadata={"run_id": str(result.run_id)},
        )
        return ResearchUnitExecution(
            status=ResearchCheckpointStatus.COMPLETED,
            result=result,
            checkpoint=checkpoint,
            skipped=False,
        )

    def _upsert(
        self,
        *,
        identity: ResearchCheckpointIdentity,
        status: ResearchCheckpointStatus,
        error_message: str | None = None,
        artifact_uri: str | None = None,
        cache_key: str | None = None,
        cached_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResearchCheckpoint:
        existing = self._checkpoints.get(identity.checkpoint_id)
        now = datetime.now(UTC)
        if existing is None:
            checkpoint = ResearchCheckpoint(identity=identity, status=status)
        else:
            if existing.identity.to_dict() != identity.to_dict():
                raise ValueError(
                    f"Unsafe checkpoint identity mismatch for {identity.checkpoint_id}"
                )
            checkpoint = existing
            checkpoint.status = status
            checkpoint.updated_at = now

        checkpoint.error_message = error_message
        checkpoint.artifact_uri = (
            artifact_uri if artifact_uri is not None else checkpoint.artifact_uri
        )
        checkpoint.cache_key = cache_key if cache_key is not None else checkpoint.cache_key
        checkpoint.cached_run_id = (
            cached_run_id if cached_run_id is not None else checkpoint.cached_run_id
        )
        if metadata:
            checkpoint.metadata = {**checkpoint.metadata, **metadata}
        self._checkpoints[identity.checkpoint_id] = checkpoint
        self._persist()
        return checkpoint

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            checkpoint_id: checkpoint.to_dict()
            for checkpoint_id, checkpoint in sorted(self._checkpoints.items())
        }
        self._persist_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _load(self) -> None:
        raw = json.loads(self._persist_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        if not isinstance(raw, dict):
            raise ValueError("Checkpoint store must contain a JSON object")
        self._checkpoints = {
            checkpoint_id: ResearchCheckpoint.from_dict(payload)
            for checkpoint_id, payload in raw.items()
        }


def simulation_request_checkpoint_identity(
    request: SimulationRunRequest,
    *,
    task_type: ResearchTaskType = ResearchTaskType.SIMULATION,
    pipeline_id: str | None = None,
    universe_version: str = "v1",
    feature_dataset_ids: dict[str, str] | None = None,
    regime_dataset_id: str | None = None,
) -> ResearchCheckpointIdentity:
    config = StrategyConfig(
        strategy_id=request.strategy_id,
        type=request.strategy_config["type"],
        parameters=request.strategy_config.get("parameters", {}),
    )
    return ResearchCheckpointIdentity(
        experiment_id=request.experiment_id or "adhoc",
        pipeline_id=pipeline_id,
        stage_name=request.stage_name or "adhoc",
        window_role=request.window_role or "default",
        strategy_id=request.strategy_id,
        config_hash=config.config_hash(),
        dataset_version=request.dataset_version,
        price_basis=request.price_basis.value,
        universe_version=universe_version,
        feature_dataset_ids=feature_dataset_ids or {},
        regime_dataset_id=regime_dataset_id,
        task_type=task_type,
        unit_id=f"{request.stage_name or 'adhoc'}:{request.window_role or 'default'}:{request.strategy_id}",
    )


def simulation_request_cache_key(request: SimulationRunRequest):
    config = StrategyConfig(
        strategy_id=request.strategy_id,
        type=request.strategy_config["type"],
        parameters=request.strategy_config.get("parameters", {}),
    )
    run_config = SimulationRunConfig(
        strategy_id=request.strategy_id,
        strategy_type=request.strategy_config["type"],
        parameters=request.strategy_config.get("parameters", {}),
        dataset_version=request.dataset_version,
        random_seed=request.random_seed,
        price_basis=request.price_basis,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        initial_cash=request.initial_cash,
        experiment_id=request.experiment_id,
        strict_data_loading=request.strict_data_loading,
        shuffle_timestamp=request.shuffle_timestamp,
        window_role=request.window_role,
        stage_name=request.stage_name,
    )
    return build_simulation_cache_key(strategy_config=config, run_config=run_config)
