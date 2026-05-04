"""
TWAPSlicer — splits an order into equal time-weighted slices

Architecture note:
    In the current platform the broker (Alpaca) receives a single parent order.
    TWAP slicing here produces an OrderSlice schedule that is:
      1. Embedded in OrderIntent.metadata["twap_slices"] for audit/analytics.
      2. Available for future child-order submission when the broker abstraction
         gains multi-order support

    The parent OrderIntent is submitted as-is (full qty); the slice schedule
    represents the *intent* for how execution should unfold.  This is the
    correct approach for the current single-broker, single-submit architecture.

Slice quantity:
    Each slice gets floor(total_qty / num_slices); remainder goes to the
    last slice to ensure the full order is accounted for.

Timing:
    slice_index=0 fires at offset=0 (immediately).
    slice_index=k fires at offset = k × (window_minutes / num_slices).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

from autonomous_trading_platform.contracts.common.enums import OrderType
from autonomous_trading_platform.contracts.execution.execution_policy_config import TWAPConfig
from autonomous_trading_platform.contracts.execution.execution_policy_result import OrderSlice
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent


class TWAPSlicer:
    def build_slices(
        self,
        *,
        intent: OrderIntent,
        config: TWAPConfig,
        mid_price: Decimal | None = None,
    ) -> list[OrderSlice]:
        """
        Build the TWAP slice schedule.

        Args:
            intent:    The (already order-type-resolved) intent.
            config:    TWAPConfig with window_minutes and num_slices.
            mid_price: Current mid-price; used to set limit_price on limit slices.

        Returns:
            Ordered list of OrderSlice, one per time bucket.
        """
        total_qty = self._resolve_quantity(intent)
        if total_qty <= Decimal("0"):
            return []

        slices: list[OrderSlice] = []
        interval = config.window_minutes / config.num_slices

        base_qty = (total_qty / Decimal(str(config.num_slices))).quantize(
            Decimal("1"), rounding=ROUND_DOWN
        )
        remainder = total_qty - base_qty * Decimal(str(config.num_slices))

        weight_base = base_qty / total_qty
        weight_last = (base_qty + remainder) / total_qty

        for i in range(config.num_slices):
            is_last = i == config.num_slices - 1
            qty = base_qty + (remainder if is_last else Decimal("0"))
            weight = weight_last if is_last else weight_base

            limit_price: Decimal | None = None
            if config.slice_order_type == OrderType.LIMIT.value and mid_price is not None:
                from decimal import ROUND_HALF_UP

                offset = config.slice_limit_offset_bps / Decimal("10000")
                from autonomous_trading_platform.contracts.common.enums import Side

                if intent.side == Side.BUY:
                    limit_price = (mid_price * (Decimal("1") + offset)).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )
                else:
                    limit_price = (mid_price * (Decimal("1") - offset)).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )

            slices.append(
                OrderSlice(
                    slice_index=i,
                    scheduled_offset_minutes=round(i * interval, 4),
                    quantity=qty,
                    weight=weight.quantize(Decimal("0.0001")),
                    limit_price=limit_price,
                )
            )

        return slices

    def to_metadata(self, slices: list[OrderSlice]) -> dict[str, Any]:
        """Serialise slices for embedding in OrderIntent.metadata."""
        return {
            "twap_slices": [
                {
                    "slice_index": s.slice_index,
                    "scheduled_offset_minutes": s.scheduled_offset_minutes,
                    "quantity": str(s.quantity),
                    "weight": str(s.weight),
                    "limit_price": str(s.limit_price) if s.limit_price else None,
                }
                for s in slices
            ]
        }

    @staticmethod
    def _resolve_quantity(intent: OrderIntent) -> Decimal:
        """Extract a usable quantity from qty or notional."""
        if intent.qty is not None:
            return Decimal(str(intent.qty))
        # notional-based: quantity isn't known until fill; return 0 to skip slicing
        return Decimal("0")
