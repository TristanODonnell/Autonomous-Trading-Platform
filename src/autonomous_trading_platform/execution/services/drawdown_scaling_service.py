# autonomous_trading_platform/execution/services/drawdown_scaling_service.py

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


class DrawdownCurveType(StrEnum):
    """
    Supported scaling curve shapes.

    LINEAR produces a straight-line ramp from 1.0 to 0.0 across the scaling
    range.  EXPONENTIAL drops more steeply at first, offering more aggressive
    early de-risking.  SIGMOID is gentle in the early scaling region and
    aggressive near the hard limit, which is a natural fit for strategies that
    should be allowed to "breathe" during minor drawdowns.
    """

    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    SIGMOID = "sigmoid"


@dataclass(frozen=True)
class DrawdownScalingConfig:
    """
    Configuration for drawdown-aware allocation scaling.

    enabled: master on/off switch.  When False the service returns scalar=1.0
             for every call and preserves existing behaviour entirely.

    scaling_start_utilization: fraction of max_drawdown_allowed at which
             tapering begins.  Default 0.5 means no scaling below 50%
             drawdown utilization.

    curve_type: shape of the scaling curve across the [start, 1.0] utilization
             range.  All curves produce 1.0 at the start threshold and approach
             0.0 (or min_floor) at 100% utilization.

    min_floor: the lowest scalar the curve may reach before the hard limit.
             0.0 (default) allows full tapering to zero; a positive floor like
             0.1 keeps a small residual position alive even near the limit.
             The hard limit (utilization >= 1.0) always produces scalar=0.0
             regardless of min_floor.
    """

    enabled: bool = True
    scaling_start_utilization: float = 0.5
    curve_type: DrawdownCurveType = DrawdownCurveType.LINEAR
    min_floor: float = 0.0


@dataclass(frozen=True)
class DrawdownScalingResult:
    """
    Output of DrawdownScalingService.compute_scalar().

    All fields are populated regardless of whether scaling was applied,
    allowing callers to emit complete audit trails and expose the drawdown
    context in AllocationResult or structured logs.
    """

    realized_drawdown: float
    max_drawdown_allowed: float
    drawdown_utilization: float
    drawdown_scalar: Decimal
    drawdown_scaling_applied: bool
    hard_limit_reached: bool


class DrawdownScalingService:
    """
    Computes a drawdown_scalar ∈ [0, 1] based on how close a strategy's
    realized peak-to-trough drawdown is to its configured maximum.

    Default (linear) behaviour:
        0–50% utilization  →  scalar = 1.0  (full size, no tapering)
        75% utilization    →  scalar = 0.5
        100% utilization   →  scalar = 0.0  (hard-limit companion)

    The scalar is multiplied into the position notional *before* broker
    submission, producing a smooth capital taper as drawdown deepens.  This
    supplements, but does not replace, the existing hard drawdown block:
    when utilization reaches 100% the scalar is forced to zero; the upstream
    hard block may still reject the order entirely.

    Design mirrors VolatilityScalingService / SharpeScalingService so all
    three scalars compose deterministically in PositionSizer.
    """

    def __init__(self, config: DrawdownScalingConfig | None = None) -> None:
        self._config = config or DrawdownScalingConfig()

        if not (0.0 <= self._config.scaling_start_utilization < 1.0):
            raise ValueError(
                f"scaling_start_utilization must be in [0, 1), "
                f"got {self._config.scaling_start_utilization}"
            )
        if not (0.0 <= self._config.min_floor < 1.0):
            raise ValueError(f"min_floor must be in [0, 1), got {self._config.min_floor}")

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def compute_scalar(
        self,
        *,
        strategy_id: str,
        realized_drawdown: float,
        max_drawdown_allowed: float,
    ) -> DrawdownScalingResult:
        """
        Compute the drawdown scalar for a strategy.

        realized_drawdown: current peak-to-trough drawdown as a positive
            fraction (e.g. 0.04 for a 4% drawdown).
        max_drawdown_allowed: configured maximum from CapitalAllocationPolicies
            or AllocationOverrides (e.g. 0.05 for a 5% limit).

        Returns DrawdownScalingResult with the scalar and all audit fields.
        """
        if not self._config.enabled:
            logger.debug(
                "DRAWDOWN_SCALING_DISABLED",
                extra={"strategy_id": strategy_id},
            )
            return DrawdownScalingResult(
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                drawdown_utilization=0.0,
                drawdown_scalar=ONE,
                drawdown_scaling_applied=False,
                hard_limit_reached=False,
            )

        if max_drawdown_allowed <= 0:
            # Defensive: a zero or negative limit is a configuration error.
            # Log a warning and skip scaling rather than producing NaN or
            # division-by-zero — fail-open so the strategy can still trade.
            logger.warning(
                "drawdown_scaling.invalid_limit",
                extra={
                    "strategy_id": strategy_id,
                    "max_drawdown_allowed": max_drawdown_allowed,
                },
            )
            return DrawdownScalingResult(
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                drawdown_utilization=0.0,
                drawdown_scalar=ONE,
                drawdown_scaling_applied=False,
                hard_limit_reached=False,
            )

        utilization = max(0.0, realized_drawdown / max_drawdown_allowed)

        # Hard limit: at or past the configured maximum — scalar forced to zero.
        if utilization >= 1.0:
            logger.info(
                "DRAWDOWN_LIMIT_REACHED",
                extra={
                    "strategy_id": strategy_id,
                    "realized_drawdown": round(realized_drawdown, 6),
                    "max_drawdown_allowed": round(max_drawdown_allowed, 6),
                    "drawdown_utilization": round(utilization, 4),
                    "drawdown_scalar": 0.0,
                },
            )
            return DrawdownScalingResult(
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=max_drawdown_allowed,
                drawdown_utilization=utilization,
                drawdown_scalar=ZERO,
                drawdown_scaling_applied=True,
                hard_limit_reached=True,
            )

        start = self._config.scaling_start_utilization

        if utilization <= start:
            # Below the scaling threshold — full position size.
            scalar_float = 1.0
        else:
            # t ∈ (0, 1]: how far through the scaling range [start, 1.0] we are.
            scaling_range = 1.0 - start
            t = (utilization - start) / scaling_range

            if self._config.curve_type == DrawdownCurveType.LINEAR:
                scalar_float = 1.0 - t

            elif self._config.curve_type == DrawdownCurveType.EXPONENTIAL:
                # exp(-3t): ~1.0 at t=0, ~0.22 at t=0.5, ~0.05 at t=1.
                scalar_float = math.exp(-3.0 * t)

            elif self._config.curve_type == DrawdownCurveType.SIGMOID:
                # Inverted sigmoid centred at t=0.5.
                # ~0.997 at t=0, ~0.5 at t=0.5, ~0.003 at t=1.
                scalar_float = 1.0 / (1.0 + math.exp(8.0 * (t - 0.5)))

            else:
                scalar_float = 1.0 - t

            scalar_float = max(self._config.min_floor, scalar_float)

        scaling_applied = scalar_float < 1.0
        scalar_decimal = Decimal(str(round(scalar_float, 6)))

        if scaling_applied:
            logger.info(
                "DRAWDOWN_SCALING_APPLIED",
                extra={
                    "strategy_id": strategy_id,
                    "realized_drawdown": round(realized_drawdown, 6),
                    "max_drawdown_allowed": round(max_drawdown_allowed, 6),
                    "drawdown_utilization": round(utilization, 4),
                    "drawdown_scalar": round(scalar_float, 4),
                    "curve_type": self._config.curve_type.value,
                },
            )
        else:
            logger.debug(
                "drawdown_scaling.no_scaling_needed",
                extra={
                    "strategy_id": strategy_id,
                    "drawdown_utilization": round(utilization, 4),
                },
            )

        return DrawdownScalingResult(
            realized_drawdown=realized_drawdown,
            max_drawdown_allowed=max_drawdown_allowed,
            drawdown_utilization=utilization,
            drawdown_scalar=scalar_decimal,
            drawdown_scaling_applied=scaling_applied,
            hard_limit_reached=False,
        )
