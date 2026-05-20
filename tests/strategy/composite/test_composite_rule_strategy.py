from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.composite import CompositeRuleStrategy
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_bars(closes: list[float], volumes: list[int] | None = None):
    volumes = volumes or [100 for _ in closes]
    return [
        make_five_minute_bar(
            timestamp=_BASE_TS + timedelta(minutes=5 * index),
            close_price=str(close),
            open_price=str(close),
            high_price=str(close),
            low_price=str(close),
            volume=volumes[index],
        )
        for index, close in enumerate(closes)
    ]


def _make_context(bars) -> StrategyContext:
    ts = _BASE_TS + timedelta(minutes=5 * len(bars))
    return StrategyContext(
        run_id=_RUN_ID,
        strategy_id="composite-test",
        symbol="AAPL",
        evaluation_timestamp=ts,
        bar_timestamp=ts - timedelta(minutes=5),
        bars=bars,
    )


def _trend_volume_config() -> dict:
    return {
        "indicators": [
            {"id": "mom_2", "component": "momentum", "parameters": {"lookback": 2}},
            {"id": "volume_ratio_3", "component": "volume_ratio", "parameters": {"window": 3}},
        ],
        "filters": [
            {
                "id": "liquidity_gate",
                "component": "liquidity_filter",
                "input": {"indicator_id": "volume_ratio_3"},
                "operator": "gte",
                "threshold": 0.5,
                "block_reason": "volume_ratio_below_minimum",
            }
        ],
        "entry_rules": [
            {
                "id": "momentum_entry",
                "component": "threshold",
                "inputs": {"value": {"indicator_id": "mom_2"}},
                "parameters": {"buy_above": 0.0, "sell_below": 0.0, "confidence": 0.7},
                "weight": 2.0,
            }
        ],
        "confirmations": [
            {
                "id": "volume_confirmation",
                "component": "threshold",
                "inputs": {"value": {"indicator_id": "volume_ratio_3"}},
                "parameters": {"buy_above": 0.9, "confidence": 0.6},
                "weight": 1.0,
            }
        ],
        "aggregator": {
            "component": "weighted_score",
            "parameters": {"buy_threshold": 0.5, "sell_threshold": -0.5},
        },
        "confidence_scoring": {"mode": "aggregation", "floor": 0.0, "cap": 1.0},
    }


def test_composite_strategy_emits_signal_with_structured_explainability() -> None:
    strategy = CompositeRuleStrategy("composite-v1", _trend_volume_config())

    signal = strategy.evaluate_symbol(_make_context(_make_bars([10, 11, 12, 13, 14])))

    assert signal is not None
    assert signal.direction == SignalDirection.BUY
    assert signal.params is not None
    explainability = signal.params["composite_explainability"]
    assert explainability["blocked"] is False
    assert [item["component_id"] for item in explainability["indicators"]] == [
        "mom_2",
        "volume_ratio_3",
    ]
    assert explainability["entry_rules"][0]["component_id"] == "momentum_entry"
    assert explainability["confirmations"][0]["component_id"] == "volume_confirmation"
    assert explainability["aggregation"]["component_name"] == "weighted_score"


def test_filters_gate_before_final_aggregation_and_explain_block() -> None:
    config = _trend_volume_config()
    config["filters"][0]["threshold"] = 2.0
    strategy = CompositeRuleStrategy("composite-v1", config)

    signal = strategy.evaluate_symbol(_make_context(_make_bars([10, 11, 12, 13, 14])))

    assert signal is None
    explainability = strategy.last_explainability
    assert explainability is not None
    assert explainability["blocked_by"]["stage"] == "filters"
    assert explainability["filters"][0]["reason"] == "volume_ratio_below_minimum"
    assert explainability["entry_rules"] == []


def test_signal_id_is_deterministic_for_same_composite_inputs() -> None:
    strategy = CompositeRuleStrategy("composite-v1", _trend_volume_config())
    context = _make_context(_make_bars([10, 11, 12, 13, 14]))

    first = strategy.evaluate_symbol(context)
    second = strategy.evaluate_symbol(context)

    assert first is not None and second is not None
    assert first.signal_id == second.signal_id


def test_strategy_factory_builds_composite_strategy() -> None:
    config = StrategyConfig(
        type="composite_rule",
        strategy_id="factory-composite",
        parameters=_trend_volume_config(),
    )

    strategy = StrategyFactory().build(config)

    assert isinstance(strategy, CompositeRuleStrategy)
