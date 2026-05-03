# autonomous_trading_platform/execution/services/position_sizer.py

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from autonomous_trading_platform.goverance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.models import AllocationResult
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


class PositionSizer:
    def __init__(
        self,
        portfolio_engine: PortfolioEngine,
        capital_fraction: Decimal = ONE,
        min_notional_usd: Decimal = Decimal("1.00"),
        max_symbol_exposure_usd: Decimal | None = None,
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

    def compute_quantity(
        self,
        *,
        strategy_id: str,
        approval_status: GovernanceState,
        symbol: str,
        current_price: Decimal,
        performance_tier: str | None = None,
        vol_scalar: Decimal | None = None,  # TASK-193 hook
    ) -> int:

        if current_price <= ZERO:
            raise ValueError(f"current_price must be positive for '{symbol}', got {current_price}")

        allocation: AllocationResult = self._portfolio_engine.get_allocation(
            strategy_id=strategy_id,
            approval_status=approval_status,
            performance_tier=performance_tier,
        )

        allocated = Decimal(str(allocation.allocated_capital_usd))

        # Apply capital_fraction
        target_notional = allocated * self._capital_fraction

        # Apply vol_scalar if provided (TASK-193)
        if vol_scalar is not None:
            if not (ZERO < vol_scalar <= ONE):
                raise ValueError(f"vol_scalar must be in (0, 1], got {vol_scalar}")
            target_notional = target_notional * vol_scalar

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
            return 0

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
            return 0

        return int(quantity)
