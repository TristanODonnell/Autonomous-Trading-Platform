from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.experiments.filtering.filters import (
    FilterResult,
    apply_filters,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    ReturnMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    RiskMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.stability_metrics import (
    StabilityMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    TradeMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.scoring import (
    RankedStrategies,
    StrategyScore,
    rank_strategies,
)


@dataclass(slots=True)
class FilterScoreInput:
    """
    Everything the service needs to evaluate one simulation result.
    The caller unpacks these from SimulationRunResult — the service
    never imports SimulationRunResult directly, keeping the dependency
    arrow one-way.
    """

    strategy_id: str
    rm: ReturnMetrics
    risk: RiskMetrics
    tm: TradeMetrics
    sm: StabilityMetrics
    equity_curve: pd.DataFrame


@dataclass(slots=True)
class FilterScoreOutput:
    """Paired result for one strategy — filter verdict + score (if it passed)."""

    strategy_id: str
    filter_result: FilterResult
    score: StrategyScore | None  # None when filter_result.passed is False


class FilterScoreService:
    """
    Encapsulates FilterConfig + ScoringWeights for the lifetime of one experiment.

    Instantiate once per experiment (or per stage if thresholds differ),
    then call filter_and_rank with a batch of simulation results.

    The service owns the filter → score pipeline so the orchestrator
    never touches FilterConfig, ScoringWeights, apply_filters or
    rank_strategies directly.
    """

    def __init__(
        self,
        *,
        filter_config: FilterConfig,
        scoring_weights: ScoringWeights,
    ) -> None:
        self._filter_config = filter_config
        self._scoring_weights = scoring_weights

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def filter_and_rank(
        self,
        inputs: list[FilterScoreInput],
        top_n: int | None = None,
    ) -> tuple[list[FilterScoreOutput], RankedStrategies]:
        """
        Run apply_filters on every input, then rank the survivors.

        Returns
        -------
        outputs     Full per-strategy verdict list (passed + failed),
                    useful for audit logging failure reasons.
        ranked      RankedStrategies for the survivors only, ordered
                    descending by composite score, capped at top_n.
        """
        outputs: list[FilterScoreOutput] = []
        scoreable: list[dict] = []

        for inp in inputs:
            filter_result = apply_filters(
                rm=inp.rm,
                risk=inp.risk,
                tm=inp.tm,
                sm=inp.sm,
                config=self._filter_config,
                equity_curve=inp.equity_curve,
            )

            score: StrategyScore | None = None
            if filter_result.passed:
                scoreable.append(
                    {
                        "strategy_id": inp.strategy_id,
                        "rm": inp.rm,
                        "risk": inp.risk,
                        "sm": inp.sm,
                    }
                )

            outputs.append(
                FilterScoreOutput(
                    strategy_id=inp.strategy_id,
                    filter_result=filter_result,
                    score=score,
                )
            )

        ranked = rank_strategies(
            results=scoreable,
            weights=self._scoring_weights,
            top_n=top_n,
        )

        # Attach scores back onto the outputs so audit logs have everything
        score_by_id = {s.strategy_id: s for s in ranked.strategies}
        for out in outputs:
            if out.strategy_id in score_by_id:
                out.score = score_by_id[out.strategy_id]

        return outputs, ranked
