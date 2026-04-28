from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_trading_platform.contracts.common.enums import OrderType, Side, TimeInForce
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService


class PortfolioConstructionService:
    def __init__(self, pre_trade_risk_service: PreTradeRiskService) -> None:
        self.pre_trade_risk_service = pre_trade_risk_service

    def generate_order_intents(
        self,
        signals: list[Signal],
        positions: dict[str, int],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        now: datetime,
    ):
        target_positions = self.position_sizer(signals)
        deltas = self.calculate_deltas(positions, target_positions)
        print("signal symbols:", [signal.symbol for signal in signals])
        print("target_positions:", target_positions)
        print("deltas:", deltas)
        print("prices keys:", list(prices.keys()))
        for delta in deltas:
            order_intent = self.build_order_intent(
                delta=delta,
                prices=prices,
                run_id=run_id,
                strategy_id=strategy_id,
                bar_timestamp=bar_timestamp,
                now=now,
            )
            self.pre_trade_risk_service.assert_order_allowed(order_intent, now=now)
            yield order_intent

    def position_sizer(
        self,
        signals: list[Signal],
    ) -> dict[str, int]:
        target_positions: dict[str, int] = {}

        for signal in signals:
            target_qty = 10
            direction = signal.direction.value.lower()

            if direction in {"long", "buy"}:
                target_positions[signal.symbol] = target_qty
            elif direction in {"sell"}:
                target_positions[signal.symbol] = 0
            elif direction in {"short"}:
                target_positions[signal.symbol] = -target_qty
            else:
                target_positions[signal.symbol] = 0

        return target_positions

    def calculate_deltas(
        self,
        current_positions: dict[str, int],
        target_positions: dict[str, int],
    ) -> list[dict[str, int | str]]:
        deltas: list[dict[str, int | str]] = []

        all_symbols = sorted(set(current_positions) | set(target_positions))
        for symbol in all_symbols:
            current_position = current_positions.get(symbol, 0)

            if hasattr(current_position, "quantity"):
                current_qty = int(current_position.quantity)
            else:
                current_qty = int(current_position)

            target_qty = target_positions.get(symbol, 0)
            delta_qty = target_qty - current_qty

            if delta_qty != 0:
                deltas.append(
                    {
                        "symbol": symbol,
                        "current_qty": current_qty,
                        "target_qty": target_qty,
                        "delta_qty": delta_qty,
                    }
                )

        return deltas

    def build_order_intent(
        self,
        delta: dict[str, Any],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        now: datetime,
    ) -> OrderIntent:
        symbol = str(delta["symbol"])
        delta_qty = int(delta["delta_qty"])

        side = Side.BUY if delta_qty > 0 else Side.SELL
        qty = abs(delta_qty)
        price = Decimal(str(prices[symbol]))

        client_order_id = self._build_client_order_id(
            run_id=run_id,
            strategy_id=strategy_id,
            bar_timestamp=bar_timestamp,
            symbol=symbol,
            side=side,
            qty=qty,
        )
        intent_id = self._build_intent_id(client_order_id=client_order_id)

        return OrderIntent(
            intent_id=intent_id,
            idempotency_key=client_order_id,
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp=now,
            bar_timestamp=bar_timestamp,
            symbol=symbol,
            side=side,
            qty=qty,
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=price,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=client_order_id,
            metadata=None,
        )

    @staticmethod
    def _build_client_order_id(
        *,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        symbol: str,
        side: Side,
        qty: int,
    ) -> str:
        seed = (
            f"run_id={run_id}|"
            f"strategy_id={strategy_id}|"
            f"bar_timestamp={bar_timestamp.isoformat()}|"
            f"symbol={symbol}|"
            f"side={side.value}|"
            f"qty={qty}"
        )
        deterministic_uuid = uuid5(NAMESPACE_URL, seed)
        return f"{strategy_id}-{symbol}-{deterministic_uuid.hex[:16]}"

    @staticmethod
    def _build_intent_id(*, client_order_id: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"order-intent:{client_order_id}")
