from __future__ import annotations

from autonomous_trading_platform.strategy.composite import CompositeStrategyConfig


def test_warmup_uses_max_declared_component_requirement() -> None:
    config = CompositeStrategyConfig.model_validate(
        {
            "indicators": [
                {
                    "id": "sma_200",
                    "component": "simple_moving_average",
                    "parameters": {"window": 200},
                },
                {"id": "rsi_14", "component": "rsi", "parameters": {"window": 14}},
            ],
            "entry_rules": [
                {
                    "id": "rsi_entry",
                    "component": "threshold",
                    "inputs": {"value": {"indicator_id": "rsi_14"}},
                    "parameters": {"buy_below": 10.0},
                }
            ],
            "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
        }
    )

    assert config.warmup_bars() == 200


def test_warmup_accounts_for_previous_indicator_offsets() -> None:
    config = CompositeStrategyConfig.model_validate(
        {
            "indicators": [
                {
                    "id": "fast_sma",
                    "component": "simple_moving_average",
                    "parameters": {"window": 3},
                },
                {
                    "id": "slow_sma",
                    "component": "simple_moving_average",
                    "parameters": {"window": 5},
                },
            ],
            "entry_rules": [
                {
                    "id": "cross",
                    "component": "crossover",
                    "inputs": {
                        "previous_fast": {"indicator_id": "fast_sma", "offset": -1},
                        "previous_slow": {"indicator_id": "slow_sma", "offset": -1},
                        "current_fast": {"indicator_id": "fast_sma"},
                        "current_slow": {"indicator_id": "slow_sma"},
                    },
                    "parameters": {"confidence": 0.6},
                }
            ],
            "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
        }
    )

    assert config.warmup_bars() == 6
