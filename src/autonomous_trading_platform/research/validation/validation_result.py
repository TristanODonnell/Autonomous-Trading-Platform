"""Core result types for the validation framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ValidationStageResult:
    """Outcome of one validation stage (walk-forward, stress, overfitting, etc.)."""

    stage_name: str
    passed: bool
    score: float  # 0.0 – 1.0; higher is better
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"ValidationStageResult.score must be in [0,1], got {self.score}")


@dataclass
class ValidationSummary:
    """
    Complete validation outcome for a single strategy run.

    Aggregates individual stage results into a unified picture with a
    composite RobustnessScore and a binary overall_passed verdict.
    """

    validation_run_id: str
    experiment_id: str
    strategy_id: str
    dataset_version: str
    stage_results: dict[str, ValidationStageResult]
    robustness_score: Any  # RobustnessScore — forward ref to avoid circular import
    computed_at: datetime
    overall_passed: bool
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        validation_run_id: str,
        experiment_id: str,
        strategy_id: str,
        dataset_version: str,
        stage_results: dict[str, ValidationStageResult],
        robustness_score: Any,
        min_robustness_score: float = 0.5,
    ) -> ValidationSummary:
        all_warnings: list[str] = []
        for sr in stage_results.values():
            all_warnings.extend(sr.warnings)
        overall_passed = robustness_score.overall >= min_robustness_score
        return cls(
            validation_run_id=validation_run_id,
            experiment_id=experiment_id,
            strategy_id=strategy_id,
            dataset_version=dataset_version,
            stage_results=stage_results,
            robustness_score=robustness_score,
            computed_at=datetime.now(UTC),
            overall_passed=overall_passed,
            warnings=all_warnings,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "validation_run_id": self.validation_run_id,
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "dataset_version": self.dataset_version,
            "overall_passed": self.overall_passed,
            "computed_at": self.computed_at.isoformat(),
            "robustness_score": self.robustness_score.as_dict(),
            "stages": {
                name: {
                    "passed": sr.passed,
                    "score": round(sr.score, 4),
                    "summary": sr.summary,
                    "warnings": sr.warnings,
                }
                for name, sr in self.stage_results.items()
            },
            "warnings": self.warnings,
        }
