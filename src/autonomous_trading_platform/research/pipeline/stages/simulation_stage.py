"""
SimulationStage — Stage 1 (Cheap) and Stage 2 (Intermediate).

Runs the simulation runner exactly once per survivor over a fixed window,
then applies a FilterScoreService to produce pass/fail verdicts.
Survivors are the strategies whose filter_result.passed == True.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.checkpoints.research_checkpoint import ResearchTaskType
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
    ResumeMode,
)
from autonomous_trading_platform.research.execution import (
    DeterministicSeedInputs,
    DeterministicSeedService,
    ExecutionMode,
    ExecutionUnit,
    ParallelExecutionConfig,
    ParallelExecutionService,
)
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
    SimulationRunResult,
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
    execution_mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False


class SimulationStage(BaseStage):
    def __init__(
        self,
        *,
        stage_config: SimulationStageConfig,
        simulation_runner: SimulationRunner,
        checkpoint_service: ResearchCheckpointService | None = None,
        resume_mode: ResumeMode = ResumeMode.ALL,
        force_rerun: bool = False,
        dry_run: bool = False,
    ) -> None:
        self._stage_config = stage_config
        self._simulation_runner = simulation_runner
        self._checkpoint_service = checkpoint_service
        self._resume_mode = resume_mode
        self._force_rerun = force_rerun
        self._dry_run = dry_run
        self._seed_service = DeterministicSeedService()
        self._execution_service = ParallelExecutionService(
            ParallelExecutionConfig(
                mode=stage_config.execution_mode,
                max_workers=stage_config.max_workers,
                fail_fast=stage_config.fail_fast,
            )
        )
        self._filter_score_service = FilterScoreService(
            filter_config=stage_config.filter_config,
            scoring_weights=stage_config.scoring_weights,
        )

    @property
    def stage_name(self) -> str:
        return self._stage_config.name

    # ------------------------------------------------------------------
    # Loader — owns its own deserialization from a YAML dict block
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        simulation_runner: SimulationRunner,
    ) -> SimulationStage:
        from autonomous_trading_platform.research.config.stage_configs import (
            SimulationStageConfigModel,
        )

        validated = SimulationStageConfigModel.model_validate(raw)
        stage_cfg = SimulationStageConfig(
            name=validated.name,
            start_date=validated.start_date,
            end_date=validated.end_date,
            symbols=list(validated.symbols),
            window_role=validated.window_role,
            execution_mode=ExecutionMode(validated.execution_mode),
            max_workers=validated.max_workers,
            fail_fast=validated.fail_fast,
            filter_config=validated.filter_config.to_dataclass(),
            scoring_weights=validated.scoring_weights.to_dataclass(),
        )
        return cls(stage_config=stage_cfg, simulation_runner=simulation_runner)

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
        units: list[ExecutionUnit[SimulationRunResult | None]] = []
        for index, config in enumerate(survivors):
            seed = self._seed_service.derive_seed(
                DeterministicSeedInputs(
                    base_seed=random_seed,
                    experiment_id=experiment_id,
                    strategy_id=config.strategy_id,
                    config_hash=config.config_hash(),
                    stage_name=self.stage_name,
                    window_role=cfg.window_role or "default",
                )
            )
            request = SimulationRunRequest(
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=dataset_version,
                random_seed=seed,
                price_basis=price_basis,
                symbols=cfg.symbols,
                start_date=cfg.start_date,
                end_date=cfg.end_date,
                initial_cash=initial_cash,
                window_role=cfg.window_role,
                stage_name=self.stage_name,
            )
            units.append(
                ExecutionUnit(
                    unit_id=f"{self.stage_name}:{cfg.window_role or 'default'}:{config.strategy_id}",
                    sort_key=(index, config.strategy_id),
                    run=self._simulation_runner_for(request),
                    metadata={
                        "strategy_id": config.strategy_id,
                        "stage_name": self.stage_name,
                        "window_role": cfg.window_role or "default",
                        "seed": seed,
                    },
                )
            )
        maybe_sim_results = self._execution_service.values(units)
        sim_results = [result for result in maybe_sim_results if result is not None]

        # --- filter and score -------------------------------------------------
        if not sim_results:
            return StageResult(
                stage_name=self.stage_name,
                simulation_results=[],
                filter_outputs=[],
                survivors=survivors,
            )

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
            "Stage %-16s | window %s->%s | entered %d | passed %d | failed %d",
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

    def _simulation_runner_for(
        self,
        request: SimulationRunRequest,
    ) -> Callable[[], SimulationRunResult | None]:
        def run() -> SimulationRunResult | None:
            return self._run_resumable_simulation(request)

        return run

    def _run_resumable_simulation(
        self,
        request: SimulationRunRequest,
    ) -> SimulationRunResult | None:
        if self._checkpoint_service is None:
            return self._simulation_runner.run(request)
        execution = self._checkpoint_service.run_simulation_unit(
            request=request,
            runner=self._simulation_runner,
            task_type=ResearchTaskType.SIMULATION,
            resume_mode=self._resume_mode,
            force_rerun=self._force_rerun,
            dry_run=self._dry_run,
        )
        return execution.result
