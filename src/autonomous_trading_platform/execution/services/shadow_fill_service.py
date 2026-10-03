"""
Shadow fills for on-deck strategies (portfolio rotation step 2).

On-deck orders never reach the broker. They are filled here with the same
simulated execution model the backtest broker uses for real orders, against the
same bar, so a shadow track record is directly comparable with an active
strategy's: identical close-price fills, volume participation cap (partial fills
when a bar is thin) and slippage. The unfilled remainder of a partial fill is
dropped, exactly as the simulated broker drops it.

Bars come from the broker when it is the simulated broker (backtests). Otherwise
(live paper) the latest validated daily bar for the tick date is used; when none
exists yet the order fills at the cycle price with slippage but no volume cap,
since there is no volume to cap against.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.clients.simulated_broker_client import (
    build_platform_execution_service,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)

BarSource = Callable[[str], Any | None]


@dataclass(frozen=True)
class _PriceBar:
    """A bar with only a price: no volume, so no participation cap applies."""

    close: Decimal
    open: Decimal
    timestamp: datetime
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None


@dataclass
class ShadowFillResult:
    fills: list[Fill] = field(default_factory=list)
    # intent_id -> quantity that did not fill (volume cap or no price).
    unfilled_qty: dict[Any, Decimal] = field(default_factory=dict)
    # symbol -> "bar" | "price" | "none": what each symbol was filled against.
    basis: dict[str, str] = field(default_factory=dict)

    @property
    def partial_count(self) -> int:
        filled = {fill.intent_id for fill in self.fills}
        return sum(1 for intent_id in self.unfilled_qty if intent_id in filled)


class ShadowFillService:
    def __init__(
        self,
        *,
        bar_source: BarSource,
        timestamp: datetime,
        execution_service: SimulatedExecutionService | None = None,
    ) -> None:
        self._bar_source = bar_source
        self._timestamp = timestamp
        self._execution = execution_service or build_platform_execution_service()
        self._price_only_execution = build_platform_execution_service(
            max_volume_participation_rate=None
        )
        self._bars: dict[str, Any | None] = {}

    @classmethod
    def for_broker(
        cls, broker_client: Any, *, session: Any, timestamp: datetime
    ) -> ShadowFillService:
        """Fill against the broker's own bars when it is the simulated broker."""
        bar_for = getattr(broker_client, "bar_for", None)
        if bar_for is None:
            from autonomous_trading_platform.execution.clients.simulated_broker_client import (
                SimulatedBrokerClient,
            )

            # Only its bar loader is used: the latest validated bar for the tick date.
            bar_for = SimulatedBrokerClient(
                session=session,
                timestamp=timestamp,
                simulated_execution_service=build_platform_execution_service(),
            ).bar_for
        return cls(bar_source=bar_for, timestamp=timestamp)

    def fill(
        self, intents: list[OrderIntent], *, prices: Mapping[str, float | Decimal]
    ) -> ShadowFillResult:
        result = ShadowFillResult()
        for intent in intents:
            qty = Decimal(intent.qty or 0)
            if qty <= 0:
                continue
            bar = self._bar(intent.symbol, prices)
            if bar is None:
                result.basis[intent.symbol] = "none"
                result.unfilled_qty[intent.intent_id] = qty
                continue
            price_only = isinstance(bar, _PriceBar)
            result.basis[intent.symbol] = "price" if price_only else "bar"
            execution = self._price_only_execution if price_only else self._execution
            batch = execution.fill(order_intents=[intent], bars_at_timestamp={intent.symbol: bar})
            filled = sum((Decimal(f.quantity) for f in batch.fills), Decimal("0"))
            result.fills.extend(batch.fills)
            if filled < qty:
                result.unfilled_qty[intent.intent_id] = qty - filled
        return result

    def _bar(self, symbol: str, prices: Mapping[str, float | Decimal]) -> Any | None:
        if symbol not in self._bars:
            bar = self._bar_source(symbol)
            if bar is None and prices.get(symbol) is not None:
                price = Decimal(str(prices[symbol]))
                bar = _PriceBar(close=price, open=price, timestamp=self._timestamp)
            self._bars[symbol] = bar
        return self._bars[symbol]
