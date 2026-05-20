from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

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
