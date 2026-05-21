"""
Parameter sensitivity analysis infrastructure.

Measures how robust a strategy's performance is to changes in its parameters.

For each tunable parameter the analyzer:
  1. Generates N evenly-spaced values across [min_value, max_value]
  2. Evaluates performance (via a caller-supplied run_fn) at each value
  3. Computes a sensitivity_score = std(Sharpe) / (range(Sharpe) + ε)
     and a stability_score = 1 − sensitivity_score
  4. Identifies the "stability region" — the contiguous range of parameter
     values where Sharpe stays above a minimum acceptable threshold

The run_fn abstraction keeps this service simulation-agnostic: it accepts a
callable that maps a parameter dict to (sharpe, drawdown, total_return).  This
makes it trivially testable with a simple mock and lets the orchestrator supply
a real SimulationRunner-backed callable in production.

Overall stability_score = mean(stability per parameter).

Disabled parameters (no min/max range defined, or not tunable) are skipped.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autonomous_trading_platform.strategy.registry.parameter_metadata import ParameterSpec


_EPSILON = 1e-9
_DEFAULT_RELATIVE_STEP = 0.25  # ±25 % if no min/max defined


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


RunFn = Callable[[dict[str, Any]], tuple[float, float, float]]
"""Callable(parameters) -> (sharpe, max_drawdown, total_return)."""


@dataclass(frozen=True)
class ParameterSweepPoint:
    parameter_value: float
    sharpe: float
    max_drawdown: float
    total_return: float


@dataclass(frozen=True)
class ParameterSensitivityResult:
    """Sensitivity analysis result for a single parameter."""

    parameter_name: str
    reference_value: float
    min_value: float
    max_value: float
    sweep_points: list[ParameterSweepPoint]
    sensitivity_score: float  # std(Sharpe) / (range(Sharpe) + ε); [0, 1] via clamp
    stability_score: float  # 1 − sensitivity_score
    stability_region_min: float | None  # contiguous acceptable range
    stability_region_max: float | None
    is_stable: bool  # stability_score >= stability_threshold
    warnings: list[str] = field(default_factory=list)

    @property
    def sharpe_values(self) -> list[float]:
        return [p.sharpe for p in self.sweep_points]


@dataclass(frozen=True)
class SensitivityProfile:
    """
    Full parameter sensitivity profile for one strategy at one configuration.

    overall_stability_score = mean(stability_score per parameter).
    An overall_stability_score of 0.8+ is generally considered stable.
    """

    strategy_id: str
    parameter_results: dict[str, ParameterSensitivityResult]
    overall_stability_score: float
    most_sensitive_parameter: str | None
    most_stable_parameter: str | None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "overall_stability_score": round(self.overall_stability_score, 4),
            "most_sensitive_parameter": self.most_sensitive_parameter,
            "most_stable_parameter": self.most_stable_parameter,
            "parameters": {
                name: {
                    "sensitivity_score": round(r.sensitivity_score, 4),
                    "stability_score": round(r.stability_score, 4),
                    "stability_region": [r.stability_region_min, r.stability_region_max],
                    "is_stable": r.is_stable,
                    "warnings": r.warnings,
                }
                for name, r in self.parameter_results.items()
            },
            "warnings": self.warnings,
        }


class ParameterSensitivityAnalyzer:
    """
    Analyses parameter sensitivity for a strategy.

    Parameters
    ----------
    stability_threshold
        Minimum stability_score for a parameter to be considered "stable".
    min_acceptable_sharpe
        Sharpe below this value in the sweep marks the parameter value as
        outside the stability region.
    n_steps
        Number of evenly-spaced parameter values to evaluate in each sweep.
    max_parameters
        Maximum number of tunable parameters to sweep (most sensitive ones
        first if the strategy has more than this limit).
    """

    def __init__(
        self,
        *,
        stability_threshold: float = 0.5,
        min_acceptable_sharpe: float = 0.0,
        n_steps: int = 9,
        max_parameters: int = 5,
    ) -> None:
        self._stability_threshold = stability_threshold
        self._min_sharpe = min_acceptable_sharpe
        self._n_steps = max(3, n_steps)
        self._max_params = max_parameters

    def analyze_parameter(
        self,
        *,
        parameter_name: str,
        parameter_spec: ParameterSpec,
        reference_parameters: dict[str, Any],
        run_fn: RunFn,
    ) -> ParameterSensitivityResult:
        """Sweep one parameter and compute its sensitivity/stability scores."""
        ref_val = float(reference_parameters.get(parameter_name, parameter_spec.default))

        # Determine sweep range
        if parameter_spec.min_value is not None and parameter_spec.max_value is not None:
            lo = float(parameter_spec.min_value)
            hi = float(parameter_spec.max_value)
        else:
            lo = ref_val * (1.0 - _DEFAULT_RELATIVE_STEP)
            hi = ref_val * (1.0 + _DEFAULT_RELATIVE_STEP)

        if lo >= hi:
            lo = ref_val * 0.5
            hi = ref_val * 2.0

        step = (hi - lo) / (self._n_steps - 1) if self._n_steps > 1 else 0.0
        sweep_values = [lo + i * step for i in range(self._n_steps)]

        sweep_points: list[ParameterSweepPoint] = []
        for val in sweep_values:
            params = dict(reference_parameters)
            if parameter_spec.discrete:
                params[parameter_name] = int(round(val))
            else:
                params[parameter_name] = val
            sharpe, drawdown, total_return = run_fn(params)
            sweep_points.append(
                ParameterSweepPoint(
                    parameter_value=val,
                    sharpe=sharpe,
                    max_drawdown=drawdown,
                    total_return=total_return,
                )
            )

        sharpes = [p.sharpe for p in sweep_points]

        if len(sharpes) < 2:
            sensitivity = 0.0
        else:
            sharpe_range = max(sharpes) - min(sharpes)
            sensitivity = _clamp(statistics.stdev(sharpes) / (sharpe_range + _EPSILON))

        stability = 1.0 - sensitivity
        is_stable = stability >= self._stability_threshold

        # Stability region: largest contiguous block with Sharpe >= min_acceptable
        acceptable = [p.parameter_value for p in sweep_points if p.sharpe >= self._min_sharpe]
        stab_min: float | None = min(acceptable) if acceptable else None
        stab_max: float | None = max(acceptable) if acceptable else None

        warnings: list[str] = []
        if sensitivity > 0.7:
            warnings.append(
                f"Parameter '{parameter_name}' has high sensitivity ({sensitivity:.2f}) — "
                "small changes cause large performance swings."
            )
        if not acceptable:
            warnings.append(
                f"No sweep value for '{parameter_name}' met the min_acceptable_sharpe "
                f"({self._min_sharpe}) — parameter is outside any stable region."
            )

        return ParameterSensitivityResult(
            parameter_name=parameter_name,
            reference_value=ref_val,
            min_value=lo,
            max_value=hi,
            sweep_points=sweep_points,
            sensitivity_score=sensitivity,
            stability_score=stability,
            stability_region_min=stab_min,
            stability_region_max=stab_max,
            is_stable=is_stable,
            warnings=warnings,
        )

    def build_profile(
        self,
        *,
        strategy_id: str,
        parameter_specs: tuple[ParameterSpec, ...],
        reference_parameters: dict[str, Any],
        run_fn: RunFn,
    ) -> SensitivityProfile:
        """Build a full sensitivity profile by sweeping all tunable parameters."""
        tunable = [s for s in parameter_specs if s.tunable][: self._max_params]

        if not tunable:
            return SensitivityProfile(
                strategy_id=strategy_id,
                parameter_results={},
                overall_stability_score=1.0,
                most_sensitive_parameter=None,
                most_stable_parameter=None,
                warnings=["No tunable parameters found — sensitivity analysis skipped."],
            )

        results: dict[str, ParameterSensitivityResult] = {}
        profile_warnings: list[str] = []

        for spec in tunable:
            try:
                result = self.analyze_parameter(
                    parameter_name=spec.name,
                    parameter_spec=spec,
                    reference_parameters=reference_parameters,
                    run_fn=run_fn,
                )
                results[spec.name] = result
                profile_warnings.extend(result.warnings)
            except Exception as exc:
                profile_warnings.append(f"Parameter '{spec.name}' sweep failed: {exc}")

        if not results:
            return SensitivityProfile(
                strategy_id=strategy_id,
                parameter_results={},
                overall_stability_score=0.0,
                most_sensitive_parameter=None,
                most_stable_parameter=None,
                warnings=profile_warnings,
            )

        stability_scores = {name: r.stability_score for name, r in results.items()}
        overall = statistics.mean(stability_scores.values())

        most_sensitive = min(stability_scores, key=lambda k: stability_scores[k])
        most_stable = max(stability_scores, key=lambda k: stability_scores[k])

        if overall < 0.5:
            profile_warnings.append(
                f"Overall parameter stability score is low ({overall:.2f}) — "
                "strategy may be over-fit to specific parameter values."
            )

        return SensitivityProfile(
            strategy_id=strategy_id,
            parameter_results=results,
            overall_stability_score=overall,
            most_sensitive_parameter=most_sensitive,
            most_stable_parameter=most_stable,
            warnings=profile_warnings,
        )
