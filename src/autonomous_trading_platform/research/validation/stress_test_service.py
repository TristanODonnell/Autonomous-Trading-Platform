"""
Deterministic stress-testing infrastructure.

Stress tests operate on an already-computed equity curve by applying
parameterised return transformations.  Each scenario modifies the bar-level
returns and recomputes Sharpe/drawdown/return from the resulting equity.

This design means stress tests are:
  - fast  (no re-simulation required)
  - deterministic  (transform functions have no randomness)
  - explainable  (each scenario has a clear economic interpretation)

Built-in scenarios
------------------
volatility_spike_2x    Multiply bar returns by 2 (double volatility)
volatility_spike_3x    Multiply bar returns by 3 (triple volatility)
return_shock_neg5pct   Subtract a −5 % one-time shock at the scenario midpoint
return_shock_neg10pct  Subtract a −10 % shock
drawdown_amplification Double the magnitude of all negative bar returns
trend_reversal         Negate all bar returns (worst-case trend reversal)
liquidity_collapse     Apply 50 bps per-bar cost (liquidity premium shock)

A strategy "survives" a scenario when:
  stressed Sharpe >= min_sharpe_threshold  AND
  stressed drawdown >= max_drawdown_threshold  (drawdown is negative)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    return_metrics as compute_return_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    risk_metrics as compute_risk_metrics,
)


@dataclass(frozen=True)
class StressScenario:
    name: str
    description: str
    shock_type: str
    parameters: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StressTestResult:
    scenario_name: str
    description: str
    original_sharpe: float
    stressed_sharpe: float
    original_drawdown: float
    stressed_drawdown: float
    original_return: float
    stressed_return: float
    sharpe_degradation: float  # (original − stressed) / (|original| + ε); positive = degradation
    survived: bool


@dataclass(frozen=True)
class StressTestSummary:
    strategy_id: str
    n_scenarios: int
    n_survived: int
    survival_rate: float
    avg_sharpe_degradation: float
    worst_scenario: str | None
    scenario_results: list[StressTestResult]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in scenario catalogue
# ---------------------------------------------------------------------------

BUILT_IN_SCENARIOS: list[StressScenario] = [
    StressScenario(
        "volatility_spike_2x",
        "2× bar-return volatility",
        "vol_multiplier",
        {"factor": 2.0},
    ),
    StressScenario(
        "volatility_spike_3x",
        "3× bar-return volatility",
        "vol_multiplier",
        {"factor": 3.0},
    ),
    StressScenario(
        "return_shock_neg5pct",
        "Single −5 % return shock at midpoint",
        "one_time_shock",
        {"shock": -0.05},
    ),
    StressScenario(
        "return_shock_neg10pct",
        "Single −10 % return shock at midpoint",
        "one_time_shock",
        {"shock": -0.10},
    ),
    StressScenario(
        "drawdown_amplification",
        "Negative bar returns amplified 2×",
        "downside_amplification",
        {"factor": 2.0},
    ),
    StressScenario(
        "trend_reversal",
        "All bar returns sign-flipped",
        "sign_flip",
        {},
    ),
    StressScenario(
        "liquidity_collapse",
        "50 bps per-bar additional slippage cost",
        "slippage_penalty",
        {"penalty_bps": 50.0},
    ),
]

_EPSILON = 1e-9


class StressTestService:
    """
    Applies deterministic stress scenarios to a simulation equity curve and
    aggregates the results into a StressTestSummary.

    Parameters
    ----------
    min_sharpe_threshold
        Minimum Sharpe ratio for a stressed run to be considered "survived".
    max_drawdown_threshold
        Maximum acceptable drawdown (negative decimal, e.g. −0.30).
    scenarios
        Which scenarios to run.  Defaults to BUILT_IN_SCENARIOS.
    """

    def __init__(
        self,
        *,
        min_sharpe_threshold: float = 0.0,
        max_drawdown_threshold: float = -0.40,
        scenarios: list[StressScenario] | None = None,
    ) -> None:
        self._min_sharpe = min_sharpe_threshold
        self._max_drawdown = max_drawdown_threshold
        self._scenarios = scenarios if scenarios is not None else list(BUILT_IN_SCENARIOS)

    def run(
        self,
        *,
        equity_curve: pd.DataFrame,
        strategy_id: str,
        bars_per_year: int = BARS_PER_YEAR,
    ) -> StressTestSummary:
        if equity_curve is None or equity_curve.empty:
            raise ValueError("equity_curve must be non-empty for stress testing")
        if "timestamp" not in equity_curve.columns or "equity" not in equity_curve.columns:
            raise ValueError("equity_curve must have 'timestamp' and 'equity' columns")

        curve = equity_curve.sort_values("timestamp").reset_index(drop=True)
        rm_orig = compute_risk_metrics(curve, bars_per_year=bars_per_year)
        ret_orig = compute_return_metrics(curve)
        orig_sharpe = rm_orig.sharpe_ratio
        orig_dd = rm_orig.max_drawdown
        orig_ret = ret_orig.total_return

        results: list[StressTestResult] = []
        for scenario in self._scenarios:
            stressed = self._apply_scenario(curve, scenario)
            try:
                rm_s = compute_risk_metrics(stressed, bars_per_year=bars_per_year)
                ret_s = compute_return_metrics(stressed)
                stressed_sharpe = rm_s.sharpe_ratio
                stressed_dd = rm_s.max_drawdown
                stressed_ret = ret_s.total_return
            except Exception:
                stressed_sharpe = 0.0
                stressed_dd = -1.0
                stressed_ret = -1.0

            degradation = (orig_sharpe - stressed_sharpe) / (abs(orig_sharpe) + _EPSILON)
            survived = stressed_sharpe >= self._min_sharpe and stressed_dd >= self._max_drawdown
            results.append(
                StressTestResult(
                    scenario_name=scenario.name,
                    description=scenario.description,
                    original_sharpe=orig_sharpe,
                    stressed_sharpe=stressed_sharpe,
                    original_drawdown=orig_dd,
                    stressed_drawdown=stressed_dd,
                    original_return=orig_ret,
                    stressed_return=stressed_ret,
                    sharpe_degradation=degradation,
                    survived=survived,
                )
            )

        n_survived = sum(1 for r in results if r.survived)
        survival_rate = n_survived / len(results) if results else 0.0
        avg_degradation = statistics.mean(r.sharpe_degradation for r in results) if results else 0.0

        worst: str | None = None
        if results:
            worst = max(results, key=lambda r: r.sharpe_degradation).scenario_name

        warnings: list[str] = []
        if survival_rate < 0.5:
            warnings.append(
                f"Strategy survived only {survival_rate:.0%} of stress scenarios — "
                "performance may be fragile."
            )
        if worst and any(r.scenario_name == worst and r.sharpe_degradation > 2.0 for r in results):
            warnings.append(
                f"Extreme degradation under '{worst}' — Sharpe collapsed by >2× under stress."
            )

        return StressTestSummary(
            strategy_id=strategy_id,
            n_scenarios=len(results),
            n_survived=n_survived,
            survival_rate=survival_rate,
            avg_sharpe_degradation=avg_degradation,
            worst_scenario=worst,
            scenario_results=results,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Scenario application
    # ------------------------------------------------------------------

    def _apply_scenario(self, curve: pd.DataFrame, scenario: StressScenario) -> pd.DataFrame:
        equity = curve["equity"].to_numpy(dtype=np.float64)
        if len(equity) < 2:
            return curve.copy()

        returns = np.diff(equity) / equity[:-1]

        if scenario.shock_type == "vol_multiplier":
            factor = scenario.parameters.get("factor", 2.0)
            returns = returns * factor

        elif scenario.shock_type == "one_time_shock":
            shock = scenario.parameters.get("shock", -0.05)
            mid = len(returns) // 2
            returns[mid] += shock

        elif scenario.shock_type == "downside_amplification":
            factor = scenario.parameters.get("factor", 2.0)
            returns = np.where(returns < 0, returns * factor, returns)

        elif scenario.shock_type == "sign_flip":
            returns = -returns

        elif scenario.shock_type == "slippage_penalty":
            penalty_bps = scenario.parameters.get("penalty_bps", 50.0)
            penalty_per_bar = penalty_bps / 10_000.0
            returns = returns - penalty_per_bar

        else:
            return curve.copy()

        # Rebuild equity curve from transformed returns
        new_equity = np.empty(len(equity), dtype=np.float64)
        new_equity[0] = equity[0]
        for i, r in enumerate(returns):
            new_equity[i + 1] = new_equity[i] * (1.0 + r)

        stressed = curve.copy()
        stressed["equity"] = new_equity
        return stressed
