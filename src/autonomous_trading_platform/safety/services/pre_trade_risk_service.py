from datetime import datetime

from autonomous_trading_platform.config.settings import Settings
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

    def assert_order_allowed(self, order_intent, now: datetime) -> None:
        order_notional = self._estimate_order_notional(order_intent)

        current_gross_exposure = self.risk_state_reader.get_gross_exposure()
        current_symbol_exposure = self.risk_state_reader.get_symbol_exposure(order_intent.symbol)
        current_daily_notional = self.risk_state_reader.get_daily_notional_traded(now.date())

        projected_gross_exposure = current_gross_exposure + order_notional
        projected_symbol_exposure = current_symbol_exposure + order_notional
        projected_daily_notional = current_daily_notional + order_notional

        if projected_gross_exposure > self.settings.max_gross_exposure:
            raise GrossExposureLimitExceededError(
                f"Projected gross exposure {projected_gross_exposure} exceeds "
                f"limit {self.settings.max_gross_exposure}."
            )

        if projected_symbol_exposure > self.settings.max_symbol_exposure:
            raise SymbolExposureLimitExceededError(
                f"Projected symbol exposure for {order_intent.symbol} "
                f"{projected_symbol_exposure} exceeds limit "
                f"{self.settings.max_symbol_exposure}."
            )

        if projected_daily_notional > self.settings.max_daily_notional_traded:
            raise DailyNotionalLimitExceededError(
                f"Projected daily notional {projected_daily_notional} exceeds "
                f"limit {self.settings.max_daily_notional_traded}."
            )

    def _estimate_order_notional(self, order_intent: OrderIntent) -> float:
        price = self._resolve_reference_price(order_intent)
        return abs(float(order_intent.quantity)) * price

    def _resolve_reference_price(self, order_intent) -> float:
        if getattr(order_intent, "limit_price", None) is not None:
            return float(order_intent.limit_price)
        if getattr(order_intent, "reference_price", None) is not None:
            return float(order_intent.reference_price)
        raise ValueError("Order intent must provide limit_price or reference_price.")
