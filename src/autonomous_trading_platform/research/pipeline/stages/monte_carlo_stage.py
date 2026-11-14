"""
MonteCarloStage — Stage 4 (Elite).

Monte Carlo robustness gate: take one full-window run per surviving strategy,
resample its equity curve in whole sessions with replacement n_runs times, and
keep the strategy iff at least min_pass_rate of those resampled paths clear the
filter thresholds.

---------------------
Walk-forward (Stage 3) tests *time robustness* — does the strategy work across
different periods? Monte Carlo asks how much of the full-window result could be
luck: over a history of the same length, how often would this strategy still
clear the bar? On a short window the honest answer is often "cannot tell", and
the gate then fails strategies whose observed edge is small next to that
uncertainty.

By the time strategies reach this stage, they have already:
  - survived a cheap quick-simulation filter  (Stage 1)
  - survived a full-window simulation filter  (Stage 2)
  - survived walk-forward fold filtering      (Stage 3)

Why resampling and not re-running with different seeds
------------------------------------------------------
Research simulations draw nothing from the seeded generator (the fill-model
probabilities that would use it are off), so N seeded re-runs are N identical
runs. The resampling leaves the simulator untouched.

Base run
--------
One simulation per strategy (window_role "mc_base"), or none when an earlier
stage recorded a full-window run over the same window and symbols. It is
recorded as reference_result so later gates reuse it.

Determinism
-----------
The base run's seed and the resampling seed are both derived from the pipeline
seed and the strategy's identity (DeterministicSeedService), so the same
experiment always produces the same verdict.

Missing evidence fails
----------------------
No base run (the simulation returned nothing, e.g. a resume that skipped it),
an empty equity curve, or a curve too short to resample eliminates the
strategy. Not being able to measure is not a pass.

Aggregation
-----------
The distributions and the verdict are computed by MonteCarloAggregator,
keeping this class focused on pipeline concerns only.

YAML block example
------------------
  - name: elite_monte_carlo
    type: monte_carlo
    start_date: "2022-01-01"
    end_date: "2025-03-31"
    symbols: [AAPL, MSFT, SPY, AMZN, NVDA]
    n_runs: 2000            # resampled paths, not simulations
    min_pass_rate: 0.70
    filter_config:
      min_sharpe: 1.0       # per path
      max_drawdown: -0.20   # per path
      min_trades: 10        # base run only
    scoring_weights:
      w_sharpe: 0.5
      w_return: 0.25
      w_drawdown: 0.20
      w_consistency: 0.05
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
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
    ParallelExecutionError,
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
from ..aggregation.session_block_bootstrap import session_block_bootstrap
from ._gate_stage_support import GateWindow, find_reusable_reference, gate_filter_output
from .base_stage import BaseStage, StageDiagnostics, StageResult

logger = logging.getLogger(__name__)

_BASE_ROLE = "mc_base"
_BOOTSTRAP_ROLE = "mc_bootstrap"


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
    n_runs: int  # resampled paths per strategy
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
      1. Take a full-window base run: reused from an earlier stage when the
         window matches, otherwise simulated once.
      2. Resample its equity curve in whole sessions, n_runs paths.
      3. Strategy survives iff pass_rate >= min_pass_rate and the base run has
         at least min_trades trades (see MonteCarloAggregator).

    Only base runs simulated here go into StageResult.simulation_results; a
    reused run was already reported by the stage that produced it.
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
        self._seed_service = DeterministicSeedService()
        self._aggregator = MonteCarloAggregator(
            filter_config=stage_config.filter_config,
            min_pass_rate=stage_config.min_pass_rate,
        )
        # FilterScoreService produces the FilterScoreOutput entries that
        # StageResult expects — one per strategy, scored on the base run so the
        # score is comparable with upstream stages.
        self._filter_score_service = FilterScoreService(
            filter_config=stage_config.filter_config,
            scoring_weights=stage_config.scoring_weights,
        )
        self._prior_results: list[StageResult] = []

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

    def bind_prior_results(self, prior_results: Sequence[StageResult]) -> None:
        self._prior_results = list(prior_results)

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

        window = GateWindow(self._cfg.symbols, self._cfg.start_date, self._cfg.end_date)
        base_runs: dict[str, SimulationRunResult] = {}
        to_simulate: list[StrategyConfig] = []
        for config in survivors:
            ref = find_reusable_reference(self._prior_results, config.strategy_id, window)
            if ref is not None:
                base_runs[config.strategy_id] = ref
            else:
                to_simulate.append(config)
        n_reused = len(base_runs)

        logger.info(
            "Stage %-20s | n_runs=%d | min_pass_rate=%.0f%% | %d strategies entering "
            "| %d reused runs | %d new runs",
            self.stage_name,
            self._cfg.n_runs,
            self._cfg.min_pass_rate * 100,
            len(survivors),
            n_reused,
            len(to_simulate),
        )

        simulated = self._simulate_base_runs(
            to_simulate,
            experiment_id=experiment_id,
            dataset_version=dataset_version,
            base_seed=random_seed,
            price_basis=price_basis,
            initial_cash=initial_cash,
            resample_to_daily=resample_to_daily,
        )
        base_runs.update(simulated)

        all_filter_outputs: list[FilterScoreOutput] = []
        final_survivors: list[StrategyConfig] = []
        diagnostics: dict[str, StageDiagnostics] = {}

        for config in survivors:
            sid = config.strategy_id
            base = base_runs.get(sid)
            aggregation, missing_reason = self._resample(
                config=config,
                base=base,
                experiment_id=experiment_id,
                base_seed=random_seed,
            )
            if aggregation is None or base is None:
                reason = missing_reason or "no base run available"
                logger.warning("  FAILED  %s | no Monte Carlo evidence: %s", sid, reason)
                all_filter_outputs.append(
                    gate_filter_output(sid, passed=False, failure_reasons=[reason])
                )
                continue

            diagnostics[sid] = StageDiagnostics(
                mc_aggregation=aggregation,
                reference_result=base,
            )
            # One FilterScoreOutput per strategy, scored on the base run so
            # scores are comparable across stages. The pass/fail verdict comes
            # from the aggregation, not the base run's own filter.
            filter_outputs, _ = self._filter_score_service.filter_and_rank(
                [
                    FilterScoreInput(
                        strategy_id=sid,
                        rm=base.return_metrics,
                        risk=base.risk_metrics,
                        tm=base.trade_metrics,
                        sm=base.stability_metrics,
                        equity_curve=base.equity_curve,
                    )
                ]
            )
            fo = filter_outputs[0]
            fo.filter_result.passed = aggregation.passed  # type: ignore[attr-defined]
            all_filter_outputs.append(fo)

            if aggregation.passed:
                final_survivors.append(config)
                logger.debug(
                    "  PASSED  %s | pass_rate=%.0f%% (%d/%d) | P(sharpe>0)=%.2f "
                    "| sharpe μ=%.3f σ=%.3f p05=%.3f | return μ=%.3f | drawdown p05=%.3f",
                    sid,
                    aggregation.pass_rate * 100,
                    aggregation.n_passed,
                    aggregation.n_runs,
                    aggregation.prob_positive_sharpe,
                    aggregation.sharpe_dist.mean,
                    aggregation.sharpe_dist.std,
                    aggregation.sharpe_dist.p05,
                    aggregation.return_dist.mean,
                    aggregation.drawdown_dist.p05,
                )
            else:
                logger.debug(
                    "  FAILED  %s | pass_rate=%.0f%% (%d/%d) | reasons: %s",
                    sid,
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
            simulation_results=list(simulated.values()),
            filter_outputs=all_filter_outputs,
            survivors=final_survivors,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # Base runs
    # ------------------------------------------------------------------

    def _simulate_base_runs(
        self,
        configs: list[StrategyConfig],
        *,
        experiment_id: str,
        dataset_version: str,
        base_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool,
    ) -> dict[str, SimulationRunResult]:
        """
        One full-window simulation per strategy, keyed by strategy_id in the
        order given. A simulation that raises propagates as
        ParallelExecutionError; one that returns nothing is left out.
        """
        units: list[ExecutionUnit[SimulationRunResult | None]] = []
        for index, config in enumerate(configs):
            seed = self._derive_seed(
                config=config,
                experiment_id=experiment_id,
                base_seed=base_seed,
                window_role=_BASE_ROLE,
            )
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
                window_role=_BASE_ROLE,
                stage_name=self.stage_name,
                resample_to_daily=resample_to_daily,
            )
            units.append(
                ExecutionUnit(
                    unit_id=f"{self.stage_name}:{_BASE_ROLE}:{config.strategy_id}",
                    sort_key=(index, config.strategy_id),
                    run=self._base_runner_for(req),
                    metadata={
                        "strategy_id": config.strategy_id,
                        "stage_name": self.stage_name,
                        "window_role": _BASE_ROLE,
                        "seed": seed,
                    },
                )
            )
        results = self._execution_service.run(units)
        failures = [r.failure for r in results if r.failure is not None]
        if failures:
            raise ParallelExecutionError(failures)
        return {r.metadata["strategy_id"]: r.value for r in results if r.value is not None}

    def _base_runner_for(
        self,
        request: SimulationRunRequest,
    ) -> Callable[[], SimulationRunResult | None]:
        def run() -> SimulationRunResult | None:
            return self._run_resumable_base(request)

        return run

    def _run_resumable_base(self, request: SimulationRunRequest) -> SimulationRunResult | None:
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

    # ------------------------------------------------------------------
    # Resampling for a single strategy
    # ------------------------------------------------------------------

    def _resample(
        self,
        *,
        config: StrategyConfig,
        base: SimulationRunResult | None,
        experiment_id: str,
        base_seed: int,
    ) -> tuple[MonteCarloAggregation | None, str | None]:
        """Returns (aggregation, None), or (None, why there is no evidence)."""
        if base is None:
            return None, "no base run available"
        if base.equity_curve is None or base.equity_curve.empty:
            return None, "base run has no equity curve"
        if base.risk_metrics is None:
            return None, "base run has no risk metrics"

        try:
            # Annualised exactly as the base run's own Sharpe was, so the
            # resampled values are comparable with the thresholds.
            paths = session_block_bootstrap(
                base.equity_curve,
                n_paths=self._cfg.n_runs,
                seed=self._derive_seed(
                    config=config,
                    experiment_id=experiment_id,
                    base_seed=base_seed,
                    window_role=_BOOTSTRAP_ROLE,
                ),
                bars_per_year=base.risk_metrics.bars_per_year,
                risk_free_rate=base.risk_metrics.risk_free_rate,
            )
        except ValueError as exc:
            return None, f"base run cannot be resampled: {exc}"

        trade_count = base.trade_metrics.total_trades if base.trade_metrics is not None else None
        aggregation = self._aggregator.aggregate(
            strategy_id=config.strategy_id,
            paths=paths,
            base_trade_count=trade_count,
        )
        return aggregation, None

    def _derive_seed(
        self,
        *,
        config: StrategyConfig,
        experiment_id: str,
        base_seed: int,
        window_role: str,
    ) -> int:
        return self._seed_service.derive_seed(
            DeterministicSeedInputs(
                base_seed=base_seed,
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                config_hash=config.config_hash(),
                stage_name=self.stage_name,
                window_role=window_role,
            )
        )
