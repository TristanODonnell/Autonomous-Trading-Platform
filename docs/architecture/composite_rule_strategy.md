# Composite Rule Strategy

## Purpose

`CompositeRuleStrategy` is the first executable composition layer for
declarative strategies. It assembles registered indicators, signal rules,
filters, confirmations, aggregators, and confidence scoring into a concrete
`BaseStrategy` implementation without requiring a new Python class for every
strategy idea.

Existing non-composite strategies are unchanged. Composite execution reuses the
registered primitives from `ComponentRegistry` and the existing
`strategy.signal_logic` rule and aggregator classes.

## Location

```
src/autonomous_trading_platform/strategy/composite/
  composite_rule_strategy.py
  composite_strategy_config.py
  component_execution_context.py
  component_evaluation_result.py
  composite_strategy_builder.py
```

`composite_rule` is registered in `StrategyRegistry` with
`family=StrategyFamily.COMPOSITE`.

## Config Shape

Composite configs are normalized and validated through
`CompositeStrategyConfig`:

```json
{
  "strategy_type": "composite_rule",
  "indicators": [
    {
      "id": "momentum_5",
      "component": "momentum",
      "parameters": {"lookback": 5}
    }
  ],
  "filters": [],
  "entry_rules": [
    {
      "id": "momentum_entry",
      "component": "threshold",
      "inputs": {"value": {"indicator_id": "momentum_5"}},
      "parameters": {"buy_above": 0.0, "sell_below": 0.0, "confidence": 0.55},
      "weight": 1.0
    }
  ],
  "confirmations": [],
  "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
  "confidence_scoring": {"mode": "aggregation", "floor": 0.0, "cap": 1.0},
  "sizing": {},
  "metadata": {}
}
```

Indicator IDs must be deterministic and unique within the config. Rule,
confirmation, and filter input references are validated against declared
indicator IDs before execution.

## Execution Flow

Execution order is deterministic:

1. Derive close, volume, and return inputs from completed bars.
2. Calculate declared indicators and cache outputs by `(indicator_id, offset)`.
3. Evaluate filters. Failed filters block before entry rules or aggregation.
4. Evaluate entry rules.
5. Evaluate confirmations as separate rule results.
6. Aggregate entry and confirmation results.
7. Score final confidence.
8. Emit a `Signal` only when aggregation returns a direction.

The strategy uses only the bars supplied in `StrategyContext.bars`. Input
references may use `offset=0` for current completed-bar indicator values or a
negative offset such as `-1` for previous completed-bar values. Positive
offsets are rejected to prevent lookahead.

## Supported Components

Executable indicators:

- `simple_moving_average`
- `exponential_moving_average`
- `momentum`
- `rate_of_change`
- `rsi`
- `rsi_wilder`
- `z_score`
- `distance_from_moving_average`
- `rolling_standard_deviation`
- `realized_volatility`
- `average_volume`
- `volume_ratio`
- `volume_spike`

Executable signal rules:

- `threshold`
- `crossover`
- `comparison`

Executable aggregators:

- `voting`
- `weighted_score`
- `logical_and`
- `logical_or`

Filters reference registered filter metadata (`volatility_filter`,
`liquidity_filter`, `regime_filter`, `time_filter`) and execute through a
lightweight composite-local comparison operator. They are intentionally simple
until dedicated filter implementations exist.

## Explainability

The `Signal` contract remains backward compatible. Composite diagnostics are
stored under `Signal.params["composite_explainability"]` for emitted signals,
and on `CompositeRuleStrategy.last_explainability` for blocked evaluations.

Structure:

```json
{
  "strategy_type": "composite_rule",
  "strategy_id": "example",
  "symbol": "AAPL",
  "bar_timestamp": "2025-01-15T10:20:00+00:00",
  "warmup_bars": 20,
  "indicators": [],
  "filters": [],
  "entry_rules": [],
  "confirmations": [],
  "aggregation": {},
  "confidence": {},
  "blocked": false,
  "blocked_by": null
}
```

Filter-blocked evaluations set `blocked=true` and include
`blocked_by.stage="filters"` with the filter IDs that blocked the signal.
Confirmations remain separate from entry rules and do not mutate entry rule
outputs.

## Warmup

Warmup is derived from component metadata:

- indicator `warmup_formula`
- indicator `warmup_parameter`
- component `warmup_bars`
- negative input offsets used by rules, confirmations, or filters

Examples:

- `simple_moving_average(window=200)` plus `rsi(window=14)` requires `200`
  bars.
- `simple_moving_average(window=5)` used with `offset=-1` requires `6` bars.
- `momentum(lookback=20)` plus `rolling_standard_deviation(window=50)` requires
  `50` bars.

## Example: Trend Momentum With Volume Confirmation

```json
{
  "strategy_type": "composite_rule",
  "indicators": [
    {"id": "momentum_5", "component": "momentum", "parameters": {"lookback": 5}},
    {"id": "volume_ratio_20", "component": "volume_ratio", "parameters": {"window": 20}}
  ],
  "filters": [
    {
      "id": "liquidity_gate",
      "component": "liquidity_filter",
      "input": {"indicator_id": "volume_ratio_20"},
      "operator": "gte",
      "threshold": 0.5,
      "block_reason": "volume_ratio_below_minimum"
    }
  ],
  "entry_rules": [
    {
      "id": "momentum_entry",
      "component": "threshold",
      "inputs": {"value": {"indicator_id": "momentum_5"}},
      "parameters": {"buy_above": 0.0, "sell_below": 0.0, "confidence": 0.7},
      "weight": 2.0
    }
  ],
  "confirmations": [
    {
      "id": "volume_confirmation",
      "component": "threshold",
      "inputs": {"value": {"indicator_id": "volume_ratio_20"}},
      "parameters": {"buy_above": 1.0, "confidence": 0.6},
      "weight": 1.0
    }
  ],
  "aggregator": {
    "component": "weighted_score",
    "parameters": {"buy_threshold": 0.5, "sell_threshold": -0.5}
  }
}
```

## Example: Moving Average Crossover

```json
{
  "strategy_type": "composite_rule",
  "indicators": [
    {"id": "fast_sma", "component": "simple_moving_average", "parameters": {"window": 10}},
    {"id": "slow_sma", "component": "simple_moving_average", "parameters": {"window": 30}}
  ],
  "entry_rules": [
    {
      "id": "sma_cross",
      "component": "crossover",
      "inputs": {
        "previous_fast": {"indicator_id": "fast_sma", "offset": -1},
        "previous_slow": {"indicator_id": "slow_sma", "offset": -1},
        "current_fast": {"indicator_id": "fast_sma"},
        "current_slow": {"indicator_id": "slow_sma"}
      },
      "parameters": {"confidence": 0.6}
    }
  ],
  "aggregator": {"component": "voting", "parameters": {"min_votes": 1}}
}
```

## Current Boundaries

Composite strategies do not yet implement automatic strategy generation,
persisted feature dependency execution, mutation/evolution metadata consumers,
distributed orchestration, portfolio-level ensembles, or automatic conversion
of existing concrete strategies into composite configs.
