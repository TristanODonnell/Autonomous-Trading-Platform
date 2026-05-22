from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.composite import CompositeRuleStrategy
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from tests.utilities.factories import make_five_minute_bar


def _context(closes: list[float]) -> StrategyContext:
    base_ts = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
    bars = [
        make_five_minute_bar(
            timestamp=base_ts + timedelta(minutes=5 * index),
            close_price=str(close),
            open_price=str(close),
            high_price=str(close),
            low_price=str(close),
        )
        for index, close in enumerate(closes)
    ]
    return StrategyContext(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        strategy_id="weighted-composite",
        symbol="AAPL",
        evaluation_timestamp=base_ts + timedelta(minutes=5 * len(bars)),
        bar_timestamp=base_ts + timedelta(minutes=5 * (len(bars) - 1)),
        bars=bars,
    )


def test_weighted_score_aggregation_uses_configured_rule_weights() -> None:
    strategy = CompositeRuleStrategy(
        "weighted",
        {
            "indicators": [
                {"id": "mom_1", "component": "momentum", "parameters": {"lookback": 1}},
                {"id": "roc_1", "component": "rate_of_change", "parameters": {"lookback": 1}},
            ],
            "entry_rules": [
                {
                    "id": "buy_rule",
                    "component": "threshold",
                    "inputs": {"value": {"indicator_id": "mom_1"}},
                    "parameters": {"buy_above": 0.0, "confidence": 0.9},
                    "weight": 3.0,
                },
                {
                    "id": "sell_rule",
                    "component": "threshold",
                    "inputs": {"value": {"indicator_id": "roc_1"}},
                    "parameters": {"sell_above": 0.0, "confidence": 0.3},
                    "weight": 1.0,
                },
            ],
            "aggregator": {
                "component": "weighted_score",
                "parameters": {"buy_threshold": 0.5, "sell_threshold": -0.5},
            },
        },
    )

    signal = strategy.evaluate_symbol(_context([10, 11, 12]))

    assert signal is not None
    assert signal.direction == SignalDirection.BUY
    assert signal.confidence == pytest.approx(0.6)
    assert signal.params is not None
    assert signal.params["composite_explainability"]["aggregation"]["params"][
        "normalized_score"
    ] == pytest.approx(0.6)
