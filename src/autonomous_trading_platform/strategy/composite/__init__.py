"""Config-driven composite strategy execution."""

from __future__ import annotations

from .component_evaluation_result import ComponentEvaluationResult
from .component_execution_context import ComponentExecutionContext
from .composite_rule_strategy import CompositeRuleStrategy
from .composite_strategy_builder import build_composite_rule_strategy
from .composite_strategy_config import (
    CompositeAggregatorConfig,
    CompositeConfidenceConfig,
    CompositeFilterConfig,
    CompositeIndicatorConfig,
    CompositeInputReference,
    CompositeRuleConfig,
    CompositeStrategyConfig,
)

__all__ = [
    "CompositeAggregatorConfig",
    "CompositeConfidenceConfig",
    "CompositeFilterConfig",
    "CompositeIndicatorConfig",
    "CompositeInputReference",
    "CompositeRuleConfig",
    "CompositeRuleStrategy",
    "CompositeStrategyConfig",
    "ComponentEvaluationResult",
    "ComponentExecutionContext",
    "build_composite_rule_strategy",
]
