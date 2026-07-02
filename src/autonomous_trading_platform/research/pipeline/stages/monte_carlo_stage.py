"""
MonteCarloStage — Stage 4 (Elite).

Monte Carlo robustness testing: run each surviving strategy N times over the
same window, each time with a different (but deterministic) random seed.
A strategy survives iff it passes individual filter thresholds on at least
min_pass_rate fraction of those runs.

---------------------
Walk-forward (Stage 3) tests *time robustness* — does the strategy work across
different periods? Monte Carlo tests *structural robustness* — is performance
real, or an artefact of lucky trade ordering and slippage draws?

By the time strategies reach this stage, they have already:
  - survived a cheap quick-simulation filter  (Stage 1)
  - survived a full-window simulation filter  (Stage 2)
  - survived walk-forward fold filtering      (Stage 3)

Seed derivation
---------------
Each run uses seed = base_random_seed + run_index (0-indexed).
This is fully deterministic — running the same experiment twice always
produces identical results.

Aggregation
-----------
All metric statistics (mean, median, std, min, max) are computed by
MonteCarloAggregator, keeping this class focused on pipeline concerns only.

YAML block example
------------------
  - name: elite_monte_carlo
    type: monte_carlo
    start_date: "2022-01-01"
    end_date: "2025-03-31"
    symbols: [AAPL, MSFT, SPY, AMZN, NVDA]
    n_runs: 50
    min_pass_rate: 0.70
    filter_config:
      min_sharpe: 1.0
      min_trades: 10
      max_drawdown: -0.20
    scoring_weights:
      w_sharpe: 0.5
      w_return: 0.25
      w_drawdown: 0.20
      w_consistency: 0.05
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
    FilterScoreOutput,
    FilterScoreService,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ..aggregation.monte_carlo_aggregator import MonteCarloAggregation, MonteCarloAggregator
from .base_stage import BaseStage, StageResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — mirrors the pattern in walk_forward_stage.py
# ---------------------------------------------------------------------------


def _parse_filter_config(fc: dict[str, Any]) -> FilterConfig:
    return FilterConfig(
        min_sharpe=fc.get("min_sharpe", 1.0),
        max_drawdown=fc.get("max_drawdown", -0.20),
        min_trades=fc.get("min_trades", 10),
        min_consistency_score=fc.get("min_consistency_score", 0.5),
        min_profit_factor=fc.get("min_profit_factor", 1.0),
        min_win_rate=fc.get("min_win_rate", 0.0),
        min_total_return=fc.get("min_total_return", 0.0),
    )


def _parse_scoring_weights(sw: dict[str, Any]) -> ScoringWeights:
    return ScoringWeights(
        w_sharpe=sw.get("w_sharpe", 0.5),
        w_return=sw.get("w_return", 0.25),
        w_drawdown=sw.get("w_drawdown", 0.20),
        w_consistency=sw.get("w_consistency", 0.05),
    )


# ---------------------------------------------------------------------------
# Stage config
# ---------------------------------------------------------------------------


@dataclass
class MonteCarloStageConfig:
    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    n_runs: int
    min_pass_rate: float
    filter_config: FilterConfig
    scoring_weights: ScoringWeights
    execution_mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if self.n_runs < 2:
            raise ValueError("n_runs must be >= 2 for Monte Carlo to be meaningful")
        if not (0.0 < self.min_pass_rate <= 1.0):
            raise ValueError("min_pass_rate must be in (0, 1]")
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class MonteCarloStage(BaseStage):
    """
    Stage 4 Elite — Monte Carlo robustness gate.

    For every surviving strategy:
      1. Run the simulation N times over the same window.
         Each run uses a unique but deterministic seed:
           seed_i = base_random_seed + i   (i = 0 … n_runs-1)
      2. Pass all N results to MonteCarloAggregator.
      3. Strategy survives iff pass_rate >= min_pass_rate.

    All N raw SimulationRunResults are forwarded into StageResult so
    downstream reporting has access to the full distribution.
    """

    def __init__(
        self,
        *,
        stage_config: MonteCarloStageConfig,
        simulation_runner: SimulationRunner,
        checkpoint_service: ResearchCheckpointService | None = None,
        resume_mode: ResumeMode = ResumeMode.ALL,
        force_rerun: bool = False,
        dry_run: bool = False,
    ) -> None:
        self._cfg = stage_config
        self._simulation_runner = simulation_runner
        self._checkpoint_service = checkpoint_service
        self._resume_mode = resume_mode
        self._force_rerun = force_rerun
        self._dry_run = dry_run
        self._execution_service = ParallelExecutionService(
            ParallelExecutionConfig(
                mode=stage_config.execution_mode,
                max_workers=stage_config.max_workers,
                fail_fast=stage_config.fail_fast,
            )
        )
        self._aggregator = MonteCarloAggregator(
            filter_config=stage_config.filter_config,
            min_pass_rate=stage_config.min_pass_rate,
        )
        # FilterScoreService is used to produce the FilterScoreOutput entries
        # that StageResult expects — one per strategy, based on the *mean*
        # run metrics so the score is comparable with upstream stages.
        self._filter_score_service = FilterScoreService(
            filter_config=stage_config.filter_config,
            scoring_weights=stage_config.scoring_weights,
        )

    # ------------------------------------------------------------------
    # Loader — owns its own deserialization from a YAML dict block
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        simulation_runner: SimulationRunner,
    ) -> MonteCarloStage:
        from autonomous_trading_platform.research.config.stage_configs import (
            MonteCarloStageConfigModel,
        )

        validated = MonteCarloStageConfigModel.model_validate(raw)
        stage_cfg = MonteCarloStageConfig(
            name=validated.name,
            symbols=list(validated.symbols),
            start_date=validated.start_date,
            end_date=validated.end_date,
            n_runs=validated.n_runs,
            min_pass_rate=validated.min_pass_rate,
            execution_mode=ExecutionMode(validated.execution_mode),
            max_workers=validated.max_workers,
            fail_fast=validated.fail_fast,
            filter_config=validated.filter_config.to_dataclass(),
            scoring_weights=validated.scoring_weights.to_dataclass(),
        )
        return cls(stage_config=stage_cfg, simulation_runner=simulation_runner)

    @property
    def stage_name(self) -> str:
        return self._cfg.name

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> StageResult:
        if not survivors:
            logger.warning("Stage %s received empty survivor list — skipping.", self.stage_name)
            return StageResult(stage_name=self.stage_name)

        logger.info(
            "Stage %-20s | n_runs=%d | min_pass_rate=%.0f%% | %d strategies entering",
            self.stage_name,
            self._cfg.n_runs,
            self._cfg.min_pass_rate * 100,
            len(survivors),
        )

        all_sim_results: list[SimulationRunResult] = []
        all_filter_outputs: list[FilterScoreOutput] = []
        final_survivors: list[StrategyConfig] = []

        for config in survivors:
            aggregation = self._run_monte_carlo(
                config=config,
                experiment_id=experiment_id,
                dataset_version=dataset_version,
                base_seed=random_seed,
                price_basis=price_basis,
                initial_cash=initial_cash,
                resample_to_daily=resample_to_daily,
            )
            if aggregation is None:
                final_survivors.append(config)
                continue

            # All N raw results flow into the stage output
            all_sim_results.extend(aggregation.run_results)

            # Produce a single FilterScoreOutput for this strategy using the
            # representative (median-seed) run, so scores are comparable across
            # stages. The pass/fail verdict comes from the aggregation, not the
            # individual run filter.
            representative_result = self._pick_representative_run(aggregation)
            filter_outputs, _ = self._filter_score_service.filter_and_rank(
                [
                    FilterScoreInput(
                        strategy_id=config.strategy_id,
                        rm=representative_result.return_metrics,
                        risk=representative_result.risk_metrics,
                        tm=representative_result.trade_metrics,
                        sm=representative_result.stability_metrics,
                        equity_curve=representative_result.equity_curve,
                    )
                ]
            )
            # Override the filter verdict with the MC aggregation verdict so
            # the StageResult faithfully reflects what the MC gate decided.
            fo = filter_outputs[0]
            fo.filter_result.passed = aggregation.passed  # type: ignore[attr-defined]
            all_filter_outputs.append(fo)

            if aggregation.passed:
                final_survivors.append(config)
                logger.debug(
                    "  PASSED  %s | pass_rate=%.0f%% (%d/%d) "
                    "| sharpe μ=%.3f σ=%.3f | return μ=%.3f | drawdown μ=%.3f",
                    config.strategy_id,
                    aggregation.pass_rate * 100,
                    aggregation.n_passed,
                    aggregation.n_runs,
                    aggregation.sharpe_dist.mean,
                    aggregation.sharpe_dist.std,
                    aggregation.return_dist.mean,
                    aggregation.drawdown_dist.mean,
                )
            else:
                logger.debug(
                    "  FAILED  %s | pass_rate=%.0f%% (%d/%d) | reasons: %s",
                    config.strategy_id,
                    aggregation.pass_rate * 100,
                    aggregation.n_passed,
                    aggregation.n_runs,
                    "; ".join(aggregation.failure_reasons) or "none",
                )

        logger.info(
            "Stage %-20s | entered %d | survived %d | eliminated %d",
            self.stage_name,
            len(survivors),
            len(final_survivors),
            len(survivors) - len(final_survivors),
        )

        return StageResult(
            stage_name=self.stage_name,
            simulation_results=all_sim_results,
            filter_outputs=all_filter_outputs,
            survivors=final_survivors,
        )

    # ------------------------------------------------------------------
    # Monte Carlo execution for a single strategy
    # ------------------------------------------------------------------

    def _run_monte_carlo(
        self,
        *,
        config: StrategyConfig,
        experiment_id: str,
        dataset_version: str,
        base_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> MonteCarloAggregation | None:
        """
        Run the strategy N times with seeds [base_seed, base_seed+1, …,
        base_seed+n_runs-1] and aggregate the results.
        """
        units: list[ExecutionUnit[SimulationRunResult | None]] = []

        for run_index in range(self._cfg.n_runs):
            seed = base_seed + run_index
            req = SimulationRunRequest(
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=dataset_version,
                random_seed=seed,
                price_basis=price_basis,
                symbols=self._cfg.symbols,
                start_date=self._cfg.start_date,
                end_date=self._cfg.end_date,
                initial_cash=initial_cash,
                window_role=f"mc_run_{run_index}",
                stage_name=self.stage_name,
                resample_to_daily=resample_to_daily,
            )
            units.append(
                ExecutionUnit(
                    unit_id=f"{self.stage_name}:mc_run_{run_index}:{config.strategy_id}",
                    sort_key=(config.strategy_id, run_index),
                    run=self._trial_runner_for(req),
                    metadata={
                        "strategy_id": config.strategy_id,
                        "stage_name": self.stage_name,
                        "window_role": f"mc_run_{run_index}",
                        "trial_id": run_index,
                        "seed": seed,
                    },
                )
            )
        maybe_run_results = self._execution_service.values(units)
        run_results = [result for result in maybe_run_results if result is not None]

        if not run_results:
            return None

        return self._aggregator.aggregate(
            strategy_id=config.strategy_id,
            run_results=run_results,
        )

    def _trial_runner_for(
        self,
        request: SimulationRunRequest,
    ) -> Callable[[], SimulationRunResult | None]:
        def run() -> SimulationRunResult | None:
            return self._run_resumable_trial(request)

        return run

    # ------------------------------------------------------------------
    # Representative run selection
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_representative_run(
        aggregation: MonteCarloAggregation,
    ) -> SimulationRunResult:
        """
        Select the run whose Sharpe is closest to the median Sharpe across all
        runs. This gives a single result that represents typical behaviour
        rather than the best or worst case.
        """
        median_sharpe = aggregation.sharpe_dist.median
        return min(
            aggregation.run_results,
            key=lambda r: abs(
                (r.risk_metrics.sharpe_ratio if r.risk_metrics else 0.0) - median_sharpe
            ),
        )

    def _run_resumable_trial(self, request: SimulationRunRequest) -> SimulationRunResult | None:
        if self._checkpoint_service is None:
            return self._simulation_runner.run(request)
        execution = self._checkpoint_service.run_simulation_unit(
            request=request,
            runner=self._simulation_runner,
            task_type=ResearchTaskType.MONTE_CARLO_TRIAL,
            resume_mode=self._resume_mode,
            force_rerun=self._force_rerun,
            dry_run=self._dry_run,
        )
        return execution.result
