# tests/execution/test_drawdown_scaling_service.py

from __future__ import annotations

from decimal import Decimal

import pytest

from autonomous_trading_platform.execution.services.drawdown_scaling_service import (
    DrawdownCurveType,
    DrawdownScalingConfig,
    DrawdownScalingResult,
    DrawdownScalingService,
)

STRATEGY = "test-strategy"
ONE = Decimal("1")
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(**kwargs) -> DrawdownScalingService:
    """Build a service with default linear config, optionally overriding fields."""
    return DrawdownScalingService(config=DrawdownScalingConfig(**kwargs))


def scalar(result: DrawdownScalingResult) -> float:
    return float(result.drawdown_scalar)


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestDrawdownScalingServiceConstruction:
    def test_default_config_is_valid(self) -> None:
        svc = DrawdownScalingService()
        assert svc.enabled is True

    def test_invalid_scaling_start_raises(self) -> None:
        with pytest.raises(ValueError, match="scaling_start_utilization"):
            DrawdownScalingService(config=DrawdownScalingConfig(scaling_start_utilization=1.0))

    def test_negative_scaling_start_raises(self) -> None:
        with pytest.raises(ValueError, match="scaling_start_utilization"):
            DrawdownScalingService(config=DrawdownScalingConfig(scaling_start_utilization=-0.1))

    def test_invalid_min_floor_raises(self) -> None:
        with pytest.raises(ValueError, match="min_floor"):
            DrawdownScalingService(config=DrawdownScalingConfig(min_floor=1.0))

    def test_negative_min_floor_raises(self) -> None:
        with pytest.raises(ValueError, match="min_floor"):
            DrawdownScalingService(config=DrawdownScalingConfig(min_floor=-0.1))


# ---------------------------------------------------------------------------
# Disabled mode — backward compatibility
# ---------------------------------------------------------------------------


class TestDisabledMode:
    def test_disabled_returns_scalar_one(self) -> None:
        svc = make_service(enabled=False)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False
        assert result.hard_limit_reached is False

    def test_disabled_returns_scalar_one_even_at_limit(self) -> None:
        svc = make_service(enabled=False)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.05,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False

    def test_disabled_utilization_reported_as_zero(self) -> None:
        svc = make_service(enabled=False)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_utilization == 0.0


# ---------------------------------------------------------------------------
# Linear curve — default behaviour
# ---------------------------------------------------------------------------


class TestLinearCurve:
    """
    Default formula (scaling_start=0.5, linear):
        utilization = realized / max
        scalar = 1.0 when utilization <= 0.5
        scalar = max(0, 1 - 2 * (utilization - 0.5)) when utilization > 0.5
    """

    def test_zero_drawdown_produces_full_scalar(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.0,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False

    def test_below_threshold_no_scaling(self) -> None:
        # 40% utilization (2% drawdown / 5% limit)
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.02,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False
        assert abs(result.drawdown_utilization - 0.40) < 1e-9

    def test_exactly_at_threshold_no_scaling(self) -> None:
        # 50% utilization — right on the boundary, no tapering yet
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.025,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False

    def test_75_pct_utilization_produces_half_scalar(self) -> None:
        # 75% → t = 0.5 in the scaling range → scalar = 0.5
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.0375,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scaling_applied is True
        assert abs(scalar(result) - 0.5) < 1e-5

    def test_100_pct_utilization_produces_zero_scalar(self) -> None:
        # Exactly at the limit
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.05,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ZERO
        assert result.drawdown_scaling_applied is True
        assert result.hard_limit_reached is True

    def test_beyond_limit_also_zeroes_scalar(self) -> None:
        # drawdown exceeds the limit
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.06,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ZERO
        assert result.hard_limit_reached is True

    def test_allocation_decreases_smoothly_between_50_and_100(self) -> None:
        svc = make_service()
        max_dd = 0.10
        utilizations = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]
        scalars = []
        for u in utilizations:
            result = svc.compute_scalar(
                strategy_id=STRATEGY,
                realized_drawdown=u * max_dd,
                max_drawdown_allowed=max_dd,
            )
            scalars.append(scalar(result))

        # Strictly decreasing
        for i in range(1, len(scalars)):
            assert scalars[i] < scalars[i - 1], (
                f"scalar not decreasing: {scalars[i]} >= {scalars[i - 1]} "
                f"at utilization {utilizations[i]:.0%}"
            )

    def test_example_from_spec_4_pct_drawdown_5_pct_limit(self) -> None:
        # Spec example: drawdown=4%, limit=5% → 80% utilization
        # scalar = 1 - 2*(0.80 - 0.50) = 1 - 0.60 = 0.40
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scaling_applied is True
        assert abs(scalar(result) - 0.4) < 1e-5

    def test_scalar_never_exceeds_one(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=-0.01,  # negative drawdown (gain) — clamped to 0
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE


# ---------------------------------------------------------------------------
# Linear curve with custom scaling_start
# ---------------------------------------------------------------------------


class TestLinearCurveCustomStart:
    def test_scaling_starts_at_custom_threshold(self) -> None:
        svc = make_service(scaling_start_utilization=0.8)
        # 70% utilization — below the 80% start threshold → no scaling
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.07,
            max_drawdown_allowed=0.10,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False

    def test_scaling_applies_above_custom_threshold(self) -> None:
        svc = make_service(scaling_start_utilization=0.8)
        # 90% utilization — halfway through the [80%, 100%] range → scalar ≈ 0.5
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.09,
            max_drawdown_allowed=0.10,
        )
        assert result.drawdown_scaling_applied is True
        assert abs(scalar(result) - 0.5) < 1e-5


# ---------------------------------------------------------------------------
# Min floor
# ---------------------------------------------------------------------------


class TestMinFloor:
    def test_floor_prevents_full_taper_before_hard_limit(self) -> None:
        svc = make_service(min_floor=0.10)
        # 90% utilization (linear) → raw scalar = 0.2, floor = 0.1 → scalar = 0.2
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.09,
            max_drawdown_allowed=0.10,
        )
        # raw = 1 - 2*(0.9-0.5) = 0.2 > floor → floor not active yet
        assert abs(scalar(result) - 0.2) < 1e-5

    def test_floor_clamps_at_high_utilization(self) -> None:
        svc = make_service(min_floor=0.25)
        # 95% utilization → raw linear scalar = 1 - 2*(0.95-0.5) = 0.1 < 0.25 floor
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.095,
            max_drawdown_allowed=0.10,
        )
        assert abs(scalar(result) - 0.25) < 1e-5
        assert result.drawdown_scaling_applied is True

    def test_floor_not_applied_at_hard_limit(self) -> None:
        # Hard limit (utilization=1.0) forces scalar to 0 even if floor > 0
        svc = make_service(min_floor=0.25)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.10,
            max_drawdown_allowed=0.10,
        )
        assert result.drawdown_scalar == ZERO
        assert result.hard_limit_reached is True


# ---------------------------------------------------------------------------
# Exponential curve
# ---------------------------------------------------------------------------


class TestExponentialCurve:
    def test_below_threshold_returns_full_scalar(self) -> None:
        svc = make_service(curve_type=DrawdownCurveType.EXPONENTIAL)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.025,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE

    def test_mid_range_is_below_linear_at_same_utilization(self) -> None:
        # Exponential decays faster than linear — scalar should be lower
        max_dd = 0.10
        # 75% utilization — in the scaling zone
        linear_svc = make_service(curve_type=DrawdownCurveType.LINEAR)
        exp_svc = make_service(curve_type=DrawdownCurveType.EXPONENTIAL)

        linear_result = linear_svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.075,
            max_drawdown_allowed=max_dd,
        )
        exp_result = exp_svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.075,
            max_drawdown_allowed=max_dd,
        )

        # exp(-3 * 0.5) ≈ 0.22, linear = 0.5 → exponential is more aggressive
        assert scalar(exp_result) < scalar(linear_result)

    def test_hard_limit_still_zeroes_scalar(self) -> None:
        svc = make_service(curve_type=DrawdownCurveType.EXPONENTIAL)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.05,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ZERO
        assert result.hard_limit_reached is True


# ---------------------------------------------------------------------------
# Sigmoid curve
# ---------------------------------------------------------------------------


class TestSigmoidCurve:
    def test_below_threshold_returns_full_scalar(self) -> None:
        svc = make_service(curve_type=DrawdownCurveType.SIGMOID)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.02,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE

    def test_midpoint_of_scaling_range_is_approximately_half(self) -> None:
        # t=0.5 → 1/(1+exp(0)) = 0.5
        svc = make_service(curve_type=DrawdownCurveType.SIGMOID)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.075,  # 75% → t=0.5 in [50%, 100%] range
            max_drawdown_allowed=0.10,
        )
        assert abs(scalar(result) - 0.5) < 1e-3

    def test_hard_limit_zeroes_scalar(self) -> None:
        svc = make_service(curve_type=DrawdownCurveType.SIGMOID)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.10,
            max_drawdown_allowed=0.10,
        )
        assert result.drawdown_scalar == ZERO


# ---------------------------------------------------------------------------
# Observability fields
# ---------------------------------------------------------------------------


class TestObservabilityFields:
    def test_result_exposes_all_fields(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.05,
        )
        assert isinstance(result.realized_drawdown, float)
        assert isinstance(result.max_drawdown_allowed, float)
        assert isinstance(result.drawdown_utilization, float)
        assert isinstance(result.drawdown_scalar, Decimal)
        assert isinstance(result.drawdown_scaling_applied, bool)
        assert isinstance(result.hard_limit_reached, bool)

    def test_utilization_is_ratio_of_drawdown_to_limit(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.03,
            max_drawdown_allowed=0.05,
        )
        assert abs(result.drawdown_utilization - 0.6) < 1e-9

    def test_drawdown_fields_populated_when_scaling_disabled(self) -> None:
        svc = make_service(enabled=False)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.05,
        )
        assert result.realized_drawdown == 0.04
        assert result.max_drawdown_allowed == 0.05
        assert result.drawdown_scaling_applied is False

    def test_hard_limit_reached_false_when_below_limit(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.05,
        )
        assert result.hard_limit_reached is False

    def test_hard_limit_reached_true_at_limit(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.05,
            max_drawdown_allowed=0.05,
        )
        assert result.hard_limit_reached is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_invalid_zero_limit_returns_full_scalar(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.0,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False

    def test_invalid_negative_limit_returns_full_scalar(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=-0.05,
        )
        assert result.drawdown_scalar == ONE

    def test_negative_drawdown_treated_as_zero_utilization(self) -> None:
        # Negative drawdown (strategy in profit) should not trigger scaling
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=-0.02,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE
        assert result.drawdown_scaling_applied is False
        assert result.drawdown_utilization == 0.0

    def test_very_small_drawdown_no_scaling(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=1e-9,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ONE

    def test_scalar_is_decimal_type(self) -> None:
        svc = make_service()
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.04,
            max_drawdown_allowed=0.05,
        )
        assert isinstance(result.drawdown_scalar, Decimal)

    def test_scalar_bounded_to_zero_at_hard_limit(self) -> None:
        svc = make_service()
        # Way past the limit
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=1.0,
            max_drawdown_allowed=0.05,
        )
        assert result.drawdown_scalar == ZERO

    def test_custom_start_zero_scaling_from_inception(self) -> None:
        # scaling_start=0.0 means scaling begins immediately from any drawdown
        svc = make_service(scaling_start_utilization=0.0)
        result = svc.compute_scalar(
            strategy_id=STRATEGY,
            realized_drawdown=0.01,
            max_drawdown_allowed=0.05,
        )
        # 20% utilization, t=0.2, linear scalar = 0.8
        assert result.drawdown_scaling_applied is True
        assert abs(scalar(result) - 0.8) < 1e-5
