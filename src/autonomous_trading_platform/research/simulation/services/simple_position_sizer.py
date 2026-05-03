# autonomous_trading_platform/research/simulation/services/simple_position_sizer.py

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


class SimplePositionSizer:
    """
    Simulation-only position sizer. Pure math — no DB, no governance, no policies.

    Divides total_capital equally across universe_size symbols so that
    simultaneous BUY signals across the full universe stay within budget.

    Example: $100k / 5 symbols = $20k per symbol.
             $20k / $150 price = 133 shares.
    """

    def __init__(
        self,
        total_capital: float,
        universe_size: int,
        min_notional_usd: float = 1.0,
    ) -> None:
        if universe_size < 1:
            raise ValueError(f"universe_size must be >= 1, got {universe_size}")
        if total_capital <= 0:
            raise ValueError(f"total_capital must be positive, got {total_capital}")

        self._capital_per_symbol = Decimal(str(total_capital)) / Decimal(str(universe_size))
        self._min_notional = Decimal(str(min_notional_usd))

    def compute_targets(
        self,
        *,
        signals: list[Signal],
        prices: dict[str, float],
    ) -> dict[str, int]:
        """
        Returns a target position dict: symbol -> whole share quantity.
        SELL/FLAT signals produce 0. BUY signals produce floor(capital / price).
        """
        targets: dict[str, int] = {}

        for signal in signals:
            if signal.direction in (SignalDirection.SELL, SignalDirection.FLAT):
                targets[signal.symbol] = 0
                continue

            if signal.direction != SignalDirection.BUY:
                logger.warning(
                    "simple_position_sizer.unknown_direction",
                    extra={"symbol": signal.symbol, "direction": str(signal.direction)},
                )
                targets[signal.symbol] = 0
                continue

            raw_price = prices.get(signal.symbol)
            if raw_price is None or raw_price <= 0:
                logger.warning(
                    "simple_position_sizer.missing_price",
                    extra={"symbol": signal.symbol},
                )
                targets[signal.symbol] = 0
                continue

            price = Decimal(str(raw_price))

            if self._capital_per_symbol < self._min_notional:
                targets[signal.symbol] = 0
                continue

            qty = (self._capital_per_symbol / price).to_integral_value(rounding=ROUND_DOWN)

            targets[signal.symbol] = int(qty) if qty >= ONE else 0

        return targets
