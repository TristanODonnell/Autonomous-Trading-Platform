from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from autonomous_trading_platform.research.strategy_generation.compatibility import (
    strategy_is_generation_compatible,
)
from autonomous_trading_platform.research.strategy_generation.composite_generation import (
    generate_composite_rule_configs,
)
from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
    GenerationResult,
    GenerationSummary,
)
from autonomous_trading_platform.research.strategy_generation.generators.base_generator import (
    BaseStrategyGenerator,
)
from autonomous_trading_platform.research.strategy_generation.generators.evolutionary_generator import (
    EvolutionaryGenerator,
)
from autonomous_trading_platform.research.strategy_generation.generators.grid_search_generator import (
    GridSearchGenerator,
)
from autonomous_trading_platform.research.strategy_generation.generators.random_sampling_generator import (
    RandomSamplingGenerator,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.registry import StrategyFamily, get_registry


class StrategyGenerationEngine:
    def __init__(self, generator: BaseStrategyGenerator | None = None) -> None:
        self.generator = generator or GridSearchGenerator()

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list] | None = None,
        *,
        method: str | None = None,
        options: GenerationOptions | Mapping[str, Any] | None = None,
    ) -> list[StrategyConfig]:
        return self.generate_result(
            strategy_type=strategy_type,
            method=method,
            parameter_space=parameter_space,
            options=options,
        ).configs

    def generate_result(
        self,
        strategy_type: str,
        *,
        method: str | None = None,
        parameter_space: dict[str, list] | None = None,
        options: GenerationOptions | Mapping[str, Any] | None = None,
    ) -> GenerationResult:
        opts = _normalize_options(options)
        generator_options = opts if options is not None else None
        registry = get_registry()
        defn = registry.get_definition(strategy_type)
        compatible, reason = strategy_is_generation_compatible(defn, opts)
        if not compatible:
            summary = GenerationSummary()
            summary.reject(
                reason or "incompatible_strategy",
                strategy_type=strategy_type,
                generator=method or _method_for_generator(self.generator),
            )
            return GenerationResult(configs=[], summary=summary)

        selected_method = method or _method_for_generator(self.generator)
        if strategy_type == "composite_rule":
            composite_configs, summary = generate_composite_rule_configs(
                options=opts,
                method=selected_method,
            )
            return GenerationResult(configs=composite_configs, summary=summary)

        generator = self._generator_for_method(selected_method)
        seen: set[str] = set()
        configs: list[StrategyConfig] = []
        summary = GenerationSummary()

        for config in generator.generate(strategy_type, parameter_space, generator_options):
            config_hash = config.config_hash()
            if config_hash in seen:
                summary.duplicate(
                    strategy_type=config.type,
                    config_hash=config_hash,
                    generator=selected_method,
                    parameters=config.parameters,
                )
                continue
            seen.add(config_hash)
            configs.append(config)
            summary.accepted_count += 1
            summary.strategy_type_distribution[config.type] += 1
            summary.family_distribution[defn.family.value] += 1

        summary.generated_count += generator.last_summary.generated_count
        summary.rejected_count += generator.last_summary.rejected_count
        summary.rejection_reasons.update(generator.last_summary.rejection_reasons)
        summary.rejected_details.extend(generator.last_summary.rejected_details)
        summary.duplicate_details.extend(generator.last_summary.duplicate_details)
        return GenerationResult(configs=configs, summary=summary)

    def generate_for_family(
        self,
        family: StrategyFamily | str,
        *,
        method: str = "grid",
        options: GenerationOptions | Mapping[str, Any] | None = None,
    ) -> GenerationResult:
        opts = _normalize_options(options)
        family_value = StrategyFamily(family).value
        opts = replace(opts, allowed_families=(family_value,))
        configs: list[StrategyConfig] = []
        summary = GenerationSummary()
        for defn in get_registry().get_family_strategies(StrategyFamily(family)):
            result = self.generate_result(
                defn.strategy_type,
                method=method,
                options=opts,
            )
            configs.extend(result.configs)
            _merge_summary(summary, result.summary)
        return GenerationResult(configs=configs, summary=summary)

    def generate_composite(
        self,
        *,
        method: str = "grid",
        options: GenerationOptions | Mapping[str, Any] | None = None,
    ) -> GenerationResult:
        return self.generate_result(
            "composite_rule",
            method=method,
            options=options,
        )

    def _generator_for_method(self, method: str) -> BaseStrategyGenerator:
        if method == "grid":
            if isinstance(self.generator, GridSearchGenerator):
                return self.generator
            return GridSearchGenerator()
        if method == "random":
            if isinstance(self.generator, RandomSamplingGenerator):
                return self.generator
            return RandomSamplingGenerator()
        if method == "evolutionary":
            if isinstance(self.generator, EvolutionaryGenerator):
                return self.generator
            return EvolutionaryGenerator()
        raise ValueError(f"Unknown generation method {method!r}")


def _normalize_options(
    options: GenerationOptions | Mapping[str, Any] | None,
) -> GenerationOptions:
    if options is None:
        return GenerationOptions()
    if isinstance(options, GenerationOptions):
        return options
    return GenerationOptions(**dict(options))


def _method_for_generator(generator: BaseStrategyGenerator) -> str:
    if isinstance(generator, RandomSamplingGenerator):
        return "random"
    if isinstance(generator, EvolutionaryGenerator):
        return "evolutionary"
    return "grid"


def _merge_summary(target: GenerationSummary, source: GenerationSummary) -> None:
    target.generated_count += source.generated_count
    target.accepted_count += source.accepted_count
    target.duplicate_count += source.duplicate_count
    target.rejected_count += source.rejected_count
    target.rejection_reasons.update(source.rejection_reasons)
    target.strategy_type_distribution.update(source.strategy_type_distribution)
    target.family_distribution.update(source.family_distribution)
    target.rejected_details.extend(source.rejected_details)
    target.duplicate_details.extend(source.duplicate_details)
