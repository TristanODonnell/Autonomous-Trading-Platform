"""
Shared plumbing for the robustness gate stages (regime, stress, overfitting).

These stages decide pass/fail through pure gate functions in
research/pipeline/gates/; this module only handles the pipeline concerns they
share: reusing an earlier stage's full-window run instead of re-simulating,
running the simulations they do need, and turning a verdict into the
FilterScoreOutput that StageResult expects.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.execution import (
    DeterministicSeedInputs,
    DeterministicSeedService,
    ExecutionMode,
    ExecutionUnit,
    ParallelExecutionConfig,
    ParallelExecutionError,
    ParallelExecutionService,
)
from autonomous_trading_platform.research.experiments.filtering.filters import FilterResult
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_stage import StageResult


@dataclass(frozen=True)
class GateWindow:
    symbols: list[str]
    start_date: date
    end_date: date


@dataclass(frozen=True)
class RunContext:
    """The per-pipeline arguments every BaseStage.run receives."""

    experiment_id: str
    dataset_version: str
    random_seed: int
    price_basis: PriceBasis
    initial_cash: float
    resample_to_daily: bool


def find_reusable_reference(
    prior_results: Sequence[StageResult],
    strategy_id: str,
    window: GateWindow,
) -> SimulationRunResult | None:
    """Most recent earlier full-window run for this strategy over the same window.

    Monte Carlo's representative run (and RegimeStage's own run) are recorded
    as reference_result; when the window matches, later gates reuse it rather
    than re-simulating identical inputs.
    """
    wanted_symbols = sorted(s.upper() for s in window.symbols)
    for stage_result in reversed(prior_results):
        diag = stage_result.diagnostics.get(strategy_id)
        ref = diag.reference_result if diag is not None else None
        if ref is None:
            continue
        if (
            ref.start_date == window.start_date
            and ref.end_date == window.end_date
            and sorted(s.upper() for s in ref.symbols) == wanted_symbols
        ):
            return ref
    return None


class GateSimulationRunner:
    """Runs the extra simulations a gate stage needs, deterministically seeded."""

    def __init__(
        self,
        *,
        simulation_runner: SimulationRunner,
        stage_name: str,
        execution_mode: ExecutionMode,
        max_workers: int,
        fail_fast: bool,
    ) -> None:
        self._runner = simulation_runner
        self._stage_name = stage_name
        self._seed_service = DeterministicSeedService()
        self._execution = ParallelExecutionService(
            ParallelExecutionConfig(
                mode=execution_mode, max_workers=max_workers, fail_fast=fail_fast
            )
        )

    def run_many(
        self,
        *,
        jobs: list[tuple[StrategyConfig, str, float]],
        window: GateWindow,
        ctx: RunContext,
    ) -> dict[tuple[str, str], SimulationRunResult]:
        """Run (config, window_role, cost_multiplier) jobs; key results by (strategy_id, role).

        A simulation that raises propagates as ParallelExecutionError, as in the
        other stages. A runner returning None (resume dry-run) is omitted —
        callers treat a missing run as missing evidence.
        """
        units: list[ExecutionUnit[SimulationRunResult | None]] = []
        for index, (config, window_role, cost_multiplier) in enumerate(jobs):
            seed = self._seed_service.derive_seed(
                DeterministicSeedInputs(
                    base_seed=ctx.random_seed,
                    experiment_id=ctx.experiment_id,
                    strategy_id=config.strategy_id,
                    config_hash=config.config_hash(),
                    stage_name=self._stage_name,
                    window_role=window_role,
                )
            )
            request = SimulationRunRequest(
                experiment_id=ctx.experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=ctx.dataset_version,
                random_seed=seed,
                price_basis=ctx.price_basis,
                symbols=window.symbols,
                start_date=window.start_date,
                end_date=window.end_date,
                initial_cash=ctx.initial_cash,
                window_role=window_role,
                stage_name=self._stage_name,
                resample_to_daily=ctx.resample_to_daily,
                cost_multiplier=cost_multiplier,
            )
            units.append(
                ExecutionUnit(
                    unit_id=f"{self._stage_name}:{window_role}:{config.strategy_id}",
                    sort_key=(index, config.strategy_id, window_role),
                    run=self._run_fn(request),
                    metadata={
                        "strategy_id": config.strategy_id,
                        "stage_name": self._stage_name,
                        "window_role": window_role,
                        "seed": seed,
                        "cost_multiplier": cost_multiplier,
                    },
                )
            )
        results = self._execution.run(units)
        failures = [r.failure for r in results if r.failure is not None]
        if failures:
            raise ParallelExecutionError(failures)
        return {
            (r.metadata["strategy_id"], r.metadata["window_role"]): r.value
            for r in results
            if r.value is not None
        }

    def _run_fn(self, request: SimulationRunRequest) -> Callable[[], SimulationRunResult | None]:
        def run() -> SimulationRunResult | None:
            return self._runner.run(request)

        return run


def gate_filter_output(
    strategy_id: str,
    *,
    passed: bool,
    failure_reasons: Sequence[str],
) -> FilterScoreOutput:
    """StageResult entry for a gate verdict. Gates judge; they do not re-score."""
    return FilterScoreOutput(
        strategy_id=strategy_id,
        filter_result=FilterResult(
            passed=passed,
            failures=list(failure_reasons),
            core_passed=passed,
            robustness_passed=passed,
        ),
        score=None,
    )
