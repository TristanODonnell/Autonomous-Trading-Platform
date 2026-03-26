from datetime import datetime

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.safety.errors import (
    DailyNotionalLimitExceededError,
    GrossExposureLimitExceededError,
    SymbolExposureLimitExceededError,
)


class PreTradeRiskService:
    def __init__(self, settings: Settings, risk_state_reader) -> None:
        self.settings = settings
        self.risk_state_reader = risk_state_reader

    def assert_order_allowed(self, order_intent: OrderIntent, now: datetime) -> None:
        order_notional = self._estimate_order_notional(order_intent)

        current_gross_exposure = float(self.risk_state_reader.get_gross_exposure())
        current_symbol_exposure = float(
            self.risk_state_reader.get_symbol_exposure(order_intent.symbol)
        )
        current_daily_notional = float(self.risk_state_reader.get_daily_notional_traded(now.date()))
        current_reserved_cash = float(self.risk_state_reader.get_reserved_cash())
        current_position_qty = float(self.risk_state_reader.get_position_qty(order_intent.symbol))

        exposure_delta = self._calculate_exposure_delta(
            order_intent=order_intent,
            order_notional=order_notional,
            current_position_qty=current_position_qty,
            current_symbol_exposure=current_symbol_exposure,
        )

        projected_gross_exposure = current_gross_exposure + exposure_delta
        projected_symbol_exposure = current_symbol_exposure + exposure_delta
        projected_daily_notional = current_daily_notional + order_notional

        if projected_gross_exposure > float(self.settings.max_gross_exposure):
            raise GrossExposureLimitExceededError(
                f"Projected gross exposure {projected_gross_exposure} exceeds "
                f"limit {self.settings.max_gross_exposure}."
            )

        if projected_symbol_exposure > float(self.settings.max_symbol_exposure):
            raise SymbolExposureLimitExceededError(
                f"Projected symbol exposure for {order_intent.symbol} "
                f"{projected_symbol_exposure} exceeds limit "
                f"{self.settings.max_symbol_exposure}."
            )

        if projected_daily_notional > float(self.settings.max_daily_notional_traded):
            raise DailyNotionalLimitExceededError(
                f"Projected daily notional {projected_daily_notional} exceeds "
                f"limit {self.settings.max_daily_notional_traded}."
            )

        self._assert_reserved_cash_capacity(
            order_intent=order_intent,
            order_notional=order_notional,
            current_reserved_cash=current_reserved_cash,
        )

    def _estimate_order_notional(self, order_intent: OrderIntent) -> float:
        if order_intent.qty is None:
            raise ValueError("Order intent qty must be set for pre-trade risk checks.")

        price = self._resolve_reference_price(order_intent)
        return abs(float(order_intent.qty)) * price

    def _resolve_reference_price(self, order_intent: OrderIntent) -> float:
        if order_intent.limit_price is not None:
            return float(order_intent.limit_price)
        raise ValueError("Order intent must provide limit_price.")

    def _calculate_exposure_delta(
        self,
        *,
        order_intent: OrderIntent,
        order_notional: float,
        current_position_qty: float,
        current_symbol_exposure: float,
    ) -> float:
        signed_order_qty = self._signed_order_qty(order_intent)

        if current_position_qty == 0:
            return order_notional

        # same direction -> increases exposure
        if current_position_qty * signed_order_qty > 0:
            return order_notional

        # opposite direction -> reduces current exposure first, then flips if oversized
        reduction_notional = min(order_notional, current_symbol_exposure)
        remainder_notional = max(order_notional - reduction_notional, 0.0)

        return remainder_notional - reduction_notional

    def _signed_order_qty(self, order_intent: OrderIntent) -> float:
        if order_intent.qty is None:
            raise ValueError("Order intent qty must be set for pre-trade risk checks.")

        abs_qty = abs(float(order_intent.qty))

        if order_intent.side == Side.BUY:
            return abs_qty
        if order_intent.side == Side.SELL:
            return -abs_qty

        raise ValueError(f"Unsupported order side: {order_intent.side}")

    def _assert_reserved_cash_capacity(
        self,
        *,
        order_intent: OrderIntent,
        order_notional: float,
        current_reserved_cash: float,
    ) -> None:
        if order_intent.side != Side.BUY:
            return

        projected_reserved_cash = current_reserved_cash + order_notional
        max_reserved_cash = float(self.settings.max_reserved_cash)

        if projected_reserved_cash > max_reserved_cash:
            raise GrossExposureLimitExceededError(
                f"Projected reserved cash {projected_reserved_cash} exceeds "
                f"limit {max_reserved_cash}."
            )
