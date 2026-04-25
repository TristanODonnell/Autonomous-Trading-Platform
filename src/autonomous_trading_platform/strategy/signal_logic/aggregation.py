from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from statistics import mean

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.base_signal_rule import (
    SignalRuleResult,
)


class SignalAggregator(ABC):
    @abstractmethod
    def aggregate(
        self,
        results: list[SignalRuleResult],
    ) -> SignalRuleResult:
        raise NotImplementedError


@dataclass(frozen=True)
class LogicalAggregator(SignalAggregator):
    operator: str  # "AND" or "OR"
    direction: SignalDirection

    def aggregate(self, results: list[SignalRuleResult]) -> SignalRuleResult:
        matching = [result for result in results if result.direction == self.direction]

        if self.operator == "AND":
            triggered = len(matching) == len(results) and len(results) > 0
        elif self.operator == "OR":
            triggered = len(matching) > 0
        else:
            raise ValueError("operator must be AND or OR")

        return SignalRuleResult(
            direction=self.direction if triggered else None,
            confidence=mean([r.confidence for r in matching]) if triggered else 0.0,
            reason=f"logical_{self.operator.lower()}_{self.direction.value.lower()}",
            params={
                "operator": self.operator,
                "target_direction": self.direction.value,
                "rules_total": len(results),
                "rules_matching": len(matching),
            },
        )


@dataclass(frozen=True)
class VotingAggregator(SignalAggregator):
    min_votes: int = 2

    def aggregate(self, results: list[SignalRuleResult]) -> SignalRuleResult:
        buy_votes = [r for r in results if r.direction == SignalDirection.BUY]
        sell_votes = [r for r in results if r.direction == SignalDirection.SELL]

        if len(buy_votes) >= self.min_votes and len(buy_votes) > len(sell_votes):
            direction = SignalDirection.BUY
            votes = buy_votes
        elif len(sell_votes) >= self.min_votes and len(sell_votes) > len(buy_votes):
            direction = SignalDirection.SELL
            votes = sell_votes
        else:
            direction = None
            votes = []

        confidence = sum(r.confidence for r in votes) / len(votes) if votes else 0.0

        return SignalRuleResult(
            direction=direction,
            confidence=confidence,
            reason="voting_aggregation",
            params={
                "buy_votes": len(buy_votes),
                "sell_votes": len(sell_votes),
                "min_votes": self.min_votes,
            },
        )


@dataclass(frozen=True)
class WeightedSignalInput:
    result: SignalRuleResult
    weight: float


@dataclass(frozen=True)
class WeightedScoreAggregator(SignalAggregator):
    buy_threshold: float = 0.6
    sell_threshold: float = -0.6

    def aggregate_weighted(
        self,
        inputs: list[WeightedSignalInput],
    ) -> SignalRuleResult:
        score = 0.0
        total_weight = 0.0

        for item in inputs:
            if item.weight <= 0:
                raise ValueError("weight must be positive")

            total_weight += item.weight

            if item.result.direction == SignalDirection.BUY:
                score += item.weight * item.result.confidence
            elif item.result.direction == SignalDirection.SELL:
                score -= item.weight * item.result.confidence

        normalized_score = score / total_weight if total_weight else 0.0

        if normalized_score >= self.buy_threshold:
            direction = SignalDirection.BUY
        elif normalized_score <= self.sell_threshold:
            direction = SignalDirection.SELL
        else:
            direction = None

        return SignalRuleResult(
            direction=direction,
            confidence=abs(normalized_score),
            reason="weighted_score_aggregation",
            params={
                "normalized_score": normalized_score,
                "buy_threshold": self.buy_threshold,
                "sell_threshold": self.sell_threshold,
                "inputs": len(inputs),
            },
        )

    def aggregate(self, results: list[SignalRuleResult]) -> SignalRuleResult:
        inputs = [WeightedSignalInput(result=result, weight=1.0) for result in results]
        return self.aggregate_weighted(inputs)
