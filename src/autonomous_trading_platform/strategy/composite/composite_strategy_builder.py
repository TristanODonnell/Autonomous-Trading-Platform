from __future__ import annotations

from typing import Any

from autonomous_trading_platform.strategy.composite.composite_rule_strategy import (
    CompositeRuleStrategy,
)
from autonomous_trading_platform.strategy.composite.composite_strategy_config import (
    CompositeStrategyConfig,
)


def build_composite_rule_strategy(
    strategy_id: str,
    parameters: dict[str, Any],
) -> CompositeRuleStrategy:
    return CompositeRuleStrategy(
        strategy_id=strategy_id,
        config=CompositeStrategyConfig.model_validate(parameters),
    )
