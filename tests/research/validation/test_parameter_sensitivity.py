"""Tests for ParameterSensitivityAnalyzer."""

from __future__ import annotations

import math

import pytest

from autonomous_trading_platform.research.validation.parameter_sensitivity_analysis import (
    ParameterSensitivityAnalyzer,
)
from autonomous_trading_platform.strategy.registry.parameter_metadata import (
    ParameterSpec,
    ParameterType,
)


def _make_spec(
    name: str,
    min_val: float,
    max_val: float,
    default: float = 10.0,
    tunable: bool = True,
    discrete: bool = False,
) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        parameter_type=ParameterType.INT if discrete else ParameterType.FLOAT,
        default=default,
        description="test",
        min_value=min_val,
        max_value=max_val,
        discrete=discrete,
        tunable=tunable,
    )


def _flat_run_fn(params: dict) -> tuple[float, float, float]:
    """Performance is flat regardless of parameter value → perfectly stable."""
    return (1.5, -0.10, 0.20)


def _sensitive_run_fn(params: dict) -> tuple[float, float, float]:
    """Performance is very sensitive to 'window' parameter."""
    val = float(params.get("window", 10))
    sharpe = math.sin(val / 5.0) * 3.0  # oscillates wildly
    return (sharpe, -0.15, sharpe * 0.05)


def _monotone_run_fn(params: dict) -> tuple[float, float, float]:
    """Sharpe linearly increases with window — moderate sensitivity."""
    val = float(params.get("window", 10))
    sharpe = val / 20.0
    return (sharpe, -0.10, sharpe * 0.05)


def test_flat_performance_is_stable():
    spec = _make_spec("window", min_val=5.0, max_val=50.0, default=20.0)
    analyzer = ParameterSensitivityAnalyzer(n_steps=9)
    result = analyzer.analyze_parameter(
        parameter_name="window",
        parameter_spec=spec,
        reference_parameters={"window": 20.0},
        run_fn=_flat_run_fn,
    )
    assert result.sensitivity_score == pytest.approx(0.0, abs=1e-6)
    assert result.stability_score == pytest.approx(1.0, abs=1e-6)
    assert result.is_stable


def test_sensitive_parameter_low_stability():
    spec = _make_spec("window", min_val=1.0, max_val=30.0, default=10.0)
    analyzer = ParameterSensitivityAnalyzer(n_steps=9, stability_threshold=0.5)
    result = analyzer.analyze_parameter(
        parameter_name="window",
        parameter_spec=spec,
        reference_parameters={"window": 10.0},
        run_fn=_sensitive_run_fn,
    )
    # Highly oscillating Sharpe → should be unstable
    assert result.sensitivity_score > 0.0
    assert len(result.sweep_points) == 9


def test_stability_region_detected():
    """Monotonically increasing Sharpe — stability region should be [mid, max]."""
    spec = _make_spec("window", min_val=2.0, max_val=40.0, default=20.0)
    analyzer = ParameterSensitivityAnalyzer(
        n_steps=9, min_acceptable_sharpe=0.5, stability_threshold=0.5
    )
    result = analyzer.analyze_parameter(
        parameter_name="window",
        parameter_spec=spec,
        reference_parameters={"window": 20.0},
        run_fn=_monotone_run_fn,
    )
    # Some sweep values should be in the acceptable region
    assert result.stability_region_min is not None
    assert result.stability_region_max is not None


def test_no_range_uses_relative_step():
    """When min/max are None, use ±25% relative step."""
    spec = ParameterSpec(
        name="threshold",
        parameter_type=ParameterType.FLOAT,
        default=0.5,
        description="threshold",
        min_value=None,
        max_value=None,
        tunable=True,
    )
    analyzer = ParameterSensitivityAnalyzer(n_steps=5)
    result = analyzer.analyze_parameter(
        parameter_name="threshold",
        parameter_spec=spec,
        reference_parameters={"threshold": 0.5},
        run_fn=_flat_run_fn,
    )
    assert len(result.sweep_points) == 5
    assert result.min_value == pytest.approx(0.375)
    assert result.max_value == pytest.approx(0.625)


def test_build_profile_skips_non_tunable():
    specs = (
        _make_spec("window", 5.0, 50.0, tunable=True),
        _make_spec("threshold", 0.1, 1.0, tunable=False),
    )
    analyzer = ParameterSensitivityAnalyzer(n_steps=5)
    profile = analyzer.build_profile(
        strategy_id="strat",
        parameter_specs=specs,
        reference_parameters={"window": 20.0, "threshold": 0.5},
        run_fn=_flat_run_fn,
    )
    assert "window" in profile.parameter_results
    assert "threshold" not in profile.parameter_results


def test_no_tunable_specs_returns_perfect_stability():
    specs = (_make_spec("p", 1.0, 10.0, tunable=False),)
    analyzer = ParameterSensitivityAnalyzer()
    profile = analyzer.build_profile(
        strategy_id="s",
        parameter_specs=specs,
        reference_parameters={},
        run_fn=_flat_run_fn,
    )
    assert profile.overall_stability_score == 1.0
    assert profile.most_sensitive_parameter is None


def test_overall_stability_is_mean():
    specs = (
        _make_spec("a", 1.0, 10.0),
        _make_spec("b", 1.0, 10.0),
    )
    analyzer = ParameterSensitivityAnalyzer(n_steps=5)
    profile = analyzer.build_profile(
        strategy_id="s",
        parameter_specs=specs,
        reference_parameters={"a": 5.0, "b": 5.0},
        run_fn=_flat_run_fn,
    )
    expected = (
        profile.parameter_results["a"].stability_score
        + profile.parameter_results["b"].stability_score
    ) / 2.0
    assert profile.overall_stability_score == pytest.approx(expected)


def test_deterministic():
    spec = _make_spec("window", 5.0, 50.0, default=20.0)
    analyzer = ParameterSensitivityAnalyzer(n_steps=7)
    r1 = analyzer.analyze_parameter(
        parameter_name="window",
        parameter_spec=spec,
        reference_parameters={"window": 20.0},
        run_fn=_monotone_run_fn,
    )
    r2 = analyzer.analyze_parameter(
        parameter_name="window",
        parameter_spec=spec,
        reference_parameters={"window": 20.0},
        run_fn=_monotone_run_fn,
    )
    assert r1.sensitivity_score == pytest.approx(r2.sensitivity_score)
    assert r1.stability_score == pytest.approx(r2.stability_score)
