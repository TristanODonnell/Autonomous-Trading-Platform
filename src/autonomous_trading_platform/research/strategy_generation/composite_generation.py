from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
    GenerationSummary,
)
from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def generate_composite_rule_configs(
    *,
    options: GenerationOptions | None = None,
    method: str = "grid",
) -> tuple[list[StrategyConfig], GenerationSummary]:
    summary = GenerationSummary()
    configs: list[StrategyConfig] = []
    seen: set[str] = set()

    for template in _selected_templates(method=method, options=options or GenerationOptions()):
        summary.generated_count += 1
        try:
            _validate_template_components(template)
            config = make_config("composite_rule", template)
        except ValueError as exc:
            summary.reject(
                str(exc),
                strategy_type="composite_rule",
                parameters=template,
                generator=method,
            )
            continue
        config_hash = config.config_hash()
        if config_hash in seen:
            summary.duplicate(
                strategy_type="composite_rule",
                config_hash=config_hash,
                generator=method,
                parameters=template,
            )
            continue
        seen.add(config_hash)
        summary.accepted_count += 1
        summary.strategy_type_distribution["composite_rule"] += 1
        summary.family_distribution["composite"] += 1
        configs.append(config)

    return configs, summary


def _selected_templates(
    *,
    method: str,
    options: GenerationOptions,
) -> Iterator[dict[str, Any]]:
    templates = _templates()
    if method == "random":
        import random

        rng = random.Random(options.seed)
        shuffled = list(templates)
        rng.shuffle(shuffled)
        yield from shuffled[: max(1, min(options.n_samples, len(shuffled)))]
        return
    if method == "evolutionary":
        yield from templates
        for index, template in enumerate(templates[: max(0, options.generations)]):
            mutated = deepcopy(template)
            mutated["metadata"]["generation_template"] += f"_mutation_{index}"
            if mutated["aggregator"]["component"] == "voting":
                mutated["aggregator"] = {
                    "component": "weighted_score",
                    "parameters": {"buy_threshold": 0.55, "sell_threshold": -0.55},
                }
            else:
                mutated["aggregator"] = {"component": "voting", "parameters": {"min_votes": 1}}
            yield mutated
        return
    yield from templates


def _templates() -> list[dict[str, Any]]:
    return [
        {
            "strategy_type": "composite_rule",
            "indicators": [
                {"id": "z_score_20", "component": "z_score", "parameters": {"window": 20}},
                {
                    "id": "volume_ratio_20",
                    "component": "volume_ratio",
                    "parameters": {"window": 20},
                },
                {
                    "id": "sma_50",
                    "component": "simple_moving_average",
                    "parameters": {"window": 50},
                },
            ],
            "entry_rules": [
                {
                    "id": "mean_reversion_entry",
                    "component": "threshold",
                    "inputs": {"value": {"indicator_id": "z_score_20", "offset": 0}},
                    "parameters": {"buy_below": -1.5, "sell_above": 1.5, "confidence": 0.6},
                    "weight": 1.0,
                }
            ],
            "filters": [
                {
                    "id": "trend_filter",
                    "component": "regime_filter",
                    "input": {"indicator_id": "sma_50", "offset": 0},
                    "operator": "gt",
                    "threshold": 0.0,
                    "block_reason": "trend_filter_blocked_signal",
                }
            ],
            "confirmations": [
                {
                    "id": "volume_confirmation",
                    "component": "threshold",
                    "inputs": {"value": {"indicator_id": "volume_ratio_20", "offset": 0}},
                    "parameters": {"buy_above": 1.0, "sell_below": 1.0, "confidence": 0.55},
                    "weight": 0.5,
                }
            ],
            "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
            "confidence_scoring": {"mode": "weighted", "floor": 0.0, "cap": 1.0},
            "sizing": {},
            "metadata": {"generation_template": "trend_mean_reversion_volume"},
        },
        {
            "strategy_type": "composite_rule",
            "indicators": [
                {"id": "momentum_10", "component": "momentum", "parameters": {"lookback": 10}},
                {
                    "id": "volatility_20",
                    "component": "rolling_standard_deviation",
                    "parameters": {"window": 20},
                },
            ],
            "entry_rules": [
                {
                    "id": "momentum_entry",
                    "component": "threshold",
                    "inputs": {"value": {"indicator_id": "momentum_10", "offset": 0}},
                    "parameters": {"buy_above": 0.0, "sell_below": 0.0, "confidence": 0.58},
                    "weight": 1.0,
                }
            ],
            "filters": [
                {
                    "id": "volatility_filter",
                    "component": "volatility_filter",
                    "input": {"indicator_id": "volatility_20", "offset": 0},
                    "operator": "lt",
                    "threshold": 10.0,
                    "block_reason": "volatility_filter_blocked_signal",
                }
            ],
            "confirmations": [],
            "aggregator": {
                "component": "weighted_score",
                "parameters": {"buy_threshold": 0.55, "sell_threshold": -0.55},
            },
            "confidence_scoring": {"mode": "aggregation", "floor": 0.0, "cap": 1.0},
            "sizing": {},
            "metadata": {"generation_template": "momentum_volatility_weighted"},
        },
        {
            "strategy_type": "composite_rule",
            "indicators": [
                {
                    "id": "sma_fast_10",
                    "component": "simple_moving_average",
                    "parameters": {"window": 10},
                },
                {
                    "id": "sma_slow_30",
                    "component": "simple_moving_average",
                    "parameters": {"window": 30},
                },
                {
                    "id": "volume_ratio_20",
                    "component": "volume_ratio",
                    "parameters": {"window": 20},
                },
            ],
            "entry_rules": [
                {
                    "id": "sma_crossover_entry",
                    "component": "crossover",
                    "inputs": {
                        "previous_fast": {"indicator_id": "sma_fast_10", "offset": -1},
                        "previous_slow": {"indicator_id": "sma_slow_30", "offset": -1},
                        "current_fast": {"indicator_id": "sma_fast_10", "offset": 0},
                        "current_slow": {"indicator_id": "sma_slow_30", "offset": 0},
                    },
                    "parameters": {"confidence": 0.62},
                    "weight": 1.0,
                }
            ],
            "filters": [],
            "confirmations": [
                {
                    "id": "volume_confirmation",
                    "component": "threshold",
                    "inputs": {"value": {"indicator_id": "volume_ratio_20", "offset": 0}},
                    "parameters": {"buy_above": 1.0, "sell_below": 1.0, "confidence": 0.55},
                    "weight": 0.4,
                }
            ],
            "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
            "confidence_scoring": {"mode": "weighted", "floor": 0.0, "cap": 1.0},
            "sizing": {},
            "metadata": {"generation_template": "sma_crossover_volume"},
        },
    ]


def _validate_template_components(template: dict[str, Any]) -> None:
    registry = get_component_registry()
    for indicator in template["indicators"]:
        defn = registry.get_component_definition(indicator["component"])
        if defn.component_type != ComponentType.INDICATOR or not defn.is_executable:
            raise ValueError(f"{indicator['component']} is not an executable indicator")
    for rule in [*template["entry_rules"], *template["confirmations"]]:
        defn = registry.get_component_definition(rule["component"])
        if defn.component_type != ComponentType.SIGNAL_RULE or not defn.is_executable:
            raise ValueError(f"{rule['component']} is not an executable signal rule")
    aggregator = registry.get_component_definition(template["aggregator"]["component"])
    if aggregator.component_type != ComponentType.AGGREGATOR or not aggregator.is_executable:
        raise ValueError(f"{aggregator.component_name} is not an executable aggregator")
    for filter_config in template["filters"]:
        defn = registry.get_component_definition(filter_config["component"])
        if defn.component_type != ComponentType.FILTER:
            raise ValueError(f"{filter_config['component']} is not a filter component")
