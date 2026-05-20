# Component Registry

## Purpose

`strategy.components` is the canonical registry for reusable strategy
construction primitives. It records metadata for the pieces future strategies
can assemble from without requiring one concrete Python strategy class for every
idea.

The registry is metadata-first. `CompositeRuleStrategy` now consumes executable
indicator, signal-rule, and aggregator definitions for local composition. The
registry still does not generate strategies automatically and existing
non-composite strategies continue to call their existing logic directly.

---

## Location

```
src/autonomous_trading_platform/strategy/components/
  __init__.py                    # Public API; triggers built-in registration
  component_type.py              # ComponentType enum
  component_parameter_schemas.py # ComponentParameterSpec metadata
  component_definition.py        # ComponentDefinition dataclass
  component_registry.py          # ComponentRegistry class + singleton
  _registrations.py              # Built-in component registrations
```

---

## StrategyRegistry vs ComponentRegistry

| Concern | Owner |
|---|---|
| Whole strategy identity, defaults, schema, builder, warmup | `StrategyRegistry` |
| Reusable primitive metadata | `ComponentRegistry` |
| Current strategy instantiation | `StrategyFactory` through `StrategyRegistry.builder` |
| Rule composition | `CompositeRuleStrategy` consuming `ComponentRegistry` |
| Future search/generation | Generators consuming both registries |

`StrategyDefinition.required_indicators` now validates against
`ComponentRegistry` indicator components during strategy registration. This is
only dependency validation; current strategies still call existing indicator
functions and signal classes directly.

---

## Component Types

`ComponentType` currently includes:

- `indicator`
- `signal_rule`
- `filter`
- `aggregator`
- `exit_rule`
- `sizing`

Indicators, signal rules, and aggregators are registered against existing code.
Filters, exits, and sizing components are represented as metadata-only
placeholders until real implementations exist. Composite strategies can use the
registered filter metadata with lightweight local comparison filters; exits and
sizing remain metadata-only.

---

## Registered Components

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

Executable signal rules: `threshold`, `crossover`, `comparison`.

Executable aggregators: `voting`, `weighted_score`, `logical_and`,
`logical_or`.

Metadata-only placeholders:

- filters: `volatility_filter`, `liquidity_filter`, `regime_filter`,
  `time_filter`
- exits: `fixed_exit_rule`, `trailing_exit_rule`, `signal_based_exit_rule`
- sizing: `fixed_sizing`, `volatility_adjusted_sizing`,
  `confidence_weighted_sizing`

Placeholders are registered with `metadata_only=True`, `is_executable=False`,
`production_ready=False`, and no implementation reference.

---

## Metadata

Each `ComponentDefinition` includes identity, implementation reference,
execution classification, required inputs, parameter specs, warmup hints,
output type/domain, compatibility fields, and production/debug/experimental
flags.

Metadata is intentionally lightweight. It is enough to validate references,
audit construction pieces, and feed future generation without pretending an
execution engine exists.

---

## Registry API

```python
from autonomous_trading_platform.strategy.components import (
    ComponentType,
    get_component_registry,
)

registry = get_component_registry()

registry.list_components()
registry.list_component_names()
registry.list_components_by_type(ComponentType.INDICATOR)
registry.get_component_definition("momentum")
registry.component_exists("momentum")
registry.list_executable_components()
registry.list_metadata_only_components()
registry.list_generation_candidates()
registry.validate_component_reference("momentum", expected_type=ComponentType.INDICATOR)
registry.list_compatible_components("momentum")
registry.get_required_inputs("momentum")
registry.get_component_parameter_specs("momentum")
```

Registration is deterministic and order-preserving. The built-in registry locks
after registration; duplicate registrations and post-lock registrations fail.

---

## Composite Consumption

`CompositeRuleStrategy` uses component metadata to validate declarative
indicator, signal-rule, filter, and aggregator references before execution.
Indicator warmup is derived from `warmup_formula`, `warmup_parameter`, or
`warmup_bars` metadata. Rule input mappings are validated against deterministic
indicator IDs in the composite config.

Future strategy generation can use component parameter specs, input/output
metadata, compatibility fields, and production/experimental flags to select
valid construction candidates. By default, generation candidates exclude
metadata-only placeholders.

Out of scope for the current registry: automatic strategy generation,
evolutionary mutation, and persisted feature dependency resolution.
