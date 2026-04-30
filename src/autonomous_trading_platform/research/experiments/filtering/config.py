"""
Per-experiment filter thresholds and scoring weights.

Consumed by:
    filters.py  → FilterConfig
    scoring.py  → ScoringWeights

Both are frozen dataclasses: hashable, immutable, JSON-serialisable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FilterConfig:
    """
    Pass/fail thresholds applied by filters.py. All comparisons are inclusive.
    Fields default to sensible baselines; override only what the experiment needs.

    Core
    ----
    min_sharpe              float   minimum annualised Sharpe                default 1.0
    max_drawdown            float   worst allowed drawdown (negative)        default -0.20
    min_trades              int     minimum closed round-trips               default 30
    min_consistency_score   float   minimum fraction of green windows        default 0.5

    Trade quality
    -------------
    min_profit_factor       float   gross_profit / gross_loss >= this        default 1.0
    min_win_rate            float   disabled by default (0.0)                default 0.0

    Stability
    ---------
    max_return_variance     float|None   cap on windowed return variance     default None (off)

    Return
    ------
    min_total_return        float   minimum full-window return               default 0.0

    Robustness (TASK-139)
    ---------------------
    robustness_min_sharpe               per-window Sharpe floor             default 0.0 (off)
    robustness_min_profitable_windows   absolute window count floor         default 0   (off)
    """

    # Core
    min_sharpe: float = 1.0
    max_drawdown: float = -0.20
    min_trades: int = 30
    min_consistency_score: float = 0.5

    # Trade quality
    min_profit_factor: float = 1.0
    min_win_rate: float = 0.0

    # Stability
    max_return_variance: float | None = None

    # Return
    min_total_return: float = 0.0

    # Robustness
    robustness_min_sharpe: float = 0.0
    robustness_min_profitable_windows: int = 0


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """
    Weights for the TASK-140 composite scoring formula:

        score = w_sharpe     * sharpe
              + w_return     * total_return
              - w_drawdown   * abs(max_drawdown)   ← note subtraction
              + w_consistency * consistency_score

    Weights must be >= 0. They don't need to sum to 1 but doing so
    makes scores directly interpretable as a weighted average.

    Defaults: Sharpe-heavy (0.4) → return (0.3) → drawdown penalty (0.2) → consistency (0.1)
    """

    w_sharpe: float = 0.4
    w_return: float = 0.3
    w_drawdown: float = 0.2
    w_consistency: float = 0.1

    def __post_init__(self) -> None:
        for name, val in (
            ("w_sharpe", self.w_sharpe),
            ("w_return", self.w_return),
            ("w_drawdown", self.w_drawdown),
            ("w_consistency", self.w_consistency),
        ):
            if val < 0.0:
                raise ValueError(f"ScoringWeights.{name} must be >= 0, got {val}.")
