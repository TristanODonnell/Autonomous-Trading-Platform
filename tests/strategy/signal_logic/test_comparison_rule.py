from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.comparison_rule import ComparisonRule


class TestComparisonRuleBuy:
    def test_left_above_right_returns_buy_when_flag_set(self) -> None:
        rule = ComparisonRule(
            left_name="fast_ma",
            left_value=11.0,
            right_name="slow_ma",
            right_value=10.0,
            buy_when_left_above=True,
        )
        result = rule.evaluate()
        assert result.direction == SignalDirection.BUY
        assert "above" in result.reason

    def test_left_above_right_no_buy_when_flag_false(self) -> None:
        rule = ComparisonRule(
            left_name="fast_ma",
            left_value=11.0,
            right_name="slow_ma",
            right_value=10.0,
            buy_when_left_above=False,
            sell_when_left_below=False,
        )
        result = rule.evaluate()
        assert result.direction is None

    def test_left_equal_right_no_buy_signal(self) -> None:
        rule = ComparisonRule(
            left_name="fast_ma",
            left_value=10.0,
            right_name="slow_ma",
            right_value=10.0,
            buy_when_left_above=True,
        )
        result = rule.evaluate()
        assert result.direction is None


class TestComparisonRuleSell:
    def test_left_below_right_returns_sell_when_flag_set(self) -> None:
        rule = ComparisonRule(
            left_name="fast_ma",
            left_value=9.0,
            right_name="slow_ma",
            right_value=10.0,
            sell_when_left_below=True,
        )
        result = rule.evaluate()
        assert result.direction == SignalDirection.SELL
        assert "below" in result.reason

    def test_left_below_right_no_sell_when_flag_false(self) -> None:
        rule = ComparisonRule(
            left_name="fast_ma",
            left_value=9.0,
            right_name="slow_ma",
            right_value=10.0,
            buy_when_left_above=False,
            sell_when_left_below=False,
        )
        result = rule.evaluate()
        assert result.direction is None

    def test_left_equal_right_no_sell_signal(self) -> None:
        rule = ComparisonRule(
            left_name="fast_ma",
            left_value=10.0,
            right_name="slow_ma",
            right_value=10.0,
            sell_when_left_below=True,
        )
        result = rule.evaluate()
        assert result.direction is None


class TestComparisonRuleEqualityEdge:
    def test_equal_values_return_none_direction(self) -> None:
        rule = ComparisonRule(
            left_name="a",
            left_value=5.0,
            right_name="b",
            right_value=5.0,
            buy_when_left_above=True,
            sell_when_left_below=True,
        )
        result = rule.evaluate()
        assert result.direction is None
        assert result.confidence == 0.0

    def test_equal_reason_string_contains_equal_or_no_rule(self) -> None:
        rule = ComparisonRule(left_name="x", left_value=1.0, right_name="y", right_value=1.0)
        result = rule.evaluate()
        assert "equal_or_no_rule_triggered" in result.reason


class TestComparisonRuleParams:
    def test_params_contain_named_values(self) -> None:
        rule = ComparisonRule(
            left_name="price",
            left_value=110.0,
            right_name="target",
            right_value=100.0,
            buy_when_left_above=True,
        )
        result = rule.evaluate()
        assert result.params["price"] == 110.0
        assert result.params["target"] == 100.0

    def test_confidence_matches_config(self) -> None:
        rule = ComparisonRule(
            left_name="a",
            left_value=2.0,
            right_name="b",
            right_value=1.0,
            confidence=0.7,
            buy_when_left_above=True,
        )
        result = rule.evaluate()
        assert result.confidence == 0.7
