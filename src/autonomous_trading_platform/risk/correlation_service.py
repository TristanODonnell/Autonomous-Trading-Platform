from __future__ import annotations

import logging
import math
from decimal import Decimal

from autonomous_trading_platform.goverance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider
from autonomous_trading_platform.portfolio.models import AllocationResult

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation for two equal-length return series."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    return cov / denom if denom > 0 else 0.0


def compute_pairwise_correlations(
    returns: dict[str, list[float]],
) -> dict[tuple[str, str], float]:
    """
    Upper-triangle pairwise Pearson correlations for all keys in `returns`.
    Returns {(id_a, id_b): correlation} for all a < b (lexicographic).
    Series are trimmed to the shortest common length before computing.
    """
    ids = sorted(returns)
    result: dict[tuple[str, str], float] = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ra = returns[a]
            rb = returns[b]
            n = min(len(ra), len(rb))
            result[(a, b)] = _pearson(ra[-n:], rb[-n:]) if n >= 2 else 0.0
    return result


def average_pairwise_correlation(returns: dict[str, list[float]]) -> float:
    """
    Average of all pairwise correlations — simple diversification score.
    0 = perfectly uncorrelated, 1 = all move together.
    Used by RiskAlertService for the correlation threshold check.
    """
    pairs = compute_pairwise_correlations(returns)
    if not pairs:
        return 0.0
    return sum(pairs.values()) / len(pairs)


def correlation_penalty_scalar(
    symbol: str,
    symbol_returns: list[float],
    other_returns: dict[str, list[float]],
    correlation_threshold: float = 0.7,
    min_scalar: float = 0.5,
) -> Decimal:
    """
    Returns a scalar in [min_scalar, 1.0] penalising a symbol whose
    returns are highly correlated with the rest of the portfolio.

    Algorithm:
        max_corr = max absolute Pearson correlation vs any other symbol
        corr <= threshold  →  scalar = 1.0   (no penalty)
        corr >= 1.0        →  scalar = min_scalar
        otherwise          →  linear interpolation between threshold and 1.0
    """
    if not other_returns:
        return ONE

    max_abs_corr = 0.0
    for other_sym, other_ret in other_returns.items():
        if other_sym == symbol:
            continue
        n = min(len(symbol_returns), len(other_ret))
        if n < 2:
            continue
        corr = abs(_pearson(symbol_returns[-n:], other_ret[-n:]))
        if corr > max_abs_corr:
            max_abs_corr = corr

    if max_abs_corr <= correlation_threshold:
        return ONE

    t = (max_abs_corr - correlation_threshold) / (1.0 - correlation_threshold)
    scalar = 1.0 - t * (1.0 - min_scalar)

    logger.debug(
        "correlation_service.penalty",
        extra={
            "symbol": symbol,
            "max_abs_corr": round(max_abs_corr, 4),
            "correlation_threshold": correlation_threshold,
            "correlation_scalar": round(scalar, 4),
        },
    )

    return Decimal(str(round(scalar, 6)))


class CorrelationAwareAllocationProvider:
    """
    Wraps any IAllocationProvider and down-scales allocated_capital_usd
    for strategies that are highly correlated with each other.

    All governance gates and policy caps from the base provider are
    honoured first — this class only ever reduces, never increases, the
    base allocation.  All non-capital fields on AllocationResult are
    passed through unchanged.

    Usage:
        provider = CorrelationAwareAllocationProvider(
            base_provider=portfolio_engine,
            strategy_returns={"strat_a": [...], "strat_b": [...]},
            correlation_threshold=0.7,
            min_scalar=0.5,
        )
        allocation = provider.get_allocation("strat_a", approval_status=...)
    """

    def __init__(
        self,
        base_provider: IAllocationProvider,
        strategy_returns: dict[str, list[float]],
        correlation_threshold: float = 0.7,
        min_scalar: float = 0.5,
    ) -> None:
        if not (0.0 < correlation_threshold < 1.0):
            raise ValueError(
                f"correlation_threshold must be in (0, 1), got {correlation_threshold}"
            )
        if not (0.0 < min_scalar <= 1.0):
            raise ValueError(f"min_scalar must be in (0, 1], got {min_scalar}")

        self._base = base_provider
        self._strategy_returns = strategy_returns
        self._threshold = correlation_threshold
        self._min_scalar = min_scalar

    @property
    def total_capital(self) -> float:
        return self._base.total_capital

    def get_allocation(
        self,
        strategy_id: str,
        approval_status: GovernanceState | None,
        performance_tier: str | None = None,
    ) -> AllocationResult:

        base = self._base.get_allocation(
            strategy_id=strategy_id,
            approval_status=approval_status,
            performance_tier=performance_tier,
        )

        own_returns = self._strategy_returns.get(strategy_id)
        if not own_returns:
            return base

        others = {sid: rets for sid, rets in self._strategy_returns.items() if sid != strategy_id}

        scalar = correlation_penalty_scalar(
            symbol=strategy_id,
            symbol_returns=own_returns,
            other_returns=others,
            correlation_threshold=self._threshold,
            min_scalar=self._min_scalar,
        )

        if scalar >= ONE:
            return base

        adjusted_capital = float(Decimal(str(base.allocated_capital_usd)) * scalar)

        logger.debug(
            "correlation_aware_allocation.scaled",
            extra={
                "strategy_id": strategy_id,
                "base_capital_usd": base.allocated_capital_usd,
                "adjusted_capital_usd": adjusted_capital,
                "correlation_scalar": float(scalar),
            },
        )

        return AllocationResult(
            strategy_id=base.strategy_id,
            approval_status=base.approval_status,
            max_pct_of_capital=base.max_pct_of_capital,
            max_position_size_usd=base.max_position_size_usd,
            max_drawdown_allowed=base.max_drawdown_allowed,
            allocated_capital_usd=adjusted_capital,
            override_applied=base.override_applied,
            override_id=base.override_id,
            policy_id=base.policy_id,
        )
