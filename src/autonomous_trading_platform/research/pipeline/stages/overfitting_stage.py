"""
OverfittingStage — final gate; runs no simulations.

Combines the evidence earlier stages recorded in StageResult.diagnostics
(walk-forward fold pairs, Monte Carlo dispersion, regime profile, reference
run) into one overfitting probability via OverfittingAnalyzer, and eliminates
strategies above max_overfitting_probability. See gates/overfitting_gate.py.

Place it last: it can only use evidence from stages that ran before it.

YAML block example
------------------
  - name: overfitting_gate
    type: overfitting
    max_overfitting_probability: 0.6
    min_core_indicators: 2
    min_trade_count: 30
    on_insufficient_evidence: pass
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
    OverfittingGateConfig,
    collect_overfitting_evidence,
    evaluate_overfitting_gate,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ._gate_stage_support import gate_filter_output
from .base_stage import BaseStage, StageDiagnostics, StageResult

logger = logging.getLogger(__name__)


@dataclass
class OverfittingStageConfig:
    name: str
    gate: OverfittingGateConfig = field(default_factory=OverfittingGateConfig)


class OverfittingStage(BaseStage):
    def __init__(self, *, stage_config: OverfittingStageConfig) -> None:
        self._cfg = stage_config
        self._prior_results: list[StageResult] = []

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        simulation_runner: SimulationRunner,  # noqa: ARG003 — no simulations here
    ) -> OverfittingStage:
        from autonomous_trading_platform.research.config.stage_configs import (
            OverfittingStageConfigModel,
        )

        v = OverfittingStageConfigModel.model_validate(raw)
        return cls(
            stage_config=OverfittingStageConfig(
                name=v.name,
                gate=OverfittingGateConfig(
                    max_overfitting_probability=v.max_overfitting_probability,
                    min_core_indicators=v.min_core_indicators,
                    min_trade_count=v.min_trade_count,
                    on_insufficient_evidence=v.on_insufficient_evidence,  # type: ignore[arg-type]
                    indicator_weights=v.indicator_weights,
                ),
            )
        )

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

        filter_outputs: list[FilterScoreOutput] = []
        diagnostics: dict[str, StageDiagnostics] = {}
        final_survivors: list[StrategyConfig] = []

        for config in survivors:
            sid = config.strategy_id
            evidence = collect_overfitting_evidence(sid, self._prior_results)
            verdict = evaluate_overfitting_gate(
                strategy_id=sid, evidence=evidence, config=self._cfg.gate
            )
            prob = verdict.analysis.overfitting_probability
            if verdict.passed:
                final_survivors.append(config)
                logger.debug(
                    "  PASSED  %s | p(overfit)=%.2f | core indicators=%d",
                    sid,
                    prob,
                    verdict.n_core_indicators,
                )
            else:
                logger.debug("  FAILED  %s | reasons: %s", sid, "; ".join(verdict.failure_reasons))
            filter_outputs.append(
                gate_filter_output(
                    sid, passed=verdict.passed, failure_reasons=verdict.failure_reasons
                )
            )
            diagnostics[sid] = StageDiagnostics(overfitting_result=verdict.analysis)

        logger.info(
            "Stage %-20s | entered %d | survived %d | eliminated %d",
            self.stage_name,
            len(survivors),
            len(final_survivors),
            len(survivors) - len(final_survivors),
        )

        return StageResult(
            stage_name=self.stage_name,
            filter_outputs=filter_outputs,
            survivors=final_survivors,
            diagnostics=diagnostics,
        )
