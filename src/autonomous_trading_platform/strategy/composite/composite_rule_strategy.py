from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry
from autonomous_trading_platform.strategy.composite.component_evaluation_result import (
    ComponentEvaluationResult,
)
from autonomous_trading_platform.strategy.composite.component_execution_context import (
    ComponentExecutionContext,
)
from autonomous_trading_platform.strategy.composite.composite_strategy_config import (
    CompositeFilterConfig,
    CompositeInputReference,
    CompositeRuleConfig,
    CompositeStrategyConfig,
)
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
    extract_closes,
    extract_volumes,
)
from autonomous_trading_platform.strategy.signal_logic.aggregation import (
    LogicalAggregator,
    VotingAggregator,
    WeightedScoreAggregator,
    WeightedSignalInput,
)
from autonomous_trading_platform.strategy.signal_logic.base_signal_rule import SignalRuleResult
from autonomous_trading_platform.strategy.signal_logic.comparison_rule import ComparisonRule
from autonomous_trading_platform.strategy.signal_logic.crossover_rule import CrossoverRule
from autonomous_trading_platform.strategy.signal_logic.threshold_rule import ThresholdRule


class CompositeRuleStrategy(BaseStrategy):
    """Execute declarative strategies assembled from registered components."""

    def __init__(
        self,
        strategy_id: str,
        config: CompositeStrategyConfig | dict[str, Any],
    ) -> None:
        self._strategy_id = strategy_id
        self.config = cast(CompositeStrategyConfig, CompositeStrategyConfig.model_validate(config))
        self._last_explainability: dict[str, Any] | None = None

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def warmup_bars(self) -> int:
        return int(self.config.warmup_bars())

    @property
    def last_explainability(self) -> dict[str, Any] | None:
        return self._last_explainability

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        execution_context = self._build_execution_context(context)
        explainability: dict[str, Any] = {
            "strategy_type": "composite_rule",
            "strategy_id": self.strategy_id,
            "symbol": context.symbol,
            "bar_timestamp": context.bar_timestamp.isoformat(),
            "warmup_bars": self.warmup_bars,
            "indicators": [],
            "filters": [],
            "entry_rules": [],
            "confirmations": [],
            "aggregation": None,
            "confidence": None,
            "blocked": False,
            "blocked_by": None,
        }

        if len(context.bars) < self.warmup_bars:
            explainability["blocked"] = True
            explainability["blocked_by"] = {
                "stage": "warmup",
                "reason": "insufficient_bars",
                "bars_available": len(context.bars),
                "warmup_bars": self.warmup_bars,
            }
            self._last_explainability = explainability
            return None

        indicator_results = self._evaluate_indicators(execution_context)
        explainability["indicators"] = [
            result.model_dump(mode="json") for result in indicator_results
        ]
        missing = [result for result in indicator_results if result.output is None]
        if missing:
            explainability["blocked"] = True
            explainability["blocked_by"] = {
                "stage": "indicators",
                "reason": "indicator_returned_none",
                "component_ids": [result.component_id for result in missing],
            }
            self._last_explainability = explainability
            return None

        filter_results = self._evaluate_filters(execution_context)
        explainability["filters"] = [result.model_dump(mode="json") for result in filter_results]
        failed_filters = [result for result in filter_results if result.passed is False]
        if failed_filters:
            explainability["blocked"] = True
            explainability["blocked_by"] = {
                "stage": "filters",
                "reason": "filter_blocked_signal",
                "component_ids": [result.component_id for result in failed_filters],
            }
            self._last_explainability = explainability
            return None

        entry_results = [
            self._evaluate_rule(rule, execution_context) for rule in self.config.entry_rules
        ]
        confirmation_results = [
            self._evaluate_rule(rule, execution_context) for rule in self.config.confirmations
        ]
        explainability["entry_rules"] = [
            self._explain_rule(rule, result)
            for rule, result in zip(self.config.entry_rules, entry_results, strict=True)
        ]
        explainability["confirmations"] = [
            self._explain_rule(rule, result)
            for rule, result in zip(self.config.confirmations, confirmation_results, strict=True)
        ]

        aggregate_result = self._aggregate([*entry_results, *confirmation_results])
        confidence = self._score_confidence(
            aggregate_result, [*entry_results, *confirmation_results]
        )
        aggregation_explainability = self._explain_aggregation(aggregate_result)
        explainability["aggregation"] = aggregation_explainability
        explainability["confidence"] = {
            "mode": self.config.confidence_scoring.mode,
            "value": confidence,
            "aggregation_confidence": aggregate_result.confidence,
            "floor": self.config.confidence_scoring.floor,
            "cap": self.config.confidence_scoring.cap,
        }

        if aggregate_result.direction is None:
            explainability["blocked"] = True
            explainability["blocked_by"] = {
                "stage": "aggregation",
                "reason": aggregate_result.reason,
            }
            self._last_explainability = explainability
            return None

        params = {
            "strategy_type": "composite_rule",
            "aggregator": self.config.aggregator.component,
            "confidence": confidence,
            "composite_explainability": explainability,
        }
        signal = Signal(
            signal_id=build_signal_id(
                run_id=context.run_id,
                strategy_id=context.strategy_id,
                symbol=context.symbol,
                bar_timestamp=context.bar_timestamp,
                direction=aggregate_result.direction,
                params=params,
            ),
            run_id=context.run_id,
            timestamp=context.evaluation_timestamp,
            bar_timestamp=context.bar_timestamp,
            strategy_id=context.strategy_id,
            symbol=context.symbol,
            direction=aggregate_result.direction,
            confidence=confidence,
            params=params,
        )
        self._last_explainability = explainability
        return signal

    def _build_execution_context(self, context: StrategyContext) -> ComponentExecutionContext:
        closes = extract_closes(context.bars)
        volumes = None
        if any(
            "volume"
            in get_component_registry()
            .get_component_definition(indicator.component)
            .required_bar_fields
            for indicator in self.config.indicators
        ):
            volumes = extract_volumes(context.bars)
        returns = [
            (closes[index] - closes[index - 1]) / closes[index - 1]
            for index in range(1, len(closes))
            if closes[index - 1] != 0
        ]
        return ComponentExecutionContext(
            strategy_context=context,
            closes=closes,
            volumes=volumes,
            returns=returns,
        )

    def _evaluate_indicators(
        self,
        execution_context: ComponentExecutionContext,
    ) -> list[ComponentEvaluationResult]:
        results: list[ComponentEvaluationResult] = []
        for indicator in self.config.indicators:
            output = self._indicator_value(indicator.id, 0, execution_context)
            execution_context.indicator_outputs[indicator.id] = output
            results.append(
                ComponentEvaluationResult(
                    component_id=indicator.id,
                    component_name=indicator.component,
                    component_type=ComponentType.INDICATOR.value,
                    reason="indicator_calculated"
                    if output is not None
                    else "indicator_unavailable",
                    output=output,
                    parameters=self._component_parameters(
                        indicator.component, indicator.parameters
                    ),
                )
            )
        return results

    def _indicator_value(
        self,
        indicator_id: str,
        offset: int,
        execution_context: ComponentExecutionContext,
    ) -> Any:
        key = (indicator_id, offset)
        if key in execution_context.indicator_cache:
            return execution_context.indicator_cache[key]

        indicator = next(item for item in self.config.indicators if item.id == indicator_id)
        defn = get_component_registry().get_component_definition(indicator.component)
        inputs = self._indicator_inputs(defn.required_inputs, offset, execution_context)
        params = self._component_parameters(indicator.component, indicator.parameters)
        implementation = cast(Callable[..., Any] | None, defn.implementation)
        if implementation is None:
            raise ValueError(f"Indicator component {indicator.component!r} is not executable")
        output = implementation(**inputs, **params)
        execution_context.indicator_cache[key] = output
        return output

    def _indicator_inputs(
        self,
        required_inputs: tuple[str, ...],
        offset: int,
        execution_context: ComponentExecutionContext,
    ) -> dict[str, Any]:
        end = (
            len(execution_context.closes) + offset if offset < 0 else len(execution_context.closes)
        )
        inputs: dict[str, Any] = {}
        for input_name in required_inputs:
            if input_name == "values":
                inputs[input_name] = execution_context.closes[:end]
            elif input_name == "volumes":
                if execution_context.volumes is None:
                    raise ValueError("volume input requested but bars do not expose volume")
                inputs[input_name] = execution_context.volumes[:end]
            elif input_name == "returns":
                return_end = max(end - 1, 0)
                inputs[input_name] = execution_context.returns[:return_end]
            else:
                raise ValueError(f"Unsupported indicator input {input_name!r}")
        return inputs

    def _evaluate_filters(
        self,
        execution_context: ComponentExecutionContext,
    ) -> list[ComponentEvaluationResult]:
        return [
            self._evaluate_filter(filter_config, execution_context)
            for filter_config in self.config.filters
        ]

    def _evaluate_filter(
        self,
        filter_config: CompositeFilterConfig,
        execution_context: ComponentExecutionContext,
    ) -> ComponentEvaluationResult:
        value = self._resolve_input(filter_config.input, execution_context)
        passed = self._compare(value, filter_config.operator, filter_config.threshold)
        reason = (
            "filter_passed"
            if passed
            else filter_config.block_reason or f"{filter_config.component}_blocked_signal"
        )
        return ComponentEvaluationResult(
            component_id=filter_config.id,
            component_name=filter_config.component,
            component_type=ComponentType.FILTER.value,
            passed=passed,
            reason=reason,
            inputs={"input": value, "threshold": filter_config.threshold},
            output=passed,
            parameters={
                "operator": filter_config.operator,
                **filter_config.parameters,
            },
        )

    def _evaluate_rule(
        self,
        rule: CompositeRuleConfig,
        execution_context: ComponentExecutionContext,
    ) -> SignalRuleResult:
        inputs = {
            input_name: self._resolve_input(ref, execution_context)
            for input_name, ref in rule.inputs.items()
        }
        params = self._component_parameters(
            rule.component,
            rule.parameters,
            include_component_defaults=False,
        )

        if rule.component == "threshold":
            return ThresholdRule(**inputs, **params).evaluate()
        if rule.component == "crossover":
            return CrossoverRule(**inputs, **params).evaluate()
        if rule.component == "comparison":
            return ComparisonRule(
                left_name=rule.inputs["left_value"].name
                or rule.inputs["left_value"].indicator_id
                or "left",
                left_value=inputs["left_value"],
                right_name=rule.inputs["right_value"].name
                or rule.inputs["right_value"].indicator_id
                or "right",
                right_value=inputs["right_value"],
                **params,
            ).evaluate()
        raise ValueError(f"Unsupported signal rule component {rule.component!r}")

    def _resolve_input(
        self,
        ref: CompositeInputReference,
        execution_context: ComponentExecutionContext,
    ) -> Any:
        if ref.indicator_id is not None:
            return self._indicator_value(ref.indicator_id, ref.offset, execution_context)
        return ref.value

    def _aggregate(self, results: list[SignalRuleResult]) -> SignalRuleResult:
        params = self._component_parameters(
            self.config.aggregator.component,
            self.config.aggregator.parameters,
            include_component_defaults=False,
        )
        if self.config.aggregator.component == "voting":
            return VotingAggregator(**params).aggregate(results)
        if self.config.aggregator.component == "weighted_score":
            aggregator = WeightedScoreAggregator(**params)
            rules = [*self.config.entry_rules, *self.config.confirmations]
            weighted_inputs = [
                WeightedSignalInput(result=result, weight=rule.weight)
                for rule, result in zip(rules, results, strict=True)
            ]
            return aggregator.aggregate_weighted(weighted_inputs)
        if self.config.aggregator.component in {"logical_and", "logical_or"}:
            direction = SignalDirection(
                params.get("target_direction")
                or params.get("direction")
                or SignalDirection.BUY.value
            )
            operator = "AND" if self.config.aggregator.component == "logical_and" else "OR"
            return LogicalAggregator(operator=operator, direction=direction).aggregate(results)
        raise ValueError(f"Unsupported aggregator component {self.config.aggregator.component!r}")

    def _score_confidence(
        self,
        aggregate_result: SignalRuleResult,
        rule_results: list[SignalRuleResult],
    ) -> float:
        config = self.config.confidence_scoring
        if config.mode == "weighted":
            weights = [
                rule.weight for rule in [*self.config.entry_rules, *self.config.confirmations]
            ]
            total_weight = sum(weights)
            raw = (
                sum(
                    result.confidence * weight
                    for result, weight in zip(rule_results, weights, strict=True)
                )
                / total_weight
                if total_weight
                else aggregate_result.confidence
            )
        else:
            raw = aggregate_result.confidence
        return float(max(config.floor, min(config.cap, float(raw))))

    def _explain_rule(self, rule: CompositeRuleConfig, result: SignalRuleResult) -> dict[str, Any]:
        return {
            "component_id": rule.id,
            "component_name": rule.component,
            "component_type": ComponentType.SIGNAL_RULE.value,
            "direction": result.direction.value if result.direction else None,
            "confidence": result.confidence,
            "reason": result.reason,
            "params": result.params,
            "weight": rule.weight,
        }

    def _explain_aggregation(self, result: SignalRuleResult) -> dict[str, Any]:
        return {
            "component_name": self.config.aggregator.component,
            "component_type": ComponentType.AGGREGATOR.value,
            "direction": result.direction.value if result.direction else None,
            "confidence": result.confidence,
            "reason": result.reason,
            "params": result.params,
        }

    def _component_parameters(
        self,
        component_name: str,
        configured: dict[str, Any],
        *,
        include_component_defaults: bool = True,
    ) -> dict[str, Any]:
        if component_name in {"logical_and", "logical_or"}:
            return dict(configured)

        if not include_component_defaults:
            return dict(configured)

        defn = get_component_registry().get_component_definition(component_name)
        defaults = {spec.name: spec.default for spec in defn.parameter_specs}
        return {**defaults, **configured}

    @staticmethod
    def _compare(value: Any, operator: str, threshold: float | bool) -> bool:
        if operator == "gt":
            return bool(value > threshold)
        if operator == "gte":
            return bool(value >= threshold)
        if operator == "lt":
            return bool(value < threshold)
        if operator == "lte":
            return bool(value <= threshold)
        if operator == "eq":
            return bool(value == threshold)
        if operator == "neq":
            return bool(value != threshold)
        raise ValueError(f"Unsupported filter operator {operator!r}")
