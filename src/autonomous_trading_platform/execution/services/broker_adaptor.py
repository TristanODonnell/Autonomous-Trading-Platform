from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.common.enums import OrderType, TimeInForce
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent


class AlpacaBrokerAdapter:
    """
    Translates internal OrderIntent objects into Alpaca order payloads.

    v1 constraints:
    - support only market and limit orders
    - extended-hours orders must be limit orders
    - fractional-share orders must use DAY time in force
    """

    def to_payload(self, intent: OrderIntent) -> dict[str, Any]:
        self._validate_intent(intent)

        payload: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side.value,
            "type": intent.order_type.value,
            "time_in_force": intent.time_in_force.value,
            "extended_hours": intent.extended_hours,
            "client_order_id": intent.client_order_id,
        }

        if intent.qty is not None:
            payload["qty"] = str(intent.qty)

        if intent.notional is not None:
            payload["notional"] = str(intent.notional)

        if intent.order_type == OrderType.LIMIT:
            if intent.limit_price is None:
                raise ValueError("limit_price is required for limit orders")
            payload["limit_price"] = str(intent.limit_price)

        return payload

    def _validate_intent(self, intent: OrderIntent) -> None:
        if intent.order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            raise ValueError(f"Unsupported order_type for v1 broker adapter: {intent.order_type}")

        if intent.extended_hours and intent.order_type != OrderType.LIMIT:
            raise ValueError("Extended-hours orders must be limit orders.")

        if self._is_fractional_qty(intent.qty) and intent.time_in_force != TimeInForce.DAY:
            raise ValueError("Fractional-share orders must use DAY time_in_force.")

    @staticmethod
    def _is_fractional_qty(qty: Decimal | int | float | None) -> bool:
        if qty is None:
            return False

        decimal_qty = Decimal(str(qty))
        return decimal_qty != decimal_qty.to_integral_value()
