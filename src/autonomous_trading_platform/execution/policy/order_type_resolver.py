"""
OrderTypeResolver — decides whether an order should be market or limit

Decision logic:
1. If policy_mode is MARKET  → always market.
2. If policy_mode is LIMIT   → always limit (with computed limit_price).
3. If policy_mode is PASSTHROUGH → honour the OrderType already set on the intent
   by the strategy (no mutation).
4. If policy_mode is TWAP/VWAP_LITE → use the slice_order_type from the
   respective config (defaults to "market" for slices).

For LIMIT orders the limit_price is computed as:
    BUY  limit = mid_price × (1 + offset_bps / 10_000)   [willing to pay up]
    SELL limit = mid_price × (1 - offset_bps / 10_000)   [willing to accept down]

This gives a passive-but-reachable limit that should fill in normal conditions
while providing price protection against adverse moves between signal and submit.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from autonomous_trading_platform.contracts.common.enums import OrderType, Side
from autonomous_trading_platform.contracts.common.types import Money
from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
    LimitOrderPolicy,
    PolicyMode,
)
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent

_BPS = Decimal("10000")
_CENT = Decimal("0.0001")


class OrderTypeResolver:
    def resolve(
        self,
        *,
        intent: OrderIntent,
        config: ExecutionPolicyConfig,
        mid_price: Money | None,
    ) -> OrderIntent:

        mode = config.policy_mode

        if mode == PolicyMode.PASSTHROUGH:
            return intent  # no mutation; strategy's choice stands

        if mode == PolicyMode.MARKET:
            return self._force_market(intent)

        if mode == PolicyMode.LIMIT:
            return self._force_limit(intent, config.limit_policy, mid_price)

        if mode in (PolicyMode.TWAP, PolicyMode.VWAP_LITE):
            slice_type = config.twap.slice_order_type if mode == PolicyMode.TWAP else "market"
            if slice_type == "limit":
                slice_offset = (
                    config.twap.slice_limit_offset_bps
                    if mode == PolicyMode.TWAP
                    else Decimal("3")  # VWAP-lite default
                )
                return self._force_limit(
                    intent,
                    LimitOrderPolicy(offset_bps=slice_offset),
                    mid_price,
                )
            return self._force_market(intent)

        return intent

    @staticmethod
    def _force_market(intent: OrderIntent) -> OrderIntent:
        """Return a copy of intent with order_type=MARKET and limit_price cleared."""
        if intent.order_type == OrderType.MARKET and intent.limit_price is None:
            return intent  # already market; no copy needed
        return cast(
            OrderIntent,
            intent.model_copy(
                update={"order_type": OrderType.MARKET, "limit_price": None, "stop_price": None}
            ),
        )

    @staticmethod
    def _force_limit(
        intent: OrderIntent,
        policy: LimitOrderPolicy,
        mid_price: Money | None,
    ) -> OrderIntent:

        from autonomous_trading_platform.execution.policy.errors import MissingMarketDataError

        if mid_price is None:
            raise MissingMarketDataError(
                f"Cannot compute limit price for {intent.symbol}: no mid-price available. "
                "Ensure get_latest_quotes() succeeds before applying LIMIT policy."
            )

        offset = policy.offset_bps / _BPS

        if intent.side == Side.BUY:
            limit_price = mid_price * (Decimal("1") + offset)
        else:
            limit_price = mid_price * (Decimal("1") - offset)

        limit_price = limit_price.quantize(_CENT, rounding=ROUND_HALF_UP)

        return cast(
            OrderIntent,
            intent.model_copy(update={"order_type": OrderType.LIMIT, "limit_price": limit_price}),
        )
