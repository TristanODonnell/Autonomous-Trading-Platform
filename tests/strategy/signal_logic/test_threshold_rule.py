from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.signal_logic.threshold_rule import ThresholdRule


class TestThresholdRuleBuyAbove:
    def test_value_above_threshold_returns_buy(self) -> None:
        rule = ThresholdRule(value=1.5, buy_above=1.0)
        result = rule.evaluate()
        assert result.direction == SignalDirection.BUY
        assert result.reason == "value_above_buy_threshold"

    def test_value_equal_to_threshold_no_signal(self) -> None:
        # ">" not ">=": equality does NOT trigger
        rule = ThresholdRule(value=1.0, buy_above=1.0)
        result = rule.evaluate()
        assert result.direction is None

    def test_value_below_threshold_no_signal(self) -> None:
        rule = ThresholdRule(value=0.5, buy_above=1.0)
        result = rule.evaluate()
        assert result.direction is None


class TestThresholdRuleBuyBelow:
    def test_value_below_threshold_returns_buy(self) -> None:
        rule = ThresholdRule(value=-2.5, buy_below=-2.0)
        result = rule.evaluate()
        assert result.direction == SignalDirection.BUY
        assert result.reason == "value_below_buy_threshold"

    def test_value_equal_to_buy_below_no_signal(self) -> None:
        rule = ThresholdRule(value=-2.0, buy_below=-2.0)
        result = rule.evaluate()
        assert result.direction is None

    def test_value_above_buy_below_no_signal(self) -> None:
        rule = ThresholdRule(value=-1.5, buy_below=-2.0)
        result = rule.evaluate()
        assert result.direction is None


class TestThresholdRuleSellAbove:
    def test_value_above_sell_threshold_returns_sell(self) -> None:
        rule = ThresholdRule(value=2.5, sell_above=2.0)
        result = rule.evaluate()
        assert result.direction == SignalDirection.SELL
        assert result.reason == "value_above_sell_threshold"

    def test_value_equal_to_sell_above_no_signal(self) -> None:
        rule = ThresholdRule(value=2.0, sell_above=2.0)
        result = rule.evaluate()
        assert result.direction is None


class TestThresholdRuleSellBelow:
    def test_value_below_sell_threshold_returns_sell(self) -> None:
        rule = ThresholdRule(value=-3.0, sell_below=-2.0)
        result = rule.evaluate()
        assert result.direction == SignalDirection.SELL
        assert result.reason == "value_below_sell_threshold"

    def test_value_equal_to_sell_below_no_signal(self) -> None:
        rule = ThresholdRule(value=-2.0, sell_below=-2.0)
        result = rule.evaluate()
        assert result.direction is None


class TestThresholdRulePriority:
    def test_buy_above_checked_before_sell_above(self) -> None:
        # buy_above has higher priority — should return BUY not SELL
        rule = ThresholdRule(value=5.0, buy_above=1.0, sell_above=2.0)
        result = rule.evaluate()
        assert result.direction == SignalDirection.BUY

    def test_buy_below_checked_before_sell_below(self) -> None:
        rule = ThresholdRule(value=-3.0, buy_below=-1.0, sell_below=-2.0)
        result = rule.evaluate()
        assert result.direction == SignalDirection.BUY


class TestThresholdRuleNoSignal:
    def test_no_thresholds_configured_returns_none(self) -> None:
        rule = ThresholdRule(value=5.0)
        result = rule.evaluate()
        assert result.direction is None
        assert result.reason == "no_threshold_crossed"
        assert result.confidence == 0.0

    def test_value_between_buy_above_and_sell_above_no_signal(self) -> None:
        rule = ThresholdRule(value=1.5, buy_above=2.0, sell_above=3.0)
        result = rule.evaluate()
        assert result.direction is None


class TestThresholdRuleParams:
    def test_params_contains_value_and_thresholds(self) -> None:
        rule = ThresholdRule(value=1.5, buy_above=1.0, sell_below=-1.0)
        result = rule.evaluate()
        assert result.params["value"] == 1.5
        assert result.params["buy_above"] == 1.0
        assert result.params["sell_below"] == -1.0

    def test_confidence_matches_configured_value(self) -> None:
        rule = ThresholdRule(value=1.5, buy_above=1.0, confidence=0.75)
        result = rule.evaluate()
        assert result.confidence == 0.75
