"""Rules a staged pipeline must satisfy before it runs (plan items R6 and R2).

These are the rules that involve more than one stage. They are checked by
``StagedPipelineConfig``, which every way of building a pipeline goes through:
an experiment YAML loaded by the CLI or the scheduler cycle, and the replay
hook, which builds its stages in Python. Rules about a single stage live in
that stage's config class (see ``WalkForwardStageConfig``).

Rules
-----
1. Range guard. A walk-forward stage's first test fold must start after every
   earlier selection stage has stopped reading. A simulation stage that comes
   before walk-forward picks the survivors, so a test fold inside its window
   is not out of sample.
2. No vacuous filter. A filter with ``min_sharpe <= 0`` and ``min_trades == 0``
   asks for no evidence at all.

``smoke=True`` waives both. A smoke pipeline checks that the plumbing runs; its
survivors are not evidence about any strategy.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig

    from .stages.base_stage import BaseStage

logger = logging.getLogger(__name__)

StageKind = Literal["selection", "walk_forward", "other"]


class PipelineConfigError(ValueError):
    """A staged pipeline breaks one or more of the rules above."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("Invalid staged pipeline: " + "; ".join(self.issues))


@dataclass(frozen=True)
class StageWindowFacts:
    """What a stage tells the validator about itself.

    kind              "selection" for a stage that picks survivors on a date
                      window, "walk_forward" for one that tests on folds.
    end_date          Selection stages: the last day the stage reads.
    first_test_start  Walk-forward stages: the first day of the first test fold.
    filters           (label, FilterConfig) for every filter the stage applies.
    """

    stage_name: str
    kind: StageKind = "other"
    end_date: date | None = None
    first_test_start: date | None = None
    filters: tuple[tuple[str, FilterConfig], ...] = ()


def is_vacuous_filter(filter_config: FilterConfig) -> bool:
    return filter_config.min_sharpe <= 0 and filter_config.min_trades == 0


def pipeline_issues(stages: Sequence[BaseStage], *, smoke: bool = False) -> list[str]:
    """Every rule the pipeline breaks, in stage order. Empty means valid."""
    if smoke:
        return []

    issues: list[str] = []
    selections_so_far: list[StageWindowFacts] = []
    for stage in stages:
        facts = stage.validation_facts()
        if facts is None:
            continue

        for label, filter_config in facts.filters:
            if is_vacuous_filter(filter_config):
                issues.append(
                    f"stage '{facts.stage_name}' {label} asks for no evidence "
                    f"(min_sharpe={filter_config.min_sharpe}, min_trades=0); "
                    "set real thresholds or mark the pipeline smoke"
                )

        if facts.kind == "walk_forward" and facts.first_test_start is not None:
            for selection in selections_so_far:
                if selection.end_date is not None and selection.end_date >= facts.first_test_start:
                    issues.append(
                        f"walk-forward stage '{facts.stage_name}' starts its first test fold on "
                        f"{facts.first_test_start}, but selection stage '{selection.stage_name}' "
                        f"reads up to {selection.end_date}; test folds must start after every "
                        "earlier selection stage ends"
                    )
        elif facts.kind == "selection":
            selections_so_far.append(facts)

    return issues


def validate_pipeline(stages: Sequence[BaseStage], *, smoke: bool = False) -> None:
    """Raise PipelineConfigError listing every broken rule."""
    if smoke:
        logger.warning(
            "Staged pipeline is marked smoke: the range guard and the vacuous-filter "
            "rule are waived, so its survivors are not evidence."
        )
        return
    issues = pipeline_issues(stages, smoke=False)
    if issues:
        raise PipelineConfigError(issues)
