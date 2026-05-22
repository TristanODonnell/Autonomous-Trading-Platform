from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from autonomous_trading_platform.research.strategy_generation.composite_generation import (
    _validate_template_components,
)
from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
)
from autonomous_trading_platform.research.strategy_generation.generators.evolutionary_generator import (
    EvolutionaryGenerator,
)
from autonomous_trading_platform.research.strategy_generation.generators.random_sampling_generator import (
    RandomSamplingGenerator,
)
from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    ParameterSpaceResolver,
)
from autonomous_trading_platform.research.strategy_generation.strategy_generation_engine import (
    StrategyGenerationEngine,
)
from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry
from autonomous_trading_platform.strategy.composite import CompositeRuleStrategy
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory
from autonomous_trading_platform.strategy.registry import get_registry
from tests.utilities.factories import make_five_minute_bar


def test_grid_uses_registry_space_and_rejects_invalid_combinations() -> None:
    result = StrategyGenerationEngine().generate_result(
        "moving_average_crossover",
        method="grid",
        parameter_space={"short_window": [5, 10], "long_window": [10, 20]},
    )

    assert [config.parameters for config in result.configs] == [
        {"short_window": 5, "long_window": 10},
        {"short_window": 5, "long_window": 20},
        {"short_window": 10, "long_window": 20},
    ]
    assert result.summary.generated_count == 4
    assert result.summary.rejected_count == 1
    assert len({config.config_hash() for config in result.configs}) == len(result.configs)


def test_registry_derived_grid_is_deterministic() -> None:
    engine = StrategyGenerationEngine()

    first = engine.generate_result("momentum", method="grid").configs
    second = engine.generate_result("momentum", method="grid").configs

    assert [config.config_hash() for config in first] == [config.config_hash() for config in second]
    assert all(
        config.parameters["sell_below"] <= config.parameters["buy_above"] for config in first
    )


def test_unknown_parameter_override_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown parameter names"):
        ParameterSpaceResolver().resolve("momentum", {"does_not_exist": [1]})


def test_out_of_range_parameter_override_is_rejected() -> None:
    with pytest.raises(ValueError, match="lookback must be >="):
        ParameterSpaceResolver().resolve("momentum", {"lookback": [0]})


def test_random_generation_is_seed_deterministic_and_dedupes() -> None:
    options = GenerationOptions(seed=11, n_samples=8)
    engine = StrategyGenerationEngine(RandomSamplingGenerator(n_samples=8, seed=11))

    first = engine.generate_result(
        "moving_average_crossover",
        method="random",
        parameter_space={"short_window": [5, 10], "long_window": [20, 30]},
        options=options,
    )
    second = engine.generate_result(
        "moving_average_crossover",
        method="random",
        parameter_space={"short_window": [5, 10], "long_window": [20, 30]},
        options=options,
    )
    different = engine.generate_result(
        "moving_average_crossover",
        method="random",
        parameter_space={"short_window": [5, 10], "long_window": [20, 30]},
        options=GenerationOptions(seed=12, n_samples=8),
    )

    assert [config.config_hash() for config in first] == [config.config_hash() for config in second]
    assert [config.config_hash() for config in first] != [
        config.config_hash() for config in different
    ]
    assert first.summary.duplicate_count > 0


def test_evolutionary_generation_is_implemented_deterministic_and_valid() -> None:
    engine = StrategyGenerationEngine(
        EvolutionaryGenerator(seed=5, population_size=5, generations=2, mutation_rate=0.5)
    )

    first = engine.generate_result(
        "momentum",
        method="evolutionary",
        parameter_space={"lookback": [3, 5], "buy_above": [0.0, 0.02], "sell_below": [-0.02, 0.0]},
        options=GenerationOptions(seed=5, population_size=5, generations=2, mutation_rate=0.5),
    )
    second = engine.generate_result(
        "momentum",
        method="evolutionary",
        parameter_space={"lookback": [3, 5], "buy_above": [0.0, 0.02], "sell_below": [-0.02, 0.0]},
        options=GenerationOptions(seed=5, population_size=5, generations=2, mutation_rate=0.5),
    )

    assert first.configs
    assert [config.config_hash() for config in first] == [config.config_hash() for config in second]
    assert all(
        config.parameters["sell_below"] <= config.parameters["buy_above"] for config in first
    )


def test_debug_strategies_are_excluded_by_default() -> None:
    engine = StrategyGenerationEngine()

    excluded = engine.generate_result("stub")
    included = engine.generate_result(
        "stub",
        options=GenerationOptions(include_debug=True, include_experimental=True),
    )

    assert excluded.configs == []
    assert excluded.summary.rejection_reasons["debug_excluded"] == 1
    assert len(included.configs) == 1


def test_family_filters_limit_generation() -> None:
    result = StrategyGenerationEngine().generate_for_family("trend", method="grid")

    assert result.configs
    assert set(result.summary.family_distribution) == {"trend"}
    assert {config.type for config in result.configs} == {"moving_average_crossover"}


def test_strategy_type_allow_and_exclude_filters_are_enforced() -> None:
    engine = StrategyGenerationEngine()

    not_allowed = engine.generate_result(
        "momentum",
        options=GenerationOptions(allowed_strategy_types=("mean_reversion",)),
    )
    excluded = engine.generate_result(
        "momentum",
        options=GenerationOptions(excluded_strategy_types=("momentum",)),
    )
    allowed = engine.generate_result(
        "momentum",
        parameter_space={"lookback": [5], "buy_above": [0.0], "sell_below": [0.0]},
        options=GenerationOptions(allowed_strategy_types=("momentum",)),
    )

    assert not_allowed.configs == []
    assert not_allowed.summary.rejection_reasons["strategy_type_not_allowed"] == 1
    assert excluded.configs == []
    assert excluded.summary.rejection_reasons["strategy_type_excluded"] == 1
    assert len(allowed.configs) == 1


def test_family_allow_exclude_filters_and_debug_family_behavior_are_enforced() -> None:
    engine = StrategyGenerationEngine()

    family_not_allowed = engine.generate_result(
        "momentum",
        options=GenerationOptions(allowed_families=("trend",)),
    )
    family_excluded = engine.generate_result(
        "momentum",
        options=GenerationOptions(excluded_families=("momentum",)),
    )
    debug_default = engine.generate_for_family("debug", method="grid")
    debug_included = engine.generate_for_family(
        "debug",
        method="grid",
        options=GenerationOptions(include_debug=True, include_experimental=True),
    )

    assert family_not_allowed.configs == []
    assert family_not_allowed.summary.rejection_reasons["family_not_allowed"] == 1
    assert family_excluded.configs == []
    assert family_excluded.summary.rejection_reasons["family_excluded"] == 1
    assert debug_default.configs == []
    assert debug_default.summary.rejection_reasons["debug_excluded"] == 3
    assert {config.type for config in debug_included.configs} == {
        "stub",
        "intentional_loser",
        "random",
    }


def test_execution_mode_and_price_basis_compatibility_are_accepted_for_current_strategies() -> None:
    result = StrategyGenerationEngine().generate_result(
        "momentum",
        parameter_space={"lookback": [5], "buy_above": [0.0], "sell_below": [0.0]},
        options=GenerationOptions(execution_mode="intraday", price_basis="adjusted"),
    )

    assert len(result.configs) == 1
    assert result.summary.rejected_count == 0


def test_composite_generation_produces_valid_buildable_configs() -> None:
    result = StrategyGenerationEngine().generate_composite()
    component_registry = get_component_registry()

    assert result.summary.accepted_count == 3
    for config in result.configs:
        strategy = cast(CompositeRuleStrategy, StrategyFactory().build(config))
        assert strategy.warmup_bars > 0
        indicator_ids = [indicator["id"] for indicator in config.parameters["indicators"]]
        assert indicator_ids == sorted(indicator_ids) or len(indicator_ids) == len(
            set(indicator_ids)
        )
        for indicator in config.parameters["indicators"]:
            component = component_registry.get_component_definition(indicator["component"])
            assert component.component_type == ComponentType.INDICATOR
            assert component.is_executable
        for rule in [*config.parameters["entry_rules"], *config.parameters["confirmations"]]:
            component = component_registry.get_component_definition(rule["component"])
            assert component.component_type == ComponentType.SIGNAL_RULE
            assert component.is_executable


def test_composite_generation_hashes_warmups_and_usage_are_deterministic() -> None:
    first = StrategyGenerationEngine().generate_composite()
    second = StrategyGenerationEngine().generate_composite()
    hashes = [config.config_hash() for config in first.configs]
    component_usage = {
        component: sum(
            1
            for config in first.configs
            for section in ("indicators", "entry_rules", "filters", "confirmations")
            for item in config.parameters.get(section, [])
            if item.get("component") == component
        )
        for component in {"momentum", "threshold", "volume_ratio"}
    }

    assert hashes == [config.config_hash() for config in second.configs]
    assert len(hashes) == len(set(hashes))
    assert [
        get_registry().get_definition("composite_rule").compute_warmup_bars(c.parameters)
        for c in first.configs
    ] == [
        cast(CompositeRuleStrategy, StrategyFactory().build(config)).warmup_bars
        for config in first.configs
    ]
    assert component_usage == {"momentum": 1, "threshold": 4, "volume_ratio": 2}
    assert first.summary.family_distribution == second.summary.family_distribution


def test_invalid_composite_template_component_references_fail_clearly() -> None:
    invalid_indicator = {
        "indicators": [{"id": "bad", "component": "volatility_filter", "parameters": {}}],
        "entry_rules": [],
        "filters": [],
        "confirmations": [],
        "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
    }
    invalid_rule = {
        "indicators": [{"id": "mom", "component": "momentum", "parameters": {"lookback": 5}}],
        "entry_rules": [{"id": "bad", "component": "momentum", "inputs": {}, "parameters": {}}],
        "filters": [],
        "confirmations": [],
        "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
    }

    with pytest.raises(ValueError, match="not an executable indicator"):
        _validate_template_components(invalid_indicator)
    with pytest.raises(ValueError, match="not an executable signal rule"):
        _validate_template_components(invalid_rule)


def test_duplicate_summaries_are_deterministic_and_use_normalized_hashes() -> None:
    options = GenerationOptions(seed=3, n_samples=5)
    engine = StrategyGenerationEngine()

    first = engine.generate_result(
        "momentum",
        method="random",
        parameter_space={"lookback": [5, 5], "buy_above": [0, 0.0], "sell_below": [0.0]},
        options=options,
    )
    second = engine.generate_result(
        "momentum",
        method="random",
        parameter_space={"sell_below": [0.0], "buy_above": [0.0, 0], "lookback": [5, 5]},
        options=options,
    )

    assert [config.config_hash() for config in first.configs] == [
        config.config_hash() for config in second.configs
    ]
    assert first.summary.duplicate_count == second.summary.duplicate_count == 4
    assert first.summary.duplicate_details == second.summary.duplicate_details
    assert first.summary.duplicate_details[0]["duplicate_hash"] == first.configs[0].config_hash()


def test_evolutionary_generation_count_and_duplicate_summary_are_deterministic() -> None:
    options = GenerationOptions(seed=9, population_size=4, generations=2, mutation_rate=1.0)
    engine = StrategyGenerationEngine()

    first = engine.generate_result(
        "momentum",
        method="evolutionary",
        parameter_space={"lookback": [3, 5], "buy_above": [0.0], "sell_below": [0.0]},
        options=options,
    )
    second = engine.generate_result(
        "momentum",
        method="evolutionary",
        parameter_space={"lookback": [3, 5], "buy_above": [0.0], "sell_below": [0.0]},
        options=options,
    )

    assert first.summary.generated_count == options.population_size * (options.generations + 1)
    assert [config.config_hash() for config in first.configs] == [
        config.config_hash() for config in second.configs
    ]
    assert first.summary.duplicate_details == second.summary.duplicate_details
    assert all(config.parameters["lookback"] in {3, 5} for config in first.configs)


def test_generated_composite_can_evaluate_deterministic_context() -> None:
    result = StrategyGenerationEngine().generate_composite()
    config = next(
        item
        for item in result.configs
        if item.parameters["metadata"]["generation_template"] == "momentum_volatility_weighted"
    )
    strategy = StrategyFactory().build(config)
    bars = [
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * index),
            open_price=str(10 + index),
            high_price=str(10 + index),
            low_price=str(10 + index),
            close_price=str(10 + index),
            volume=1000,
        )
        for index in range(30)
    ]
    context = StrategyContext(
        run_id=UUID("00000000-0000-0000-0000-000000000123"),
        strategy_id=config.strategy_id,
        symbol="AAPL",
        evaluation_timestamp=datetime(2025, 1, 1, 3, tzinfo=UTC),
        bar_timestamp=datetime(2025, 1, 1, 2, 55, tzinfo=UTC),
        bars=bars,
    )

    signal = strategy.evaluate_symbol(context)

    assert signal is not None
    assert signal.params is not None
    assert signal.params["composite_explainability"]["blocked"] is False
