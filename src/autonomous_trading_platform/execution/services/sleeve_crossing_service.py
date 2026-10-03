"""
Internal crossing between strategy sleeves.

When one strategy wants to buy a symbol in the same cycle another wants to sell
it, sending both orders to the broker would round-trip shares through the market
(and can trip broker wash-trade checks). Instead the overlapping quantity is
transferred between the two sleeves at the current price and only the residual
goes to the broker. Every residual order still belongs to exactly one strategy.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)


@dataclass(frozen=True)
class PlannedCross:
    cross_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    buyer_strategy_id: str
    seller_strategy_id: str


@dataclass(frozen=True)
class CrossingPlan:
    residual_intents: list[OrderIntent]
    crosses: list[PlannedCross]


class SleeveCrossingService:
    def __init__(self, construction_service: PortfolioConstructionService) -> None:
        self._construction = construction_service

    def plan(
        self,
        intents: list[OrderIntent],
        *,
        prices: dict[str, float],
        run_id: UUID,
    ) -> CrossingPlan:
        by_symbol: dict[str, list[OrderIntent]] = defaultdict(list)
        for intent in intents:
            by_symbol[intent.symbol].append(intent)

        residual: list[OrderIntent] = []
        crosses: list[PlannedCross] = []
        for symbol in sorted(by_symbol):
            group = by_symbol[symbol]
            buys = sorted((i for i in group if i.side == Side.BUY), key=_key)
            sells = sorted((i for i in group if i.side == Side.SELL), key=_key)
            price = prices.get(symbol)
            if not buys or not sells or price is None:
                residual.extend(group)
                continue

            remaining = {i.intent_id: _qty(i) for i in group}
            for buy in buys:
                for sell in sells:
                    if buy.strategy_id == sell.strategy_id:
                        continue
                    quantity = min(remaining[buy.intent_id], remaining[sell.intent_id])
                    if quantity <= 0:
                        continue
                    crosses.append(
                        PlannedCross(
                            cross_id=uuid5(
                                NAMESPACE_URL,
                                f"cross:{run_id}:{symbol}:{buy.intent_id}:{sell.intent_id}",
                            ).hex,
                            symbol=symbol,
                            quantity=quantity,
                            price=Decimal(str(price)),
                            buyer_strategy_id=buy.strategy_id,
                            seller_strategy_id=sell.strategy_id,
                        )
                    )
                    remaining[buy.intent_id] -= quantity
                    remaining[sell.intent_id] -= quantity

            for intent in group:
                left = remaining[intent.intent_id]
                if left == _qty(intent):
                    residual.append(intent)
                elif left > 0:
                    residual.append(self._reduced(intent, left, prices=prices, run_id=run_id))

        return CrossingPlan(residual_intents=residual, crosses=crosses)

    def _reduced(
        self,
        intent: OrderIntent,
        quantity: Decimal,
        *,
        prices: dict[str, float],
        run_id: UUID,
    ) -> OrderIntent:
        signed = int(quantity) if intent.side == Side.BUY else -int(quantity)
        rebuilt = self._construction.build_order_intent(
            delta={"symbol": intent.symbol, "delta_qty": signed},
            prices=prices,
            run_id=run_id,
            strategy_id=intent.strategy_id,
            bar_timestamp=intent.bar_timestamp,
            now=intent.timestamp,
        )
        rebuilt.metadata = {
            **(intent.metadata or {}),
            "crossed_qty": str(_qty(intent) - quantity),
            "pre_cross_qty": str(intent.qty),
        }
        return rebuilt


def _qty(intent: OrderIntent) -> Decimal:
    return Decimal(intent.qty) if intent.qty is not None else Decimal("0")


def _key(intent: OrderIntent) -> tuple[str, str]:
    return (intent.strategy_id, str(intent.intent_id))
