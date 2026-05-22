from __future__ import annotations

from dataclasses import dataclass, field

from autonomous_trading_platform.research.cache.cache_lookup_result import CacheLookupResult
from autonomous_trading_platform.research.checkpoints.research_checkpoint import (
    ResearchCheckpoint,
    ResearchCheckpointIdentity,
    ResearchCheckpointStatus,
)


@dataclass(frozen=True)
class RestartPlanUnit:
    identity: ResearchCheckpointIdentity
    status: ResearchCheckpointStatus
    action: str
    reason: str | None = None
    cache_key: str | None = None
    checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "cache_key": self.cache_key,
            "checkpoint_id": self.checkpoint_id or self.identity.checkpoint_id,
            "identity": self.identity.to_dict(),
            "reason": self.reason,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class RestartPlan:
    completed_units: list[RestartPlanUnit] = field(default_factory=list)
    missing_units: list[RestartPlanUnit] = field(default_factory=list)
    failed_units: list[RestartPlanUnit] = field(default_factory=list)
    cache_hit_units: list[RestartPlanUnit] = field(default_factory=list)
    units_to_rerun: list[RestartPlanUnit] = field(default_factory=list)
    units_to_skip: list[RestartPlanUnit] = field(default_factory=list)
    unsafe_to_resume_reasons: list[str] = field(default_factory=list)
    dry_run: bool = True

    @property
    def safe_to_resume(self) -> bool:
        return not self.unsafe_to_resume_reasons

    def to_dict(self) -> dict[str, object]:
        return {
            "cache_hit_units": [unit.to_dict() for unit in self.cache_hit_units],
            "completed_units": [unit.to_dict() for unit in self.completed_units],
            "dry_run": self.dry_run,
            "failed_units": [unit.to_dict() for unit in self.failed_units],
            "missing_units": [unit.to_dict() for unit in self.missing_units],
            "safe_to_resume": self.safe_to_resume,
            "summary": {
                "cache_hit": len(self.cache_hit_units),
                "completed": len(self.completed_units),
                "failed": len(self.failed_units),
                "missing": len(self.missing_units),
                "to_rerun": len(self.units_to_rerun),
                "to_skip": len(self.units_to_skip),
                "unsafe": len(self.unsafe_to_resume_reasons),
            },
            "units_to_rerun": [unit.to_dict() for unit in self.units_to_rerun],
            "units_to_skip": [unit.to_dict() for unit in self.units_to_skip],
            "unsafe_to_resume_reasons": list(self.unsafe_to_resume_reasons),
        }


class RestartPlanService:
    """Build deterministic restart plans from expected research units."""

    def __init__(
        self,
        *,
        checkpoints: dict[str, ResearchCheckpoint],
        cache_lookup: dict[str, CacheLookupResult] | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._cache_lookup = cache_lookup or {}

    def plan(
        self,
        expected_units: list[ResearchCheckpointIdentity],
        *,
        dry_run: bool = True,
        resume_failed: bool = True,
        resume_missing: bool = True,
        force_rerun: bool = False,
    ) -> RestartPlan:
        completed: list[RestartPlanUnit] = []
        missing: list[RestartPlanUnit] = []
        failed: list[RestartPlanUnit] = []
        cache_hits: list[RestartPlanUnit] = []
        to_rerun: list[RestartPlanUnit] = []
        to_skip: list[RestartPlanUnit] = []
        unsafe: list[str] = []

        for identity in sorted(expected_units, key=lambda item: item.checkpoint_id):
            checkpoint = self._checkpoints.get(identity.checkpoint_id)
            if checkpoint and checkpoint.identity.to_dict() != identity.to_dict():
                unsafe.append(
                    "Checkpoint identity mismatch for "
                    f"{identity.checkpoint_id}: persisted identity differs from requested unit"
                )
                continue

            cache_result = self._cache_lookup.get(identity.checkpoint_id)
            if not force_rerun and cache_result and cache_result.hit and cache_result.metadata:
                unit = RestartPlanUnit(
                    identity=identity,
                    status=ResearchCheckpointStatus.CACHE_HIT,
                    action="skip",
                    reason="exact cache hit",
                    cache_key=cache_result.metadata.cache_key,
                    checkpoint_id=identity.checkpoint_id,
                )
                cache_hits.append(unit)
                to_skip.append(unit)
                continue

            if checkpoint is None:
                unit = RestartPlanUnit(
                    identity=identity,
                    status=ResearchCheckpointStatus.PENDING,
                    action="rerun" if resume_missing or force_rerun else "skip",
                    reason="missing checkpoint",
                    checkpoint_id=identity.checkpoint_id,
                )
                missing.append(unit)
                (to_rerun if unit.action == "rerun" else to_skip).append(unit)
                continue

            unit = RestartPlanUnit(
                identity=identity,
                status=checkpoint.status,
                action="rerun" if force_rerun else "skip",
                reason=checkpoint.error_message,
                cache_key=checkpoint.cache_key,
                checkpoint_id=checkpoint.checkpoint_id,
            )

            if checkpoint.status in (
                ResearchCheckpointStatus.COMPLETED,
                ResearchCheckpointStatus.SKIPPED,
                ResearchCheckpointStatus.CACHE_HIT,
            ):
                if checkpoint.status == ResearchCheckpointStatus.CACHE_HIT:
                    cache_hits.append(unit)
                else:
                    completed.append(unit)
                (to_rerun if force_rerun else to_skip).append(unit)
            elif checkpoint.status == ResearchCheckpointStatus.FAILED:
                action = "rerun" if resume_failed or force_rerun else "skip"
                failed_unit = RestartPlanUnit(
                    identity=identity,
                    status=checkpoint.status,
                    action=action,
                    reason=checkpoint.error_message,
                    cache_key=checkpoint.cache_key,
                    checkpoint_id=checkpoint.checkpoint_id,
                )
                failed.append(failed_unit)
                (to_rerun if action == "rerun" else to_skip).append(failed_unit)
            else:
                missing.append(unit)
                (to_rerun if resume_missing or force_rerun else to_skip).append(unit)

        return RestartPlan(
            completed_units=completed,
            missing_units=missing,
            failed_units=failed,
            cache_hit_units=cache_hits,
            units_to_rerun=to_rerun,
            units_to_skip=to_skip,
            unsafe_to_resume_reasons=unsafe,
            dry_run=dry_run,
        )
