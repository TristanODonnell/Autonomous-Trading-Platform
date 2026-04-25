from __future__ import annotations

from dataclasses import dataclass

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.base_signal_rule import (
    BaseSignalRule,
    SignalRuleResult,
)


@dataclass(frozen=True)
class CrossoverRule(BaseSignalRule):
    previous_fast: float
    previous_slow: float
    current_fast: float
    current_slow: float
    confidence: float = 0.6

    def evaluate(self) -> SignalRuleResult:
        crossed_above = (
            self.previous_fast <= self.previous_slow and self.current_fast > self.current_slow
        )
        crossed_below = (
            self.previous_fast >= self.previous_slow and self.current_fast < self.current_slow
        )

        if crossed_above:
            return SignalRuleResult(
                direction=SignalDirection.BUY,
                confidence=self.confidence,
                reason="fast_crossed_above_slow",
                params={
                    "previous_fast": self.previous_fast,
                    "previous_slow": self.previous_slow,
                    "current_fast": self.current_fast,
                    "current_slow": self.current_slow,
                },
            )

        if crossed_below:
            return SignalRuleResult(
                direction=SignalDirection.SELL,
                confidence=self.confidence,
                reason="fast_crossed_below_slow",
                params={
                    "previous_fast": self.previous_fast,
                    "previous_slow": self.previous_slow,
                    "current_fast": self.current_fast,
                    "current_slow": self.current_slow,
                },
            )

        return SignalRuleResult(
            direction=None,
            confidence=0.0,
            reason="no_crossover",
            params={
                "previous_fast": self.previous_fast,
                "previous_slow": self.previous_slow,
                "current_fast": self.current_fast,
                "current_slow": self.current_slow,
            },
        )
