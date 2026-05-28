# autonomous_trading_platform/execution/services/position_sizer.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from autonomous_trading_platform.execution.services.drawdown_scaling_service import (
    DrawdownScalingService,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.metrics import (
    ratp_drawdown_scaling_applied_total,
    ratp_strategy_drawdown_scalar,
    ratp_strategy_drawdown_utilization,
)
from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SizingResult:
    """
    Complete output from a single PositionSizer.compute_quantity() call.

    quantity:         Whole-share quantity to trade; 0 means skip this order.
    base_notional:    allocated * capital_fraction before any scaling layers.
    final_notional:   Notional after vol/Sharpe combined scalar, drawdown scalar,
                      and position-size caps.
    combined_scalar:  The pre-multiplied vol/Sharpe scalar forwarded by
                      PortfolioConstructionService; None when no scaling was
                      applied at that layer.
    scaling_applied:  True when final_notional < base_notional due to any
                      scaling factor (vol, drawdown, or caps).
    """

    quantity: int
    base_notional: Decimal
    final_notional: Decimal
    combined_scalar: Decimal | None
    scaling_applied: bool


class PositionSizer:
    def __init__(
        self,
        portfolio_engine: IAllocationProvider,
        capital_fraction: Decimal = ONE,
        min_notional_usd: Decimal = Decimal("1.00"),
        max_symbol_exposure_usd: Decimal | None = None,
        drawdown_scaling_service: DrawdownScalingService | None = None,
    ) -> None:

        if not (ZERO < capital_fraction <= ONE):
            raise ValueError(f"capital_fraction must be in (0, 1], got {capital_fraction}")
        if min_notional_usd < ZERO:
            raise ValueError(f"min_notional_usd must be >= 0, got {min_notional_usd}")
        if max_symbol_exposure_usd is not None and max_symbol_exposure_usd <= ZERO:
            raise ValueError(
                f"max_symbol_exposure_usd must be positive, got {max_symbol_exposure_usd}"
            )

        self._portfolio_engine = portfolio_engine
        self._capital_fraction = capital_fraction
        self._min_notional_usd = min_notional_usd
        self._max_symbol_exposure_usd = max_symbol_exposure_usd
        self._drawdown_scaling = drawdown_scaling_service

    def compute_quantity(
        self,
        *,
        strategy_id: str,
        symbol: str,
        current_price: Decimal,
        approval_status: GovernanceState | None = None,
        performance_tier: str | None = None,
        combined_scalar: Decimal | None = None,
        realized_drawdown: float | None = None,
    ) -> SizingResult:
        """
        Compute the whole-share quantity for a position.

        combined_scalar is the pre-multiplied vol/Sharpe scalar forwarded from
        PortfolioConstructionService.  It must be in (0, 1] when provided —
        we only scale positions down, never up.  The clamp is enforced by
        PortfolioConstructionService before this call; raising here is a
        final safety net.

        Scaling composition (outermost first):
            final = base * combined_scalar * drawdown_scalar
            (then capped by max_position_size_usd and max_symbol_exposure_usd)
        """

        if current_price <= ZERO:
            raise ValueError(f"current_price must be positive for '{symbol}', got {current_price}")

        allocation = self._portfolio_engine.get_allocation(
            strategy_id=strategy_id,
            approval_status=approval_status,
            performance_tier=performance_tier,
        )

        allocated = Decimal(str(allocation.allocated_capital_usd))

        # base_notional: capital committed before any risk scaling
        base_notional = allocated * self._capital_fraction
        target_notional = base_notional

        # Apply combined vol/Sharpe scalar (FINDING-18 — replaces old TASK-193 vol_scalar)
        if combined_scalar is not None:
            if not (ZERO < combined_scalar <= ONE):
                raise ValueError(f"combined_scalar must be in (0, 1], got {combined_scalar}")
            target_notional = target_notional * combined_scalar

        # Apply drawdown scalar (FINDING-12) — strategy-level taper based on
        # how close realized drawdown is to the policy-configured maximum.
        # Must run after combined_scalar so the scaling composition is deterministic:
        #   final_notional = base * capital_fraction * combined_scalar * drawdown_scalar
        # The max_drawdown_allowed comes from the already-resolved allocation so
        # override and policy merging is respected without a second lookup.
        if self._drawdown_scaling is not None and realized_drawdown is not None:
            notional_before_drawdown = target_notional
            dd_result = self._drawdown_scaling.compute_scalar(
                strategy_id=strategy_id,
                realized_drawdown=realized_drawdown,
                max_drawdown_allowed=allocation.max_drawdown_allowed,
            )

            ratp_strategy_drawdown_utilization.record(
                dd_result.drawdown_utilization,
                {"strategy_id": strategy_id},
            )
            ratp_strategy_drawdown_scalar.record(
                float(dd_result.drawdown_scalar),
                {"strategy_id": strategy_id},
            )

            if dd_result.drawdown_scaling_applied:
                target_notional = target_notional * dd_result.drawdown_scalar
                ratp_drawdown_scaling_applied_total.add(
                    1,
                    {
                        "strategy_id": strategy_id,
                        "hard_limit_reached": str(dd_result.hard_limit_reached),
                    },
                )
                logger.info(
                    "position_sizer.drawdown_scaling_applied",
                    extra={
                        "strategy_id": strategy_id,
                        "symbol": symbol,
                        "realized_drawdown": round(dd_result.realized_drawdown, 6),
                        "max_drawdown_allowed": round(dd_result.max_drawdown_allowed, 6),
                        "drawdown_utilization": round(dd_result.drawdown_utilization, 4),
                        "drawdown_scalar": float(dd_result.drawdown_scalar),
                        "hard_limit_reached": dd_result.hard_limit_reached,
                        "notional_before": float(notional_before_drawdown),
                        "notional_after": float(target_notional),
                    },
                )

        # Apply max_position_size_usd — policy-level cap per strategy position
        if allocation.max_position_size_usd is not None:
            max_pos = Decimal(str(allocation.max_position_size_usd))
            if target_notional > max_pos:
                target_notional = max_pos
                logger.debug(
                    "position_sizer.capped_by_max_position",
                    extra={
                        "strategy_id": strategy_id,
                        "symbol": symbol,
                        "target_notional": float(target_notional),
                        "max_position_size_usd": float(max_pos),
                    },
                )

        # Apply max_symbol_exposure_usd — TASK-192 settings-level cap per symbol
        if (
            self._max_symbol_exposure_usd is not None
            and target_notional > self._max_symbol_exposure_usd
        ):
            target_notional = self._max_symbol_exposure_usd
            logger.debug(
                "position_sizer.capped_by_symbol_exposure",
                extra={
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "target_notional": float(target_notional),
                    "max_symbol_exposure_usd": float(self._max_symbol_exposure_usd),
                },
            )

        # Below min notional — skip
        if target_notional < self._min_notional_usd:
            logger.warning(
                "position_sizer.below_min_notional",
                extra={
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "target_notional": float(target_notional),
                    "min_notional_usd": float(self._min_notional_usd),
                },
            )
            return SizingResult(
                quantity=0,
                base_notional=base_notional,
                final_notional=target_notional,
                combined_scalar=combined_scalar,
                scaling_applied=target_notional < base_notional,
            )

        # Convert notional to whole shares
        quantity = (target_notional / current_price).to_integral_value(rounding=ROUND_DOWN)

        if quantity < ONE:
            logger.warning(
                "position_sizer.insufficient_for_one_share",
                extra={
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "target_notional": float(target_notional),
                    "current_price": float(current_price),
                },
            )
            return SizingResult(
                quantity=0,
                base_notional=base_notional,
                final_notional=target_notional,
                combined_scalar=combined_scalar,
                scaling_applied=target_notional < base_notional,
            )

        return SizingResult(
            quantity=int(quantity),
            base_notional=base_notional,
            final_notional=target_notional,
            combined_scalar=combined_scalar,
            scaling_applied=target_notional < base_notional,
        )
