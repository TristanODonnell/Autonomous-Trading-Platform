"""
SimulationStage — Stage 1 (Cheap) and Stage 2 (Intermediate).

Runs the simulation runner exactly once per survivor over a fixed window,
then applies a FilterScoreService to produce pass/fail verdicts.
Survivors are the strategies whose filter_result.passed == True.

This is the simplest concrete stage and the direct replacement for the
per-window loop that previously lived in ExperimentOrchestrationService.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreInput,
    FilterScoreService,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_stage import BaseStage, StageResult

logger = logging.getLogger(__name__)


@dataclass
class SimulationStageConfig:
    name: str
    start_date: date
    end_date: date
    symbols: list[str]
    filter_config: FilterConfig
    scoring_weights: ScoringWeights
    window_role: str | None = None


class SimulationStage(BaseStage):
    def __init__(
        self,
        *,
        stage_config: SimulationStageConfig,
        simulation_runner: SimulationRunner,
    ) -> None:
        self._stage_config = stage_config
        self._simulation_runner = simulation_runner
        self._filter_score_service = FilterScoreService(
            filter_config=stage_config.filter_config,
            scoring_weights=stage_config.scoring_weights,
        )

    @property
    def stage_name(self) -> str:
        return self._stage_config.name

    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
    ) -> StageResult:
        if not survivors:
            logger.warning("Stage %s received empty survivor list — skipping.", self.stage_name)
            return StageResult(stage_name=self.stage_name)

        cfg = self._stage_config

        # --- run simulation once per survivor ---------------------------------
        sim_results = []
        for config in survivors:
            request = SimulationRunRequest(
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=dataset_version,
                random_seed=random_seed,
                price_basis=price_basis,
                symbols=cfg.symbols,
                start_date=cfg.start_date,
                end_date=cfg.end_date,
                initial_cash=initial_cash,
                window_role=cfg.window_role,
            )
            sim_results.append(self._simulation_runner.run(request))

        # --- filter and score -------------------------------------------------
        filter_inputs = [
            FilterScoreInput(
                strategy_id=result.strategy_id,
                rm=result.return_metrics,
                risk=result.risk_metrics,
                tm=result.trade_metrics,
                sm=result.stability_metrics,
                equity_curve=result.equity_curve,
            )
            for result in sim_results
        ]

        filter_outputs, _ = self._filter_score_service.filter_and_rank(filter_inputs)

        # --- narrow survivors -------------------------------------------------
        passed_ids = {o.strategy_id for o in filter_outputs if o.filter_result.passed}
        next_survivors = [c for c in survivors if c.strategy_id in passed_ids]

        # --- logging ----------------------------------------------------------
        passed = [o for o in filter_outputs if o.filter_result.passed]
        failed = [o for o in filter_outputs if not o.filter_result.passed]

        logger.info(
            "Stage %-16s | window %s→%s | entered %d | passed %d | failed %d",
            self.stage_name,
            cfg.start_date,
            cfg.end_date,
            len(sim_results),
            len(passed),
            len(failed),
        )
        for o in failed:
            logger.debug("  FAILED %s: %s", o.strategy_id, "; ".join(o.filter_result.failures))
        for o in passed:
            if o.score is not None:
                logger.debug(
                    "  PASSED %s | score=%.4f | sharpe=%.3f | return=%.3f | drawdown=%.3f",
                    o.strategy_id,
                    o.score.score,
                    o.score.sharpe_contrib,
                    o.score.return_contrib,
                    o.score.drawdown_contrib,
                )

        return StageResult(
            stage_name=self.stage_name,
            simulation_results=sim_results,
            filter_outputs=filter_outputs,
            survivors=next_survivors,
        )
