"""
StressStage — eliminates strategies that break under adverse conditions.

Per surviving strategy:
  1. Take a full-window baseline run — reused from an earlier stage when the
     window matches (Monte Carlo / Regime reference run), otherwise simulated.
  2. Shock scenarios: transform the baseline equity curve with the built-in
     StressTestService scenarios (vol spike, −5/−10% crash, downside
     amplification, trend reversal, per-bar cost drag). No re-simulation.
  3. Cost scenarios: re-simulate with slippage + commission scaled by each
     cost multiplier (default 2x and 3x).
  4. Apply the stress gate (gates/stress_gate.py).

Cost: len(cost_multipliers) simulations per survivor (+1 if no reusable
baseline). Set cost_multipliers: [] to run shock scenarios only.

YAML block example
------------------
  - name: stress_robustness
    type: stress
    start_date: "2024-01-02"
    end_date: "2024-04-01"
    symbols: [SPY, QQQ, AAPL]
    min_shock_survival_rate: 0.5
    cost_multipliers: [2.0, 3.0]
    min_cost_sharpe: 0.0
    min_cost_survival_rate: 0.5
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.execution import ExecutionMode
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.pipeline.gates.stress_gate import (
    CostStressResult,
    StressGateConfig,
    evaluate_stress_gate,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunResult,
)
from autonomous_trading_platform.research.validation.stress_test_service import (
    BUILT_IN_SCENARIOS,
    StressTestService,
    StressTestSummary,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ._gate_stage_support import (
    GateSimulationRunner,
    GateWindow,
    RunContext,
    find_reusable_reference,
    gate_filter_output,
)
from .base_stage import BaseStage, StageDiagnostics, StageResult

logger = logging.getLogger(__name__)

_BASELINE_ROLE = "stress_baseline"


def cost_window_role(multiplier: float) -> str:
    return f"stress_cost_{multiplier:g}x"


@dataclass
class StressStageConfig:
    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    gate: StressGateConfig = field(default_factory=StressGateConfig)
    execution_mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if not self.symbols:
            raise ValueError("symbols must not be empty")


class StressStage(BaseStage):
    def __init__(
        self,
        *,
        stage_config: StressStageConfig,
        simulation_runner: SimulationRunner,
    ) -> None:
        self._cfg = stage_config
        gate = stage_config.gate
        self._shock_service = StressTestService(
            min_sharpe_threshold=gate.min_shock_sharpe,
            max_drawdown_threshold=gate.max_shock_drawdown,
            scenarios=[s for s in BUILT_IN_SCENARIOS if s.name in gate.shock_scenarios],
        )
        self._sims = GateSimulationRunner(
            simulation_runner=simulation_runner,
            stage_name=stage_config.name,
            execution_mode=stage_config.execution_mode,
            max_workers=stage_config.max_workers,
            fail_fast=stage_config.fail_fast,
        )
        self._prior_results: list[StageResult] = []

    @classmethod
    def from_dict(cls, raw: dict[str, Any], simulation_runner: SimulationRunner) -> StressStage:
        from autonomous_trading_platform.research.config.stage_configs import (
            StressStageConfigModel,
        )

        v = StressStageConfigModel.model_validate(raw)
        default_gate = StressGateConfig()
        stage_cfg = StressStageConfig(
            name=v.name,
            symbols=list(v.symbols),
            start_date=v.start_date,
            end_date=v.end_date,
            gate=StressGateConfig(
                shock_scenarios=(
                    tuple(v.shock_scenarios)
                    if v.shock_scenarios is not None
                    else default_gate.shock_scenarios
                ),
                min_shock_sharpe=v.min_shock_sharpe,
                max_shock_drawdown=v.max_shock_drawdown,
                min_shock_survival_rate=v.min_shock_survival_rate,
                cost_multipliers=tuple(v.cost_multipliers),
                min_cost_sharpe=v.min_cost_sharpe,
                max_cost_drawdown=v.max_cost_drawdown,
                min_cost_survival_rate=v.min_cost_survival_rate,
            ),
            execution_mode=ExecutionMode(v.execution_mode),
            max_workers=v.max_workers,
            fail_fast=v.fail_fast,
        )
        return cls(stage_config=stage_cfg, simulation_runner=simulation_runner)

    @property
    def stage_name(self) -> str:
        return self._cfg.name

    def bind_prior_results(self, prior_results: Sequence[StageResult]) -> None:
        self._prior_results = list(prior_results)

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

        gate = self._cfg.gate
        window = GateWindow(self._cfg.symbols, self._cfg.start_date, self._cfg.end_date)
        ctx = RunContext(
            experiment_id=experiment_id,
            dataset_version=dataset_version,
            random_seed=random_seed,
            price_basis=price_basis,
            initial_cash=initial_cash,
            resample_to_daily=resample_to_daily,
        )

        baselines: dict[str, SimulationRunResult] = {}
        jobs: list[tuple[StrategyConfig, str, float]] = []
        for config in survivors:
            ref = find_reusable_reference(self._prior_results, config.strategy_id, window)
            if ref is not None:
                baselines[config.strategy_id] = ref
            elif gate.shock_scenarios:
                jobs.append((config, _BASELINE_ROLE, 1.0))
            for multiplier in gate.cost_multipliers:
                jobs.append((config, cost_window_role(multiplier), multiplier))

        logger.info(
            "Stage %-20s | %d strategies | %d reused baselines | %d simulations | "
            "shock scenarios=%d | cost multipliers=%s",
            self.stage_name,
            len(survivors),
            len(baselines),
            len(jobs),
            len(gate.shock_scenarios),
            ",".join(f"{m:g}x" for m in gate.cost_multipliers) or "none",
        )

        runs = self._sims.run_many(jobs=jobs, window=window, ctx=ctx)

        filter_outputs: list[FilterScoreOutput] = []
        diagnostics: dict[str, StageDiagnostics] = {}
        final_survivors: list[StrategyConfig] = []

        for config in survivors:
            sid = config.strategy_id
            baseline = baselines.get(sid) or runs.get((sid, _BASELINE_ROLE))
            shock_summary = self._run_shocks(sid, baseline) if gate.shock_scenarios else None
            cost_results = [
                _to_cost_result(m, runs[(sid, cost_window_role(m))])
                for m in gate.cost_multipliers
                if (sid, cost_window_role(m)) in runs
            ]
            verdict = evaluate_stress_gate(
                strategy_id=sid,
                shock_summary=shock_summary,
                cost_results=cost_results,
                config=gate,
            )
            if verdict.passed:
                final_survivors.append(config)
                logger.debug(
                    "  PASSED  %s | shock=%s | cost=%s",
                    sid,
                    _rate(verdict.shock_survival_rate),
                    _rate(verdict.cost_survival_rate),
                )
            else:
                logger.debug("  FAILED  %s | reasons: %s", sid, "; ".join(verdict.failure_reasons))
            filter_outputs.append(
                gate_filter_output(
                    sid, passed=verdict.passed, failure_reasons=verdict.failure_reasons
                )
            )
            diagnostics[sid] = StageDiagnostics(stress_verdict=verdict, reference_result=baseline)

        logger.info(
            "Stage %-20s | entered %d | survived %d | eliminated %d",
            self.stage_name,
            len(survivors),
            len(final_survivors),
            len(survivors) - len(final_survivors),
        )

        return StageResult(
            stage_name=self.stage_name,
            simulation_results=list(runs.values()),
            filter_outputs=filter_outputs,
            survivors=final_survivors,
            diagnostics=diagnostics,
        )

    def _run_shocks(
        self, strategy_id: str, baseline: SimulationRunResult | None
    ) -> StressTestSummary | None:
        if baseline is None or baseline.equity_curve is None or len(baseline.equity_curve) < 2:
            return None
        return self._shock_service.run(equity_curve=baseline.equity_curve, strategy_id=strategy_id)


def _to_cost_result(multiplier: float, result: SimulationRunResult) -> CostStressResult:
    return CostStressResult(
        cost_multiplier=multiplier,
        sharpe=float(result.risk_metrics.sharpe_ratio),
        max_drawdown=float(result.risk_metrics.max_drawdown),
        total_return=float(result.return_metrics.total_return),
        trade_count=int(result.trade_count),
    )


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"
