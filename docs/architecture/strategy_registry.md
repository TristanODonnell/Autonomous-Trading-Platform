# Strategy Registry

## Purpose

`strategy.registry` is the canonical single source of truth for all strategy
metadata.  It replaces the lightweight catalog introduced in Stage 0 and serves
as the foundation for:

- composable strategy systems
- parameter-aware strategy generation
- compatibility-aware generation
- feature dependency resolution
- warmup inference
- search-space generation
- future composite and ensemble strategies

---

## Location

```
src/autonomous_trading_platform/strategy/registry/
  __init__.py              # Public API; triggers registration on import
  strategy_family.py       # StrategyFamily enum
  parameter_metadata.py    # ParameterType, ParameterSpec
  strategy_definition.py   # StrategyDefinition dataclass
  strategy_registry.py     # StrategyRegistry class + singleton
  validators.py            # Per-strategy parameter validator functions
  _registrations.py        # All strategy registrations (runs once on import)
```

---

## StrategyDefinition Fields

| Group | Field | Type | Description |
|---|---|---|---|
| **Core identity** | `strategy_type` | `str` | Canonical type key |
| | `display_name` | `str` | Human-readable name |
| | `description` | `str` | Strategy purpose |
| | `family` | `StrategyFamily` | Canonical family enum |
| | `implementation_class` | `type` | Concrete strategy class |
| **Classification** | `debug` | `bool` | Test-only flag |
| | `production_ready` | `bool` | Production flag |
| **Parameters** | `default_parameters` | `dict[str, Any]` | Canonical defaults |
| | `parameter_validator` | `Callable` | Validates a parameters dict |
| **Warmup/deps** | `warmup_bars_fn` | `Callable` | Returns minimum bars from params |
| | `required_indicators` | `tuple[str, ...]` | Indicator function names used |
| | `required_persisted_features` | `tuple[str, ...]` | Parquet features (empty now) |
| **Generation** | `parameter_specs` | `tuple[ParameterSpec, ...]` | Search-space specs per param |
| **Compatibility** | `supports_long_only` | `bool` | Default: `True` |
| | `supports_shorting` | `bool` | Default: `True` |
| | `supports_intraday` | `bool` | Default: `True` |
| | `supports_daily` | `bool` | Default: `True` |
| | `supports_adjusted_prices` | `bool` | Default: `True` |
| | `supports_raw_prices` | `bool` | Default: `True` |
| **Operational** | `deterministic` | `bool` | Reproducible given same params |
| | `builder` | `Callable` | `(strategy_id, params) -> BaseStrategy` |

---

## ParameterSpec Fields

Each `ParameterSpec` describes one tunable parameter's search space:

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Parameter key (matches `default_parameters`) |
| `parameter_type` | `ParameterType` | INT, FLOAT, BOOL, STRING |
| `default` | `Any` | Default value |
| `description` | `str` | Human description |
| `min_value` | `float \| None` | Search space lower bound |
| `max_value` | `float \| None` | Search space upper bound |
| `discrete` | `bool` | True for integer grid search |
| `step` | `float \| None` | Grid step size (discrete only) |
| `tunable` | `bool` | Whether to include in optimization |

---

## Strategy Family Classification

| Strategy Type | Family | Debug | Production Ready |
|---|---|---|---|
| `stub` | `DEBUG` | Yes | No |
| `intentional_loser` | `DEBUG` | Yes | No |
| `random` | `DEBUG` | Yes | No |
| `moving_average_crossover` | `TREND` | No | Yes |
| `momentum` | `MOMENTUM` | No | Yes |
| `mean_reversion` | `MEAN_REVERSION` | No | Yes |
| `factor_based` | `FACTOR` | No | Yes |

Available families (enum `StrategyFamily`):
`MOMENTUM`, `MEAN_REVERSION`, `TREND`, `FACTOR`, `COMPOSITE`, `ENSEMBLE`, `DEBUG`

---

## Warmup Metadata

Warmup bars are computed at runtime from strategy parameters via `warmup_bars_fn`:

| Strategy | Formula |
|---|---|
| `stub` | `1` |
| `intentional_loser` | `1` |
| `random` | `0` |
| `moving_average_crossover` | `long_window + 1` |
| `momentum` | `lookback + 1` |
| `mean_reversion` | `window` |
| `factor_based` | `max(momentum_lookback+1, mean_reversion_window, volatility_window, volume_window)` |

Use `defn.compute_warmup_bars(parameters)` or `defn.compute_warmup_bars()` to get the warmup for default parameters.

---

## Registration Lifecycle

1. `strategy.registry.__init__` is imported.
2. `__init__` imports `_registrations`, which runs once.
3. `_registrations` calls `_REGISTRY.register()` for each strategy in order.
4. After all registrations, `_REGISTRY.lock()` is called.
5. Any subsequent `register()` call raises `RuntimeError`.

Registration is order-preserving and stable.  All list APIs return results in
original registration order.

---

## Registry API

```python
from autonomous_trading_platform.strategy.registry import get_registry

reg = get_registry()

# Lookup
reg.get_definition("momentum")            # -> StrategyDefinition
reg.strategy_exists("momentum")           # -> bool

# Listings
reg.list_definitions()                    # -> list[StrategyDefinition]
reg.list_strategy_types()                 # -> list[str]
reg.list_families()                       # -> list[StrategyFamily]
reg.list_debug_strategies()               # -> list[StrategyDefinition]
reg.list_production_strategies()          # -> list[StrategyDefinition]

# Filtered
reg.get_family_strategies(StrategyFamily.TREND)   # -> list[StrategyDefinition]
reg.get_generation_candidates()           # -> list[StrategyDefinition] (non-empty specs)
```

---

## Backward Compatibility

`strategy.catalog` is a thin shim that re-exports the original public API:

```python
# These all still work — they delegate to the registry
from autonomous_trading_platform.strategy.catalog import (
    StrategyCatalogEntry,      # alias for StrategyDefinition
    get_strategy_definition,
    list_strategy_types,
    list_production_strategy_types,
    list_debug_strategy_types,
    strategy_type_exists,
)
```

`research.config.strategy_parameter_validators` re-exports from `strategy.registry.validators` for backward compatibility.

---

## Factory & Config Integration

**StrategyFactory** uses the registry builder:

```python
def build(self, config: StrategyConfig) -> BaseStrategy:
    defn = get_registry().get_definition(config.type)
    return defn.builder(config.strategy_id, config.parameters)
```

**StrategyConfig** validates via the registry:

```python
# Type validation
registry.strategy_exists(v)

# Parameter validation
defn = registry.get_definition(self.type)
defn.parameter_validator(self.parameters)
```

---

## Relationship to Strategy Generation

The registry exposes generation-friendly metadata without containing generation logic:

- `parameter_specs` declares the search space declaratively
- `get_generation_candidates()` returns strategies with non-empty specs
- Generation engines (RandomSamplingGenerator, GridSearchGenerator, EvolutionaryGenerator) query the registry for parameter specs and ranges

---

## Distinction: Registry vs Execution

| Concern | Owner |
|---|---|
| Strategy metadata, defaults, validation | `strategy.registry` |
| Strategy instantiation | `strategy.factories.StrategyFactory` |
| Strategy evaluation | `strategy.services.StrategyEvaluationService` |
| Simulation orchestration | `research.simulation.SimulationRunner` |
| Artifact persistence | `research.artifacts` |

---

## Future Extension Points

The following are **not yet implemented** but the registry is designed to support them:

- **Composite strategies**: `family=COMPOSITE`, `required_persisted_features` populated
- **Ensemble orchestration**: new family `ENSEMBLE`, multi-strategy builder signature
- **Indicator registry**: `required_indicators` consumed by indicator dependency resolver
- **Feature dependency execution**: `required_persisted_features` consumed by data loader
- **Search-space generation engines**: consume `parameter_specs` from `get_generation_candidates()`
- **Dynamic plugin discovery**: call `registry.register()` before `lock()` from plugin entrypoints
- **Automated strategy assembly**: use `family`, `required_indicators`, and `parameter_specs` to compose strategies programmatically

---

## Adding a New Strategy

1. Implement the strategy class in `strategy/implementations/`.
2. Add validator function(s) to `strategy/registry/validators.py` and the `VALIDATORS` dict.
3. Add a builder function and `StrategyDefinition` registration to `strategy/registry/_registrations.py` **before** the `_REGISTRY.lock()` call.
4. Update `tests/strategy/test_strategy_registry.py` expected sets if needed.
5. Run `python -m pytest tests/strategy/` to verify.
