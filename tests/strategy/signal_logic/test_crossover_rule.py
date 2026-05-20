from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.crossover_rule import CrossoverRule


class TestCrossoverRuleBuySignal:
    def test_fast_crosses_above_slow_returns_buy(self) -> None:
        rule = CrossoverRule(
            previous_fast=9.0,
            previous_slow=10.0,
            current_fast=11.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.direction == SignalDirection.BUY
        assert result.reason == "fast_crossed_above_slow"

    def test_fast_equal_to_slow_then_crosses_above(self) -> None:
        # previous_fast == previous_slow (touching): should still trigger BUY on cross above
        rule = CrossoverRule(
            previous_fast=10.0,
            previous_slow=10.0,
            current_fast=11.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.direction == SignalDirection.BUY

    def test_already_above_no_crossover_returns_none(self) -> None:
        rule = CrossoverRule(
            previous_fast=12.0,
            previous_slow=10.0,
            current_fast=13.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.direction is None


class TestCrossoverRuleSellSignal:
    def test_fast_crosses_below_slow_returns_sell(self) -> None:
        rule = CrossoverRule(
            previous_fast=11.0,
            previous_slow=10.0,
            current_fast=9.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.direction == SignalDirection.SELL
        assert result.reason == "fast_crossed_below_slow"

    def test_fast_equal_to_slow_then_crosses_below(self) -> None:
        rule = CrossoverRule(
            previous_fast=10.0,
            previous_slow=10.0,
            current_fast=9.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.direction == SignalDirection.SELL

    def test_already_below_no_crossover_returns_none(self) -> None:
        rule = CrossoverRule(
            previous_fast=8.0,
            previous_slow=10.0,
            current_fast=7.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.direction is None


class TestCrossoverRuleNoCrossover:
    def test_both_stable_above_no_signal(self) -> None:
        rule = CrossoverRule(
            previous_fast=12.0,
            previous_slow=10.0,
            current_fast=12.0,
            current_slow=10.0,
        )
        assert rule.evaluate().direction is None

    def test_both_stable_below_no_signal(self) -> None:
        rule = CrossoverRule(
            previous_fast=8.0,
            previous_slow=10.0,
            current_fast=8.0,
            current_slow=10.0,
        )
        assert rule.evaluate().direction is None

    def test_no_crossover_reason_string(self) -> None:
        rule = CrossoverRule(
            previous_fast=12.0,
            previous_slow=10.0,
            current_fast=12.0,
            current_slow=10.0,
        )
        assert rule.evaluate().reason == "no_crossover"


class TestCrossoverRuleConfidence:
    def test_default_confidence(self) -> None:
        rule = CrossoverRule(
            previous_fast=9.0,
            previous_slow=10.0,
            current_fast=11.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.confidence == 0.6

    def test_custom_confidence(self) -> None:
        rule = CrossoverRule(
            previous_fast=9.0,
            previous_slow=10.0,
            current_fast=11.0,
            current_slow=10.0,
            confidence=0.8,
        )
        result = rule.evaluate()
        assert result.confidence == 0.8


class TestCrossoverRuleParams:
    def test_params_contain_all_ma_values(self) -> None:
        rule = CrossoverRule(
            previous_fast=9.0,
            previous_slow=10.0,
            current_fast=11.0,
            current_slow=10.0,
        )
        result = rule.evaluate()
        assert result.params["previous_fast"] == 9.0
        assert result.params["previous_slow"] == 10.0
        assert result.params["current_fast"] == 11.0
        assert result.params["current_slow"] == 10.0

    def test_determinism_same_inputs_same_output(self) -> None:
        rule_a = CrossoverRule(
            previous_fast=9.0, previous_slow=10.0, current_fast=11.0, current_slow=10.0
        )
        rule_b = CrossoverRule(
            previous_fast=9.0, previous_slow=10.0, current_fast=11.0, current_slow=10.0
        )
        assert rule_a.evaluate().direction == rule_b.evaluate().direction
        assert rule_a.evaluate().reason == rule_b.evaluate().reason
