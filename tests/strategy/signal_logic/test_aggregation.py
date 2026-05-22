from __future__ import annotations

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.aggregation import (
    LogicalAggregator,
    VotingAggregator,
    WeightedScoreAggregator,
    WeightedSignalInput,
)
from autonomous_trading_platform.strategy.signal_logic.base_signal_rule import SignalRuleResult


def _buy(confidence: float = 0.6) -> SignalRuleResult:
    return SignalRuleResult(
        direction=SignalDirection.BUY, confidence=confidence, reason="test_buy", params={}
    )


def _sell(confidence: float = 0.6) -> SignalRuleResult:
    return SignalRuleResult(
        direction=SignalDirection.SELL, confidence=confidence, reason="test_sell", params={}
    )


def _none() -> SignalRuleResult:
    return SignalRuleResult(direction=None, confidence=0.0, reason="test_none", params={})


class TestLogicalAggregatorAND:
    def test_all_buy_returns_buy(self) -> None:
        agg = LogicalAggregator(operator="AND", direction=SignalDirection.BUY)
        result = agg.aggregate([_buy(), _buy(), _buy()])
        assert result.direction == SignalDirection.BUY

    def test_one_missing_buy_returns_none(self) -> None:
        agg = LogicalAggregator(operator="AND", direction=SignalDirection.BUY)
        result = agg.aggregate([_buy(), _none(), _buy()])
        assert result.direction is None

    def test_empty_list_returns_none(self) -> None:
        agg = LogicalAggregator(operator="AND", direction=SignalDirection.BUY)
        result = agg.aggregate([])
        assert result.direction is None

    def test_all_sell_and_operator_returns_sell(self) -> None:
        agg = LogicalAggregator(operator="AND", direction=SignalDirection.SELL)
        result = agg.aggregate([_sell(), _sell()])
        assert result.direction == SignalDirection.SELL

    def test_confidence_is_average_of_matching(self) -> None:
        agg = LogicalAggregator(operator="AND", direction=SignalDirection.BUY)
        result = agg.aggregate([_buy(0.4), _buy(0.8)])
        assert result.direction == SignalDirection.BUY
        assert abs(result.confidence - 0.6) < 1e-9


class TestLogicalAggregatorOR:
    def test_any_buy_returns_buy(self) -> None:
        agg = LogicalAggregator(operator="OR", direction=SignalDirection.BUY)
        result = agg.aggregate([_none(), _buy(), _none()])
        assert result.direction == SignalDirection.BUY

    def test_no_matching_returns_none(self) -> None:
        agg = LogicalAggregator(operator="OR", direction=SignalDirection.BUY)
        result = agg.aggregate([_sell(), _none()])
        assert result.direction is None

    def test_empty_list_returns_none(self) -> None:
        agg = LogicalAggregator(operator="OR", direction=SignalDirection.BUY)
        result = agg.aggregate([])
        assert result.direction is None

    def test_invalid_operator_raises(self) -> None:
        agg = LogicalAggregator(operator="XOR", direction=SignalDirection.BUY)
        with pytest.raises(ValueError):
            agg.aggregate([_buy()])


class TestVotingAggregator:
    def test_two_buy_votes_meet_min_votes(self) -> None:
        agg = VotingAggregator(min_votes=2)
        result = agg.aggregate([_buy(), _buy(), _none()])
        assert result.direction == SignalDirection.BUY

    def test_one_buy_vote_below_min_votes_returns_none(self) -> None:
        agg = VotingAggregator(min_votes=2)
        result = agg.aggregate([_buy(), _none(), _none()])
        assert result.direction is None

    def test_sell_wins_over_buy_when_more_votes(self) -> None:
        agg = VotingAggregator(min_votes=2)
        result = agg.aggregate([_sell(), _sell(), _sell(), _buy(), _buy()])
        assert result.direction == SignalDirection.SELL

    def test_tie_returns_none(self) -> None:
        agg = VotingAggregator(min_votes=2)
        result = agg.aggregate([_buy(), _buy(), _sell(), _sell()])
        assert result.direction is None

    def test_empty_list_returns_none(self) -> None:
        agg = VotingAggregator(min_votes=1)
        result = agg.aggregate([])
        assert result.direction is None
        assert result.confidence == 0.0

    def test_confidence_is_average_of_winning_votes(self) -> None:
        agg = VotingAggregator(min_votes=2)
        result = agg.aggregate([_buy(0.4), _buy(0.6)])
        assert result.direction == SignalDirection.BUY
        assert abs(result.confidence - 0.5) < 1e-9

    def test_min_votes_one(self) -> None:
        agg = VotingAggregator(min_votes=1)
        result = agg.aggregate([_buy()])
        assert result.direction == SignalDirection.BUY


class TestWeightedScoreAggregator:
    def test_strong_buy_signal_returns_buy(self) -> None:
        agg = WeightedScoreAggregator(buy_threshold=0.5, sell_threshold=-0.5)
        result = agg.aggregate([_buy(1.0), _buy(1.0)])
        assert result.direction == SignalDirection.BUY

    def test_strong_sell_signal_returns_sell(self) -> None:
        agg = WeightedScoreAggregator(buy_threshold=0.5, sell_threshold=-0.5)
        result = agg.aggregate([_sell(1.0), _sell(1.0)])
        assert result.direction == SignalDirection.SELL

    def test_mixed_signals_below_threshold_returns_none(self) -> None:
        agg = WeightedScoreAggregator(buy_threshold=0.8, sell_threshold=-0.8)
        result = agg.aggregate([_buy(0.5), _sell(0.5)])
        assert result.direction is None

    def test_empty_list_returns_none(self) -> None:
        agg = WeightedScoreAggregator()
        result = agg.aggregate([])
        assert result.direction is None

    def test_uniform_weights_with_aggregate_weighted(self) -> None:
        agg = WeightedScoreAggregator(buy_threshold=0.5, sell_threshold=-0.5)
        inputs = [
            WeightedSignalInput(result=_buy(1.0), weight=1.0),
            WeightedSignalInput(result=_buy(1.0), weight=1.0),
        ]
        result = agg.aggregate_weighted(inputs)
        assert result.direction == SignalDirection.BUY

    def test_zero_weight_raises(self) -> None:
        agg = WeightedScoreAggregator()
        inputs = [WeightedSignalInput(result=_buy(1.0), weight=0.0)]
        with pytest.raises(ValueError):
            agg.aggregate_weighted(inputs)

    def test_weighted_aggregation_golden_value(self) -> None:
        # buy(1.0) w=2, sell(1.0) w=1 → score=(2*1 - 1*1)/(2+1)=1/3≈0.333 < 0.6
        agg = WeightedScoreAggregator(buy_threshold=0.6, sell_threshold=-0.6)
        inputs = [
            WeightedSignalInput(result=_buy(1.0), weight=2.0),
            WeightedSignalInput(result=_sell(1.0), weight=1.0),
        ]
        result = agg.aggregate_weighted(inputs)
        assert result.direction is None
        assert abs(result.confidence - 1.0 / 3.0) < 1e-9

    def test_confidence_is_abs_normalized_score(self) -> None:
        agg = WeightedScoreAggregator(buy_threshold=0.5, sell_threshold=-0.5)
        result = agg.aggregate([_buy(0.8)])
        assert result.direction == SignalDirection.BUY
        assert abs(result.confidence - 0.8) < 1e-9

    def test_aggregate_calls_aggregate_weighted_with_equal_weights(self) -> None:
        # aggregate() should behave identically to aggregate_weighted with weight=1.0
        agg = WeightedScoreAggregator(buy_threshold=0.5, sell_threshold=-0.5)
        results = [_buy(0.8), _sell(0.5)]

        direct = agg.aggregate(results)
        weighted_inputs = [WeightedSignalInput(result=r, weight=1.0) for r in results]
        via_weighted = agg.aggregate_weighted(weighted_inputs)

        assert direct.direction == via_weighted.direction
        assert abs(direct.confidence - via_weighted.confidence) < 1e-9
