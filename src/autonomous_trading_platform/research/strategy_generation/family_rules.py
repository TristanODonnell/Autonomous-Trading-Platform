"""Family rules the research draw applies before a config is made and hashed.

Research plan Step 1b row 4. The draw (random and grid) passes every candidate's
parameters through :func:`research_form`. It may refuse them (``ValueError``: the draw
counts a rejection and, under G2, draws again) or rewrite them into a canonical form, so
that settings which behave the same get one id and the engine's same-hash check drops the
repeat. Only the draw does this: the parameter schemas, stored rows and hand-written
configs are untouched, so nothing that already exists changes its id or its meaning.

Factor (``factor_based``), the only family with rules today:

- G4, a rule of the draw: a config whose buy threshold is above every score its weights
  can reach can never buy, and is refused (the 2026-11-23 baseline had two, ``0a2d5aed``
  and ``8c07c0ec``, each a simulation spent to find out).
- G8, a canonical form: a part with weight 0 adds nothing to the score, so its window is
  set to the research floor; then each threshold is moved to the reachable score that
  picks the same bars (the smallest at or above the buy threshold, the largest at or
  below the sell threshold), so ``4fa1f4c7`` and ``89197bfc`` (one behaviour in every run
  of the baseline) are one config.
"""

from __future__ import annotations

from itertools import product
from typing import Any

from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    MIN_RESEARCH_WINDOW_BARS,
)
from autonomous_trading_platform.strategy.implementations.factor_based_strategy import (
    FactorBasedStrategy,
)

FACTOR_TYPE = "factor_based"

# Each part's weight and the window it reads, as FactorBasedStrategy names them.
_FACTOR_PARTS: tuple[tuple[str, str], ...] = (
    ("momentum_weight", "momentum_lookback"),
    ("mean_reversion_weight", "mean_reversion_window"),
    ("volume_weight", "volume_window"),
    ("volatility_weight", "volatility_window"),
)

# The values each part's score can take, read from the strategy's own scoring functions
# (a test checks these are all of them), so the rule follows the strategy.
_MOMENTUM_SCORES = tuple(
    sorted({FactorBasedStrategy._normalize_signed(x) for x in (-1.0, 0.0, 1.0)})
)
_MEAN_REVERSION_SCORES = tuple(
    sorted({FactorBasedStrategy._score_mean_reversion(z) for z in (-3.0, 0.0, 3.0)})
)
_VOLUME_SCORES = tuple(sorted({FactorBasedStrategy._score_volume(v) for v in (0.0, 1.0, 2.0)}))
_VOLATILITY_SCORE_MOVING = FactorBasedStrategy._score_volatility(1.0)
_VOLATILITY_SCORE_FLAT = FactorBasedStrategy._score_volatility(0.0)


def research_form(strategy_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """The parameters the draw makes a config from: refused (``ValueError``) when the
    family's rule says the config cannot work, otherwise in the family's canonical form.
    Families without rules come back unchanged."""
    if strategy_type == FACTOR_TYPE:
        return _factor_form(params)
    return params


def factor_reachable_scores(params: dict[str, Any]) -> list[float]:
    """Every score a bar can give these weights, sorted, computed as the strategy
    computes it (same terms, same order, so the floats compare exactly).

    Volatility scores 0 only on a stretch where every close in its window is the same.
    When that window is no shorter than mean reversion's, the mean-reversion window is
    flat too, its z-score is undefined and the strategy gives no signal: so there the
    flat score is not reachable. (Exact up to one floating-point corner: z-score is
    undefined only at a standard deviation of exactly 0, and a run of identical closes
    can leave float noise in one window's mean and not another's. It needs
    ``volatility_window`` identical closes in a row.)"""
    volatility_scores = [_VOLATILITY_SCORE_MOVING]
    if int(params["volatility_window"]) < int(params["mean_reversion_window"]):
        volatility_scores.append(_VOLATILITY_SCORE_FLAT)
    momentum_weight = float(params["momentum_weight"])
    mean_reversion_weight = float(params["mean_reversion_weight"])
    volume_weight = float(params["volume_weight"])
    volatility_weight = float(params["volatility_weight"])
    return sorted(
        {
            momentum_score * momentum_weight
            + mean_reversion_score * mean_reversion_weight
            + volume_score * volume_weight
            + volatility_score * volatility_weight
            for momentum_score, mean_reversion_score, volume_score, volatility_score in product(
                _MOMENTUM_SCORES, _MEAN_REVERSION_SCORES, _VOLUME_SCORES, volatility_scores
            )
        }
    )


def _factor_form(params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params)
    # G8, first part (measured, not proven: research plan G8). A part with weight 0 adds
    # nothing to the score; it can still withhold a signal while its indicator has no
    # value, which on the G5 bars never changed a decision (0 of 120 sampled groups).
    # The floor is the shortest window the research draw uses.
    for weight, window in _FACTOR_PARTS:
        if weight in out and window in out and float(out[weight]) == 0.0:
            out[window] = MIN_RESEARCH_WINDOW_BARS

    # The thresholds, from the scores of the config as it will run (windows above).
    scores = factor_reachable_scores(out)
    buy = float(out["buy_score_threshold"])
    sell = float(out["sell_score_threshold"])

    # G4: nothing the weights can reach is at or above the buy threshold.
    reachable_buys = [s for s in scores if s >= buy]
    if not reachable_buys:
        raise ValueError(
            f"factor_based can never buy: buy_score_threshold {buy:g} is above the highest "
            f"score its weights can reach ({scores[-1]:g})"
        )

    # G8, second part (exact): the strategy buys at score >= buy and sells at
    # score <= sell, and the score only takes the values above, so any threshold with no
    # reachable score between it and the canonical one picks the same bars.
    out["buy_score_threshold"] = reachable_buys[0]
    reachable_sells = [s for s in scores if s <= sell]
    # None reachable: the config never sells; every such config gets the same value.
    out["sell_score_threshold"] = reachable_sells[-1] if reachable_sells else scores[0] - 1.0
    return out
