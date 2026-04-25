# strategy/signal_logic/base_signal_rule.py

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from autonomous_trading_platform.contracts.common.enums import SignalDirection


@dataclass(frozen=True)
class SignalRuleResult:
    direction: SignalDirection | None
    confidence: float
    reason: str
    params: dict[str, float | str | int | bool | None]


class BaseSignalRule(ABC):
    @abstractmethod
    def evaluate(self) -> SignalRuleResult:
        raise NotImplementedError
