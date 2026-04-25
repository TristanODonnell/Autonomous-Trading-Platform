from __future__ import annotations

from dataclasses import dataclass

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.base_signal_rule import (
    BaseSignalRule,
    SignalRuleResult,
)


@dataclass(frozen=True)
class ThresholdRule(BaseSignalRule):
    value: float
    buy_above: float | None = None
    buy_below: float | None = None
    sell_above: float | None = None
    sell_below: float | None = None
    confidence: float = 0.55

    def evaluate(self) -> SignalRuleResult:
        if self.buy_above is not None and self.value > self.buy_above:
            return self._result(
                direction=SignalDirection.BUY,
                reason="value_above_buy_threshold",
            )

        if self.buy_below is not None and self.value < self.buy_below:
            return self._result(
                direction=SignalDirection.BUY,
                reason="value_below_buy_threshold",
            )

        if self.sell_above is not None and self.value > self.sell_above:
            return self._result(
                direction=SignalDirection.SELL,
                reason="value_above_sell_threshold",
            )

        if self.sell_below is not None and self.value < self.sell_below:
            return self._result(
                direction=SignalDirection.SELL,
                reason="value_below_sell_threshold",
            )

        return SignalRuleResult(
            direction=None,
            confidence=0.0,
            reason="no_threshold_crossed",
            params=self._params(),
        )

    def _result(
        self,
        *,
        direction: SignalDirection,
        reason: str,
    ) -> SignalRuleResult:
        return SignalRuleResult(
            direction=direction,
            confidence=self.confidence,
            reason=reason,
            params=self._params(),
        )

    def _params(self) -> dict[str, float | str | int | bool | None]:
        return {
            "value": self.value,
            "buy_above": self.buy_above,
            "buy_below": self.buy_below,
            "sell_above": self.sell_above,
            "sell_below": self.sell_below,
        }
