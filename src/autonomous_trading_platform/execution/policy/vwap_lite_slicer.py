"""
VWAPLiteSlicer — splits an order proportionally to a volume profile

'Lite' because we use a static intraday volume profile rather than live
order-book data.  Two built-in profiles are supported:

  uniform    — equal weight per slice (identical to TWAP by quantity).
  u_shaped   — higher volume weight at open and close, lower mid-day.
               Models the classic U-shaped intraday volume curve for US equities.

The same architectural note as TWAPSlicer applies: the parent intent is
submitted whole; the slice schedule is for analytics and future child routing.

Volume profiles:
    Given N slices the weight for slice i under u_shaped is:
        raw_weight[i] = (dist_from_midpoint[i] + 1) ^ 2
        weight[i]     = raw_weight[i] / sum(raw_weight)
    where dist_from_midpoint[i] = abs(i - (N-1)/2).

    This gives a symmetric U-shape peaking at slice 0 and slice N-1.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

from autonomous_trading_platform.contracts.execution.execution_policy_config import VWAPLiteConfig
from autonomous_trading_platform.contracts.execution.execution_policy_result import OrderSlice
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent


class VWAPLiteSlicer:
    def build_slices(
        self,
        *,
        intent: OrderIntent,
        config: VWAPLiteConfig,
    ) -> list[OrderSlice]:
        """
        Build the VWAP-lite slice schedule.

        Args:
            intent:  The (already order-type-resolved) intent.
            config:  VWAPLiteConfig with num_slices, window_minutes, volume_profile.

        Returns:
            Ordered list of OrderSlice weighted by volume profile.
        """
        total_qty = self._resolve_quantity(intent)
        if total_qty <= Decimal("0"):
            return []

        weights = self._compute_weights(config)
        interval = config.window_minutes / config.num_slices
        slices: list[OrderSlice] = []

        accumulated = Decimal("0")
        for i, weight in enumerate(weights):
            is_last = i == config.num_slices - 1
            if is_last:
                qty = total_qty - accumulated
            else:
                qty = (total_qty * weight).quantize(Decimal("1"), rounding=ROUND_DOWN)
            accumulated += qty

            slices.append(
                OrderSlice(
                    slice_index=i,
                    scheduled_offset_minutes=round(i * interval, 4),
                    quantity=qty,
                    weight=weight.quantize(Decimal("0.0001")),
                    limit_price=None,  # VWAP-lite always uses market child orders
                )
            )

        return slices

    def to_metadata(self, slices: list[OrderSlice], profile: str) -> dict[str, Any]:
        return {
            "vwap_lite_slices": [
                {
                    "slice_index": s.slice_index,
                    "scheduled_offset_minutes": s.scheduled_offset_minutes,
                    "quantity": str(s.quantity),
                    "weight": str(s.weight),
                }
                for s in slices
            ],
            "volume_profile": profile,
        }

    def _compute_weights(self, config: VWAPLiteConfig) -> list[Decimal]:
        n = config.num_slices
        if config.volume_profile == "u_shaped":
            return self._u_shaped_weights(n)
        return self._uniform_weights(n)

    @staticmethod
    def _uniform_weights(n: int) -> list[Decimal]:
        base = Decimal("1") / Decimal(str(n))
        weights = [base] * n
        # Adjust last slice to ensure sum == 1.
        total = sum(weights)
        weights[-1] += Decimal("1") - total
        return weights

    @staticmethod
    def _u_shaped_weights(n: int) -> list[Decimal]:
        mid = (n - 1) / 2.0
        raw = [Decimal(str((abs(i - mid) + 1) ** 2)) for i in range(n)]
        total = sum(raw)
        weights = [r / total for r in raw]
        # Normalise so sum is exactly 1.
        diff = Decimal("1") - sum(weights)
        weights[-1] += diff
        return weights

    @staticmethod
    def _resolve_quantity(intent: OrderIntent) -> Decimal:
        if intent.qty is not None:
            return Decimal(str(intent.qty))
        return Decimal("0")
