"""
Composite scoring and ranking of strategies that passed filters.

TASK-140: composite score formula
    score = w_sharpe     * sharpe
          + w_return     * total_return
          - w_drawdown   * abs(max_drawdown)
          + w_consistency * consistency_score

    max_drawdown is already negative from risk_metrics; abs() is applied here
    so w_drawdown always acts as a penalty regardless of sign.

TASK-141: rank strategies by composite score, return ordered list.

Usage
-----
    from .config import ScoringWeights
    from .scoring import score_strategy, rank_strategies

    s = score_strategy(rm=rm, risk=risk, sm=sm, weights=ScoringWeights())

    ranked = rank_strategies(
        results=[
            {"strategy_id": "s1", "rm": rm1, "risk": risk1, "sm": sm1},
            {"strategy_id": "s2", "rm": rm2, "risk": risk2, "sm": sm2},
        ],
        weights=ScoringWeights(),
        top_n=10,
    )
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import ScoringWeights
from .metrics.return_metrics import ReturnMetrics
from .metrics.risk_metrics import RiskMetrics
from .metrics.stability_metrics import StabilityMetrics

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StrategyScore:
    """Composite score and metric breakdown for one strategy."""

    strategy_id: str
    score: float  # final composite score
    sharpe_contrib: float  # w_sharpe     * sharpe
    return_contrib: float  # w_return     * total_return
    drawdown_contrib: float  # w_drawdown * abs(max_drawdown)  — always negative
    consistency_contrib: float  # w_consistency * consistency_score


@dataclass(slots=True)
class RankedStrategies:
    """Ordered output of rank_strategies()."""

    strategies: list[StrategyScore]  # descending by score
    top_n: int | None  # None means all survivors returned
    weights: ScoringWeights


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def score_strategy(
    *,
    strategy_id: str,
    rm: ReturnMetrics,
    risk: RiskMetrics,
    sm: StabilityMetrics,
    weights: ScoringWeights,
) -> StrategyScore:
    """
    Apply the TASK-140 composite formula to one strategy.

    score = w_sharpe     * sharpe
          + w_return     * total_return
          - w_drawdown   * abs(max_drawdown)
          + w_consistency * consistency_score

    Contributions are stored separately so callers can see which term
    drove a high or low score — useful for experiment analysis.
    """
    sharpe_contrib = weights.w_sharpe * risk.sharpe_ratio
    return_contrib = weights.w_return * rm.total_return
    drawdown_contrib = -weights.w_drawdown * abs(risk.max_drawdown)
    consistency_contrib = weights.w_consistency * sm.consistency_score

    score = sharpe_contrib + return_contrib + drawdown_contrib + consistency_contrib

    return StrategyScore(
        strategy_id=strategy_id,
        score=score,
        sharpe_contrib=sharpe_contrib,
        return_contrib=return_contrib,
        drawdown_contrib=drawdown_contrib,
        consistency_contrib=consistency_contrib,
    )


def rank_strategies(
    *,
    results: list[dict],
    weights: ScoringWeights,
    top_n: int | None = None,
) -> RankedStrategies:
    """
    Score and rank a list of strategies descending by composite score.

    Each entry in results must contain:
        strategy_id  str
        rm           ReturnMetrics
        risk         RiskMetrics
        sm           StabilityMetrics

    top_n limits the returned list. None returns all strategies scored.
    Strategies are expected to have already passed apply_filters() —
    this function does not re-run filters.
    """
    scores: list[StrategyScore] = [
        score_strategy(
            strategy_id=r["strategy_id"],
            rm=r["rm"],
            risk=r["risk"],
            sm=r["sm"],
            weights=weights,
        )
        for r in results
    ]

    scores.sort(key=lambda s: s.score, reverse=True)

    if top_n is not None:
        scores = scores[:top_n]

    return RankedStrategies(
        strategies=scores,
        top_n=top_n,
        weights=weights,
    )
