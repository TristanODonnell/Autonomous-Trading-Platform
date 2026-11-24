"""
Abstract base class for all pipeline stages.

Every stage receives the current survivor list and the experiment plan,
runs simulations under its own conditions, filters results, and returns
a StageResult containing raw results, filter verdicts, and the narrowed
survivor list for the next stage.

Concrete stages
---------------
SimulationStage   — single sim run per survivor over a defined window
WalkForwardStage  — re-runs survivors across rolling train/test folds
MonteCarloStage   — one run per survivor, its equity curve resampled N times
RegimeStage       — one run per survivor, gated on per-regime performance
                    using the auto-classified regime labels
StressStage       — equity-curve shock scenarios + cost-multiplier re-runs
NeighbourStage    — re-runs each survivor with one setting moved a little,
                    down and up, and records whether the result holds
OverfittingStage  — no simulations; gates on evidence produced by earlier
                    stages (fold degradation, MC resampling, regime spread)

Cross-stage evidence
--------------------
Stages record per-strategy evidence in StageResult.diagnostics. Before each
stage runs, PipelineRunner calls bind_prior_results() with every earlier
StageResult, so late stages (OverfittingStage) can read that evidence without
re-simulating.

Loader contract
---------------
Each concrete stage owns its own deserialization via from_dict(raw, simulation_runner).
The CLI and any other loader only needs to call StageRegistry.load(stage_raw, runner)
and never needs to know about individual stage internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

if TYPE_CHECKING:
    from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
        StrategyRegimeProfile,
    )
    from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
        MonteCarloAggregation,
    )
    from autonomous_trading_platform.research.pipeline.gates.neighbour_gate import (
        NeighbourGateVerdict,
    )
    from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
        OverfittingGateVerdict,
    )
    from autonomous_trading_platform.research.pipeline.gates.regime_gate import RegimeGateVerdict
    from autonomous_trading_platform.research.pipeline.gates.stress_gate import StressGateVerdict
    from autonomous_trading_platform.research.pipeline.pipeline_validation import (
        StageWindowFacts,
    )
    from autonomous_trading_platform.research.validation.overfitting_analysis import (
        OverfittingAnalysisResult,
    )
    from autonomous_trading_platform.research.validation.walk_forward_validation import (
        FoldValidationInput,
    )


@dataclass
class StageDiagnostics:
    """
    Per-strategy evidence produced by one stage.

    Every field is optional — each stage fills in only what it computed.

    fold_inputs         Walk-forward train/test metric pairs, one per fold, including
                        folds whose train run failed (train_passed=False).
    mc_aggregation      Monte Carlo distribution across resampled paths.
    regime_profile      Regime-conditioned performance profile.
    regime_verdict      RegimeStage gate decision and reasons.
    stress_verdict      StressStage gate decision and reasons.
    neighbour_verdict   NeighbourStage gate decision: every neighbour's run, the
                        ones not built, and the settings found fragile.
    overfitting_result  OverfittingStage analysis.
    overfitting_verdict OverfittingStage gate decision, findings and whether enforced.
    reference_result    Full-window run that best represents the strategy
                        (used by later stages instead of re-simulating).
    """

    fold_inputs: list[FoldValidationInput] | None = None
    mc_aggregation: MonteCarloAggregation | None = None
    regime_profile: StrategyRegimeProfile | None = None
    regime_verdict: RegimeGateVerdict | None = None
    stress_verdict: StressGateVerdict | None = None
    neighbour_verdict: NeighbourGateVerdict | None = None
    overfitting_result: OverfittingAnalysisResult | None = None
    overfitting_verdict: OverfittingGateVerdict | None = None
    reference_result: SimulationRunResult | None = None


@dataclass
class StageResult:
    """
    Output of one pipeline stage.

    stage_name          Human-readable label e.g. "cheap", "intermediate".
    simulation_results  Every raw SimulationRunResult produced in this stage.
                        A run reused from an earlier stage is not repeated here.
    filter_outputs      Filter verdicts + scores. Most stages write one per
                        strategy that entered; walk-forward writes one per
                        strategy per fold per window, and a stage may write
                        none. Not a count of anything: see ``entered``.
    survivors           Strategies that passed this stage's filters, ready to
                        be passed into the next stage.
    diagnostics         Per-strategy evidence keyed by strategy_id (see
                        StageDiagnostics). Empty for stages that record none.
    entered             How many strategies were handed to the stage. The
                        pipeline runner sets it, being the one that hands them
                        over. None on a result built outside a pipeline.
    """

    stage_name: str
    simulation_results: list[SimulationRunResult] = field(default_factory=list)
    filter_outputs: list[FilterScoreOutput] = field(default_factory=list)
    survivors: list[StrategyConfig] = field(default_factory=list)
    diagnostics: dict[str, StageDiagnostics] = field(default_factory=dict)
    entered: int | None = None

    @property
    def n_entered(self) -> int:
        """Strategies that entered the stage.

        The count the pipeline runner recorded. Without one (a stage run on its own),
        the strategies this result names: those with a verdict and those that survived.
        Never the number of verdicts, which walk-forward writes several of per strategy.
        """
        if self.entered is not None:
            return self.entered
        named = {output.strategy_id for output in self.filter_outputs}
        named.update(config.strategy_id for config in self.survivors)
        return len(named)

    @property
    def n_passed(self) -> int:
        return len(self.survivors)

    @property
    def n_failed(self) -> int:
        return self.n_entered - self.n_passed


class BaseStage(ABC):
    @property
    @abstractmethod
    def stage_name(self) -> str:
        """Short identifier used in logs and StageResult."""

    def bind_prior_results(self, prior_results: Sequence[StageResult]) -> None:  # noqa: B027
        """
        Receive every StageResult produced before this stage runs.

        No-op by default. Stages that consume cross-stage evidence (e.g.
        OverfittingStage) override this.
        """

    @abstractmethod
    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> StageResult: ...

    def validation_facts(self) -> StageWindowFacts | None:
        """What the pipeline validator needs to know about this stage.

        None (the default) means the stage takes no part in the cross-stage
        rules. Stages that select on a date window or test on folds override it.
        """
        return None

    @classmethod
    @abstractmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        simulation_runner: SimulationRunner,
    ) -> BaseStage:
        """
        Deserialize a stage from a raw YAML/dict block and return a fully
        constructed stage instance.

        Each concrete stage owns this logic — the CLI never needs to know
        about individual stage internals. Just call StageRegistry.load().

        Parameters
        ----------
        raw:
            The raw dict for this stage as parsed from YAML, e.g.:
            {
                "name": "cheap",
                "type": "simulation",
                "start_date": "2023-07-01",
                ...
            }
        simulation_runner:
            Injected by the caller — stages don't construct their own runner.
        """
