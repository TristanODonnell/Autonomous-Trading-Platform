from __future__ import annotations

import logging
from dataclasses import dataclass, field

from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunResult
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ...contracts.common.enums import PriceBasis
from .stages.base_stage import BaseStage, StageResult

logger = logging.getLogger(__name__)


@dataclass
class StagedPipelineConfig:
    stages: list[BaseStage] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("StagedPipelineConfig requires at least one stage.")


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
    """

    stage_results: list[StageResult] = field(default_factory=list)
    all_simulation_results: list[SimulationRunResult] = field(default_factory=list)
    all_filter_outputs: list[FilterScoreOutput] = field(default_factory=list)
    final_survivors: list[StrategyConfig] = field(default_factory=list)


class PipelineRunner:
    def __init__(self, *, stages: list[BaseStage]) -> None:
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
    ) -> PipelineRunResult:

        result = PipelineRunResult()
        survivors = initial_configs

        logger.info(
            "Pipeline starting | %d stages | %d initial strategies",
            len(self._stages),
            len(survivors),
        )

        for stage in self._stages:
            if not survivors:
                logger.warning(
                    "Pipeline stopping early before stage '%s' — no survivors remaining.",
                    stage.stage_name,
                )
                break

            stage_result = stage.run(
                survivors=survivors,
                experiment_id=experiment_id,
                dataset_version=dataset_version,
                random_seed=random_seed,
                price_basis=price_basis,
                initial_cash=initial_cash,
            )

            result.stage_results.append(stage_result)
            result.all_simulation_results.extend(stage_result.simulation_results)
            result.all_filter_outputs.extend(stage_result.filter_outputs)

            survivors = stage_result.survivors

            logger.info(
                "Stage %-16s complete | %d→%d survivors",
                stage.stage_name,
                stage_result.n_entered,
                stage_result.n_passed,
            )

        result.final_survivors = survivors

        logger.info(
            "Pipeline complete | %d stages ran | %d final survivors | %d total sim runs",
            len(result.stage_results),
            len(result.final_survivors),
            len(result.all_simulation_results),
        )

        return result
