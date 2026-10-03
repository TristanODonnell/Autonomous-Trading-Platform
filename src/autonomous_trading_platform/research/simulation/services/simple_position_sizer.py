# autonomous_trading_platform/research/simulation/services/simple_position_sizer.py

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.sleeve_sizing import position_budget_usd
from autonomous_trading_platform.execution.services.volatility_scaling_service import (
    VolatilityScalingService,
)

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


class SimplePositionSizer:
    """
    Simulation-only position sizer. Pure math — no DB, no governance, no policies.

    Sizes like the platform portfolio cycle (execution/services/sleeve_sizing.py):
    total_capital split equally across universe_size symbols, scaled down by the
    volatility scalar of the symbol's recent closes when a scaling service is given,
    capped at max_symbol_exposure_usd, floored to whole shares.

    Example: $100k / 5 symbols = $20k per symbol; vol scalar 0.8 -> $16k.
             $16k / $150 price = 106 shares.
    """

    def __init__(
        self,
        total_capital: float,
        universe_size: int,
        min_notional_usd: float = 1.0,
        volatility_scaling_service: VolatilityScalingService | None = None,
        max_symbol_exposure_usd: float | None = None,
    ) -> None:
        if universe_size < 1:
            raise ValueError(f"universe_size must be >= 1, got {universe_size}")
        if total_capital <= 0:
            raise ValueError(f"total_capital must be positive, got {total_capital}")

        self._capital_per_symbol = position_budget_usd(Decimal(str(total_capital)), universe_size)
        self._min_notional = Decimal(str(min_notional_usd))
        self._vol_scaling = volatility_scaling_service
        self._max_symbol_exposure = (
            Decimal(str(max_symbol_exposure_usd)) if max_symbol_exposure_usd is not None else None
        )

    @property
    def uses_volatility_scaling(self) -> bool:
        return self._vol_scaling is not None

    def compute_targets(
        self,
        *,
        signals: list[Signal],
        prices: dict[str, float],
        recent_closes: dict[str, list[float]] | None = None,
        capital_scale: Decimal = ONE,
    ) -> dict[str, int]:
        """
        Returns a target position dict: symbol -> whole share quantity.
        SELL/FLAT signals produce 0. BUY signals produce
        floor(capital_per_symbol * capital_scale * vol_scalar, capped / price).

        recent_closes are the closes before the bar (VOL_LOOKBACK_BARS of them) the vol
        scalar needs; capital_scale is the simulation's equity growth so far (equity at
        the previous bar / initial cash) — the platform sizes from current equity.
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

            notional = self._capital_per_symbol * capital_scale
            closes = (recent_closes or {}).get(signal.symbol)
            if self._vol_scaling is not None and closes:
                scalar = self._vol_scaling.compute_scalar(symbol=signal.symbol, closes=closes)
                if scalar is not None:
                    notional = notional * min(scalar, ONE)
            if self._max_symbol_exposure is not None and notional > self._max_symbol_exposure:
                notional = self._max_symbol_exposure

            if notional < self._min_notional:
                targets[signal.symbol] = 0
                continue

            qty = (notional / price).to_integral_value(rounding=ROUND_DOWN)

            targets[signal.symbol] = int(qty) if qty >= ONE else 0

        return targets
