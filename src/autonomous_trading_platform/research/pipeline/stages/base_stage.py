"""
Abstract base class for all pipeline stages.

Every stage receives the current survivor list and the experiment plan,
runs simulations under its own conditions, filters results, and returns
a StageResult containing raw results, filter verdicts, and the narrowed
survivor list for the next stage.

Concrete stages
---------------
SimulationStage   — single sim run per survivor over a defined window
RegimeStage       — re-runs survivors across bull/bear/sideways windows   (future)
MonteCarloStage   — re-runs survivors N times with varied seeds            (future)
WalkForwardStage  — re-runs survivors across rolling train/test folds      (future)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunResult
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


@dataclass
class StageResult:
    """
    Output of one pipeline stage.

    stage_name          Human-readable label e.g. "cheap", "intermediate".
    simulation_results  Every raw SimulationRunResult produced in this stage.
                        For multi-run stages (Monte Carlo) this includes all
                        individual runs before aggregation.
    filter_outputs      Per-strategy filter verdict + score after aggregation.
                        One entry per survivor that entered this stage.
    survivors           Strategies that passed this stage's filters, ready to
                        be passed into the next stage.
    """

    stage_name: str
    simulation_results: list[SimulationRunResult] = field(default_factory=list)
    filter_outputs: list[FilterScoreOutput] = field(default_factory=list)
    survivors: list[StrategyConfig] = field(default_factory=list)

    @property
    def n_entered(self) -> int:
        return len(self.filter_outputs)

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

    @abstractmethod
    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
    ) -> StageResult: ...
