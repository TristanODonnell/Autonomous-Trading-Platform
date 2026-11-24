"""
NeighbourStage — re-runs each surviving strategy with one setting moved a little, and
records whether the result is still there (plan R13).

Per surviving strategy:
  1. Take the strategy's own full-window run: reused from an earlier stage when the
     window matches (the Monte Carlo base run), otherwise simulated.
  2. Build its neighbours: each setting moved down and moved up, the others left alone
     (gates/parameter_neighbours.py). One that cannot be built is recorded with the
     reason and not run.
  3. Simulate every neighbour over the same window.
  4. Apply the neighbour gate (gates/neighbour_gate.py).

Report only unless ``enforce`` is set: the findings are recorded and nobody is
eliminated.

Cost: up to two simulations per setting per survivor (+1 if no reusable own run). A
plain strategy has 2 to 10 settings, a composite 1 to 6. It is the most simulations per
strategy of any stage, so it belongs late, on the fewest survivors.

A neighbour is a strategy of its own, with its own id; its runs are stored under that
id with the window role ``neighbour``. It is never a survivor: the stage hands on the
strategies it was given, and a neighbour appears only in the verdict of the strategy
it was made from.

YAML block example
------------------
  - name: parameter_neighbours
    type: neighbour
    start_date: "2024-01-02"
    end_date: "2024-04-01"
    symbols: [SPY, QQQ, AAPL]
    min_share_of_own_sharpe: 0.5
    enforce: false
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.execution import ExecutionMode
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.pipeline.gates.neighbour_gate import (
    NeighbourGateConfig,
    NeighbourResult,
    SkippedNeighbour,
    evaluate_neighbour_gate,
)
from autonomous_trading_platform.research.pipeline.gates.parameter_neighbours import (
    Neighbour,
    NudgeRule,
    build_neighbours,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ..pipeline_validation import StageWindowFacts
from ._gate_stage_support import (
    GateSimulationRunner,
    GateWindow,
    RunContext,
    find_reusable_reference,
    gate_filter_output,
)
from .base_stage import BaseStage, StageDiagnostics, StageResult

logger = get_logger(__name__)

# Window roles, as stored on a run (32 characters at most).
OWN_RUN_ROLE = "neighbour_base"
NEIGHBOUR_ROLE = "neighbour"


@dataclass
class NeighbourStageConfig:
    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    gate: NeighbourGateConfig = field(default_factory=NeighbourGateConfig)
    nudge: NudgeRule = field(default_factory=NudgeRule)
    execution_mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False
    # The exported universe timeline the runs trade on (a dataset version id).
    # None = the symbols above are the universe for the whole window.
    universe_timeline_version: str | None = None

    def __post_init__(self) -> None:
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if not self.symbols:
            raise ValueError("symbols must not be empty")


class NeighbourStage(BaseStage):
    def __init__(
        self,
        *,
        stage_config: NeighbourStageConfig,
        simulation_runner: SimulationRunner,
    ) -> None:
        self._cfg = stage_config
        self._sims = GateSimulationRunner(
            simulation_runner=simulation_runner,
            stage_name=stage_config.name,
            execution_mode=stage_config.execution_mode,
            max_workers=stage_config.max_workers,
            fail_fast=stage_config.fail_fast,
        )
        self._prior_results: list[StageResult] = []

    @classmethod
    def from_dict(cls, raw: dict[str, Any], simulation_runner: SimulationRunner) -> NeighbourStage:
        from autonomous_trading_platform.research.config.stage_configs import (
            NeighbourStageConfigModel,
        )

        v = NeighbourStageConfigModel.model_validate(raw)
        stage_cfg = NeighbourStageConfig(
            name=v.name,
            symbols=list(v.symbols),
            start_date=v.start_date,
            end_date=v.end_date,
            gate=NeighbourGateConfig(
                min_share_of_own_sharpe=v.min_share_of_own_sharpe,
                min_neighbour_sharpe=v.min_neighbour_sharpe,
                enforce=v.enforce,
            ),
            nudge=NudgeRule(
                window_fraction=v.window_fraction,
                range_fraction=v.range_fraction,
                unranged_fraction=v.unranged_fraction,
            ),
            execution_mode=ExecutionMode(v.execution_mode),
            max_workers=v.max_workers,
            fail_fast=v.fail_fast,
        )
        return cls(stage_config=stage_cfg, simulation_runner=simulation_runner)

    @property
    def stage_name(self) -> str:
        return self._cfg.name

    def validation_facts(self) -> StageWindowFacts:
        # The strategy's own run and every neighbour's cover the stage's whole window.
        return StageWindowFacts(stage_name=self._cfg.name, last_day_read=self._cfg.end_date)

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

        window = GateWindow(
            self._cfg.symbols,
            self._cfg.start_date,
            self._cfg.end_date,
            self._cfg.universe_timeline_version,
        )
        ctx = RunContext(
            experiment_id=experiment_id,
            dataset_version=dataset_version,
            random_seed=random_seed,
            price_basis=price_basis,
            initial_cash=initial_cash,
            resample_to_daily=resample_to_daily,
        )

        own_runs: dict[str, SimulationRunResult] = {}
        neighbours: dict[str, list[Neighbour]] = {}
        jobs: list[tuple[StrategyConfig, str, float]] = []
        queued: set[str] = set()
        for config in survivors:
            ref = find_reusable_reference(self._prior_results, config.strategy_id, window)
            if ref is not None:
                own_runs[config.strategy_id] = ref
            else:
                jobs.append((config, OWN_RUN_ROLE, 1.0))
            neighbours[config.strategy_id] = build_neighbours(config, self._cfg.nudge)
            for neighbour in neighbours[config.strategy_id]:
                # Two survivors can share a neighbour: it is the same strategy, run once.
                if neighbour.config is not None and neighbour.config.strategy_id not in queued:
                    queued.add(neighbour.config.strategy_id)
                    jobs.append((neighbour.config, NEIGHBOUR_ROLE, 1.0))

        logger.info(
            "Stage %-20s | %d strategies | %d reused own runs | %d neighbour simulations | "
            "%d neighbours not built",
            self.stage_name,
            len(survivors),
            len(own_runs),
            len(queued),
            sum(1 for built in neighbours.values() for n in built if n.config is None),
        )

        runs = self._sims.run_many(jobs=jobs, window=window, ctx=ctx)

        filter_outputs: list[FilterScoreOutput] = []
        diagnostics: dict[str, StageDiagnostics] = {}
        final_survivors: list[StrategyConfig] = []
        with_findings = 0

        for config in survivors:
            sid = config.strategy_id
            own = own_runs.get(sid) or runs.get((sid, OWN_RUN_ROLE))
            results: list[NeighbourResult] = []
            skipped: list[SkippedNeighbour] = []
            missing = 0
            for neighbour in neighbours[sid]:
                if neighbour.config is None:
                    skipped.append(
                        SkippedNeighbour(
                            setting=neighbour.setting,
                            direction=neighbour.direction,
                            base_value=neighbour.base_value,
                            value=neighbour.value,
                            reason=neighbour.skipped or "not built",
                        )
                    )
                    continue
                run = runs.get((neighbour.config.strategy_id, NEIGHBOUR_ROLE))
                if run is None:
                    missing += 1
                    continue
                results.append(_to_result(neighbour, run))

            verdict = evaluate_neighbour_gate(
                strategy_id=sid,
                own_sharpe=float(own.risk_metrics.sharpe_ratio) if own is not None else None,
                results=results,
                skipped=skipped,
                missing=missing,
                config=self._cfg.gate,
            )
            if verdict.passed:
                final_survivors.append(config)
            with_findings += 1 if verdict.findings else 0
            for finding in verdict.findings:
                logger.info(
                    "  %s %s | %s",
                    "FAILED " if verdict.enforced else "FINDING (report only)",
                    sid,
                    finding,
                )
            filter_outputs.append(
                gate_filter_output(
                    sid, passed=verdict.passed, failure_reasons=verdict.failure_reasons
                )
            )
            diagnostics[sid] = StageDiagnostics(neighbour_verdict=verdict, reference_result=own)

        logger.info(
            "Stage %-20s | entered %d | survived %d | eliminated %d | with findings %d%s",
            self.stage_name,
            len(survivors),
            len(final_survivors),
            len(survivors) - len(final_survivors),
            with_findings,
            "" if self._cfg.gate.enforce else " (report only)",
        )

        return StageResult(
            stage_name=self.stage_name,
            simulation_results=list(runs.values()),
            filter_outputs=filter_outputs,
            survivors=final_survivors,
            diagnostics=diagnostics,
        )


def _to_result(neighbour: Neighbour, run: SimulationRunResult) -> NeighbourResult:
    assert neighbour.config is not None and neighbour.value is not None
    return NeighbourResult(
        setting=neighbour.setting,
        direction=neighbour.direction,
        base_value=neighbour.base_value,
        value=neighbour.value,
        strategy_id=neighbour.config.strategy_id,
        sharpe=float(run.risk_metrics.sharpe_ratio),
        max_drawdown=float(run.risk_metrics.max_drawdown),
        total_return=float(run.return_metrics.total_return),
        trade_count=int(run.trade_count),
    )
