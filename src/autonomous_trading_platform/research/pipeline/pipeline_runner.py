from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    StepMetricSet,
    record_step_completed,
    record_step_failed,
    record_step_started,
)
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metric_labels import observability_environment
from autonomous_trading_platform.observability.metrics import (
    research_stage_duration,
    research_stage_runs,
    research_stage_survivors_entered,
    research_stage_survivors_passed,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunResult,
    refuse_daily_resample,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ...contracts.common.enums import PriceBasis
from .pipeline_validation import validate_pipeline
from .stages.base_stage import BaseStage, StageResult

logger = get_logger(__name__)

_COMPONENT = "research.pipeline_runner"
_STAGE_METRICS = StepMetricSet(runs=research_stage_runs, duration=research_stage_duration)


@dataclass
class StagedPipelineConfig:
    """The stages of one pipeline, validated as a set.

    Every way of building a pipeline constructs this (experiment YAML through
    the CLI or the scheduler cycle, the replay hook in Python), so the
    cross-stage rules in pipeline_validation.py are checked here. smoke=True
    waives them for plumbing checks; YAML key ``staged_pipeline_config.smoke``.

    holdout_start is the first day no stage may read. It is not a YAML key: the
    loaders pass ``research/pipeline/holdout.py``'s date, the one place it is
    declared. None = no holdout is set aside and none is checked.
    """

    stages: Sequence[BaseStage] = field(default_factory=list)
    smoke: bool = False
    holdout_start: date | None = None

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("StagedPipelineConfig requires at least one stage.")
        validate_pipeline(self.stages, smoke=self.smoke, holdout_start=self.holdout_start)


@dataclass
class PipelineRunResult:
    """
    Aggregated output of a full pipeline run across all stages.

    stage_results       One StageResult per stage, in execution order.
                        Useful for per-stage audit and debugging.
    all_simulation_results  Every raw SimulationRunResult produced across
                            all stages — includes re-runs from Monte Carlo
                            and regime stages.
    all_filter_outputs  Every FilterScoreOutput across all stages.
    final_survivors     Strategies that passed every stage — the elite set.
    generation          One line per generated family (asked, delivered, rejected,
                        repeats, exhausted), set by the orchestration service when it
                        generated the initial configs; empty otherwise.
    """

    stage_results: list[StageResult] = field(default_factory=list)
    all_simulation_results: list[SimulationRunResult] = field(default_factory=list)
    all_filter_outputs: list[FilterScoreOutput] = field(default_factory=list)
    final_survivors: list[StrategyConfig] = field(default_factory=list)
    generation: list[dict[str, object]] = field(default_factory=list)


class PipelineRunner:
    def __init__(self, *, stages: Sequence[BaseStage]) -> None:
        if not stages:
            raise ValueError("PipelineRunner requires at least one stage.")
        self._stages = stages

    def run(
        self,
        *,
        initial_configs: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> PipelineRunResult:

        # Refused here as well as on each run's request, so a pipeline stops before
        # its first stage and not inside it.
        refuse_daily_resample(resample_to_daily)

        result = PipelineRunResult()
        survivors = initial_configs

        logger.info(
            "pipeline_started",
            extra=LogContext(
                run_id=experiment_id,
                experiment_id=experiment_id,
                dataset_version=dataset_version,
                component=_COMPONENT,
                n_entered=len(survivors),
            ).to_extra(),
        )

        for stage in self._stages:
            if not survivors:
                logger.warning(
                    "pipeline_stage_skipped_no_survivors",
                    extra=LogContext(
                        run_id=experiment_id,
                        experiment_id=experiment_id,
                        component=_COMPONENT,
                        stage_name=stage.stage_name,
                    ).to_extra(),
                )
                break

            stage.bind_prior_results(list(result.stage_results))

            t0 = time.perf_counter()
            record_step_started(
                logger=logger,
                metrics=_STAGE_METRICS,
                step=stage.stage_name,
                component=_COMPONENT,
                run_id=experiment_id,
            )

            try:
                with start_span(
                    "experiment_pipeline.stage",
                    timespan=SpanTimespan.STEP,
                    stage_name=stage.stage_name,
                    experiment_id=experiment_id,
                    dataset_version=dataset_version,
                    entered_count=len(survivors),
                ):
                    stage_result = stage.run(
                        survivors=survivors,
                        experiment_id=experiment_id,
                        dataset_version=dataset_version,
                        random_seed=random_seed,
                        price_basis=price_basis,
                        initial_cash=initial_cash,
                        resample_to_daily=resample_to_daily,
                    )
            except Exception as exc:
                duration = time.perf_counter() - t0
                record_step_failed(
                    logger=logger,
                    metrics=_STAGE_METRICS,
                    step=stage.stage_name,
                    component=_COMPONENT,
                    run_id=experiment_id,
                    exc=exc,
                    duration_seconds=duration,
                )
                raise

            duration = time.perf_counter() - t0
            record_step_completed(
                logger=logger,
                metrics=_STAGE_METRICS,
                step=stage.stage_name,
                component=_COMPONENT,
                run_id=experiment_id,
                duration_seconds=duration,
            )

            # The stage was handed these strategies; its verdicts do not say how many.
            stage_result.entered = len(survivors)

            env = observability_environment()
            stage_labels = {"environment": env, "stage_name": stage.stage_name}
            research_stage_survivors_entered.record(stage_result.n_entered, stage_labels)
            research_stage_survivors_passed.record(stage_result.n_passed, stage_labels)

            result.stage_results.append(stage_result)
            result.all_simulation_results.extend(stage_result.simulation_results)
            result.all_filter_outputs.extend(stage_result.filter_outputs)

            survivors = stage_result.survivors

            logger.info(
                "pipeline_stage_survivors",
                extra=LogContext(
                    run_id=experiment_id,
                    experiment_id=experiment_id,
                    component=_COMPONENT,
                    stage_name=stage.stage_name,
                    n_entered=stage_result.n_entered,
                    n_passed=stage_result.n_passed,
                    n_failed=stage_result.n_failed,
                ).to_extra(),
            )

        result.final_survivors = survivors

        logger.info(
            "pipeline_completed",
            extra=LogContext(
                run_id=experiment_id,
                experiment_id=experiment_id,
                component=_COMPONENT,
                n_entered=len(initial_configs),
                n_passed=len(result.final_survivors),
            ).to_extra(),
        )

        return result
