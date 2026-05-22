from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.strategy.composite import CompositeStrategyConfig
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.registry import StrategyFamily, get_registry


def _base_config() -> dict:
    return {
        "indicators": [
            {"id": "mom_5", "component": "momentum", "parameters": {"lookback": 5}},
        ],
        "entry_rules": [
            {
                "id": "mom_entry",
                "component": "threshold",
                "inputs": {"value": {"indicator_id": "mom_5"}},
                "parameters": {"buy_above": 0.0, "sell_below": 0.0},
            }
        ],
        "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
    }


def test_rejects_duplicate_indicator_ids() -> None:
    config = _base_config()
    config["indicators"].append(
        {"id": "mom_5", "component": "rate_of_change", "parameters": {"lookback": 5}}
    )

    with pytest.raises(ValidationError, match="indicator ids must be unique"):
        CompositeStrategyConfig.model_validate(config)


def test_rejects_unknown_rule_input_indicator_reference() -> None:
    config = _base_config()
    config["entry_rules"][0]["inputs"] = {"value": {"indicator_id": "missing"}}

    with pytest.raises(ValidationError, match="unknown indicator id"):
        CompositeStrategyConfig.model_validate(config)


def test_rejects_wrong_component_type_for_entry_rule() -> None:
    config = _base_config()
    config["entry_rules"][0]["component"] = "momentum"

    with pytest.raises(ValidationError, match="expected 'signal_rule'"):
        CompositeStrategyConfig.model_validate(config)


def test_rejects_invalid_aggregation_config() -> None:
    config = _base_config()
    config["aggregator"] = {"component": "threshold", "parameters": {}}

    with pytest.raises(ValidationError, match="expected 'aggregator'"):
        CompositeStrategyConfig.model_validate(config)


def test_composite_rule_is_strategy_registry_integrated() -> None:
    defn = get_registry().get_definition("composite_rule")

    assert defn.family == StrategyFamily.COMPOSITE
    assert defn.implementation_class.__name__ == "CompositeRuleStrategy"
    assert defn.compute_warmup_bars() == 6


def test_strategy_config_normalizes_composite_defaults() -> None:
    config = StrategyConfig(type="composite_rule", strategy_id="default-composite")

    assert config.parameters == get_registry().get_default_parameters("composite_rule")
