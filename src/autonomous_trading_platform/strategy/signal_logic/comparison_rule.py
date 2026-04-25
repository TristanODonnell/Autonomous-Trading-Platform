# strategy/signal_logic/comparison_rule.py

from __future__ import annotations

from dataclasses import dataclass

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.base_signal_rule import (
    BaseSignalRule,
    SignalRuleResult,
)


@dataclass(frozen=True)
class ComparisonRule(BaseSignalRule):
    left_name: str
    left_value: float
    right_name: str
    right_value: float
    buy_when_left_above: bool = True
    sell_when_left_below: bool = True
    confidence: float = 0.55

    def evaluate(self) -> SignalRuleResult:
        if self.left_value > self.right_value and self.buy_when_left_above:
            return SignalRuleResult(
                direction=SignalDirection.BUY,
                confidence=self.confidence,
                reason=f"{self.left_name}_above_{self.right_name}",
                params={
                    self.left_name: self.left_value,
                    self.right_name: self.right_value,
                    "buy_when_left_above": self.buy_when_left_above,
                    "sell_when_left_below": self.sell_when_left_below,
                },
            )

        if self.left_value < self.right_value and self.sell_when_left_below:
            return SignalRuleResult(
                direction=SignalDirection.SELL,
                confidence=self.confidence,
                reason=f"{self.left_name}_below_{self.right_name}",
                params={
                    self.left_name: self.left_value,
                    self.right_name: self.right_value,
                    "buy_when_left_above": self.buy_when_left_above,
                    "sell_when_left_below": self.sell_when_left_below,
                },
            )

        return SignalRuleResult(
            direction=None,
            confidence=0.0,
            reason=f"{self.left_name}_equal_or_no_rule_triggered_{self.right_name}",
            params={
                self.left_name: self.left_value,
                self.right_name: self.right_value,
                "buy_when_left_above": self.buy_when_left_above,
                "sell_when_left_below": self.sell_when_left_below,
            },
        )
