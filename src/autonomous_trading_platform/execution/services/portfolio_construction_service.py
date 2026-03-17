from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

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
            # placeholder sizing rule
            target_qty = 10

            if signal.direction.value == "long":
                target_positions[signal.symbol] = target_qty
            elif signal.direction.value == "short":
                target_positions[signal.symbol] = -target_qty
            else:
                target_positions[signal.symbol] = 0

        return target_positions

    def calculate_deltas(
        self,
        current_positions: dict[str, int],
        target_positions: dict[str, int],
    ):
        deltas = []

        all_symbols = set(current_positions) | set(target_positions)

        for symbol in all_symbols:
            current_qty = current_positions.get(symbol, 0)
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
        self, delta, prices: dict[str, float], run_id, strategy_id, bar_timestamp, now
    ) -> OrderIntent:
        side = Side.BUY if delta["delta_qty"] > 0 else Side.SELL
        price = Decimal(str(prices[delta["symbol"]]))
        return OrderIntent(
            intent_id=uuid4(),
            idempotency_key="pending",
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp=now,
            bar_timestamp=bar_timestamp,
            symbol=delta["symbol"],
            side=side,
            qty=abs(delta["delta_qty"]),
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=price,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=f"{strategy_id}-{delta['symbol']}-{int(now.timestamp())}",
            metadata=None,
        )
