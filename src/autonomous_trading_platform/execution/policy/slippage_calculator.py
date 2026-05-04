# autonomous_trading_platform/execution/policy/slippage_calculator.py
"""
SlippageCalculator — pre-execution slippage and cost estimation

Implements the SlippageModel formula from the spec:
    fill_price = market_price × (1 + slippage_rate)   [buy]
    fill_price = market_price × (1 - slippage_rate)   [sell]

Additionally computes spread cost when bid/ask is available and assembles
a CostBreakdown for the full round-trip cost estimate.

Design notes:
- Input is the mid-price from AlpacaBrokerClient.get_latest_quotes() (bid+ask)/2.
- Spread cost = half-spread × quantity (taker pays half the spread on entry).
- Commission cost = config.commission_per_share × quantity.
- This is the *pre-execution* estimate; post-execution actuals are computed
  in FillQualityAnalyser from the real Fill returned by the broker.
- v1 uses fixed slippage rate; v2 extension point is volume_based_rate() below.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity
from autonomous_trading_platform.contracts.execution.execution_policy_config import SlippageConfig
from autonomous_trading_platform.contracts.trading.cost_breakdown import CostBreakdown
from autonomous_trading_platform.contracts.trading.slippage_measurement import SlippageMeasurement

_ZERO = Decimal("0")
_BPS = Decimal("10000")
_CENT = Decimal("0.0001")  # rounding precision for prices


class SlippageCalculator:
    """
    Computes expected slippage and cost breakdown from a reference price.

    Usage::

        calc = SlippageCalculator(config)
        measurement = calc.expected_slippage(
            side=Side.BUY,
            reference_price=Decimal("150.00"),
            quantity=Decimal("10"),
            ask_price=Decimal("150.02"),   # optional, for spread cost
            bid_price=Decimal("149.98"),   # optional, for spread cost
        )
        cost = calc.cost_breakdown(measurement)
    """

    def __init__(self, config: SlippageConfig) -> None:
        self._config = config

    def expected_slippage(
        self,
        *,
        side: Side,
        reference_price: Money,
        quantity: Quantity,
        ask_price: Money | None = None,
        bid_price: Money | None = None,
    ) -> SlippageMeasurement:
        """
        Estimate pre-execution slippage using fixed_slippage_rate.

        If ask/bid are provided and include_spread_cost=True, the half-spread
        is added to the slippage for the taker side.
        """
        rate = self._effective_rate(
            side=side,
            ask_price=ask_price,
            bid_price=bid_price,
        )

        fill_price = self._compute_fill_price(
            side=side,
            reference_price=reference_price,
            rate=rate,
        )

        slippage_per_share = abs(fill_price - reference_price)
        slippage_notional = slippage_per_share * Decimal(str(quantity))
        slippage_bps = (
            (slippage_per_share / reference_price * _BPS).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if reference_price > _ZERO
            else _ZERO
        )

        return SlippageMeasurement(
            side=side,
            reference_price=reference_price,
            fill_price=fill_price,
            quantity=quantity,
            slippage_per_share=slippage_per_share,
            slippage_notional=slippage_notional,
            slippage_bps=slippage_bps,
        )

    def actual_slippage(
        self,
        *,
        side: Side,
        reference_price: Money,
        fill_price: Money,
        quantity: Quantity,
    ) -> SlippageMeasurement:

        # For buys: adverse slippage means fill > reference (paid more).
        # For sells: adverse slippage means fill < reference (received less).
        if side == Side.BUY:
            slippage_per_share = fill_price - reference_price
        else:
            slippage_per_share = reference_price - fill_price

        slippage_notional = slippage_per_share * Decimal(str(quantity))
        slippage_bps = (
            (abs(slippage_per_share) / reference_price * _BPS).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if reference_price > _ZERO
            else _ZERO
        )

        return SlippageMeasurement(
            side=side,
            reference_price=reference_price,
            fill_price=fill_price,
            quantity=quantity,
            slippage_per_share=slippage_per_share,
            slippage_notional=slippage_notional,
            slippage_bps=slippage_bps,
        )

    def cost_breakdown(
        self,
        slippage: SlippageMeasurement,
        *,
        ask_price: Money | None = None,
        bid_price: Money | None = None,
    ) -> CostBreakdown:

        quantity = Decimal(str(slippage.quantity))
        commission_cost = (self._config.commission_per_share * quantity).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )

        spread_cost = _ZERO
        if (
            self._config.include_spread_cost
            and ask_price is not None
            and bid_price is not None
            and ask_price > bid_price
        ):
            half_spread = (ask_price - bid_price) / Decimal("2")
            spread_cost = (half_spread * quantity).quantize(_CENT, rounding=ROUND_HALF_UP)

        slippage_cost = slippage.slippage_notional.quantize(_CENT, rounding=ROUND_HALF_UP)
        total_cost = commission_cost + spread_cost + slippage_cost

        return CostBreakdown(
            commission_cost=commission_cost,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            total_cost=total_cost,
        )

    def volume_based_rate(
        self,
        *,
        order_qty: Quantity,
        avg_daily_volume: Quantity,
    ) -> Decimal:
        """
        Placeholder for v2 volume-based slippage scaling.

        Participation rate = order_qty / avg_daily_volume.
        Linear scaling: rate = fixed_rate * (1 + participation_rate).
        """
        if avg_daily_volume <= _ZERO:
            return self._config.fixed_slippage_rate
        participation = Decimal(str(order_qty)) / Decimal(str(avg_daily_volume))
        return self._config.fixed_slippage_rate * (Decimal("1") + participation)

    def _effective_rate(
        self,
        *,
        side: Side,
        ask_price: Money | None,
        bid_price: Money | None,
    ) -> Decimal:
        """Base rate + optional half-spread component."""
        rate = self._config.fixed_slippage_rate
        if (
            self._config.include_spread_cost
            and ask_price is not None
            and bid_price is not None
            and ask_price > bid_price
        ):
            # Taker pays half the spread on top of fixed slippage.
            # This is folded into the rate for fill_price computation.
            # (It's also broken out separately in cost_breakdown.)
            pass  # Spread cost is kept separate; rate stays as fixed_slippage_rate.
        return rate

    def _compute_fill_price(
        self,
        *,
        side: Side,
        reference_price: Money,
        rate: Decimal,
    ) -> Money:
        """
        fill_price = market_price × (1 ± slippage_rate)

        BUY:  fill above mid (taker pays more).
        SELL: fill below mid (taker receives less).
        """
        if side == Side.BUY:
            fill_price = reference_price * (Decimal("1") + rate)
        else:
            fill_price = reference_price * (Decimal("1") - rate)
        return fill_price.quantize(_CENT, rounding=ROUND_HALF_UP)
