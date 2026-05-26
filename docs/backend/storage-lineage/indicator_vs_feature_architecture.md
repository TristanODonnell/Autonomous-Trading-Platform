# Indicator vs Feature Architecture

## Purpose

The platform has two calculation layers that intentionally overlap in names and
some formulas:

- `strategy/indicators/`: lightweight in-memory calculations used directly by
  strategy implementations.
- `feature_engineering/`: persisted reusable feature datasets with schemas,
  dataset versions, storage paths, and lineage metadata.

This document defines the boundary for TASK-1.3. It does not deprecate strategy
indicators and does not require every strategy to consume persisted features.

---

## Strategy Indicators

`strategy/indicators/` owns calculations that are:

- in-memory and local to strategy evaluation
- deterministic for a given input series
- fast enough to compute from the current `StrategyContext.bars`
- not persisted
- not dataset-versioned
- not lineage-tracked
- suitable for simple runtime or research calculations

Indicators are plain functions. They are allowed to duplicate feature formulas
when the strategy needs a simple local calculation and the duplicate is covered
by equivalence tests or documented as intentionally different.

Current strategy indicators include:

- trend: `simple_moving_average`, `exponential_moving_average`
- momentum: `momentum`, `rate_of_change`, `rsi`, `rsi_wilder`
- volatility: `rolling_standard_deviation`, `realized_volatility`
- volume: `average_volume`, `volume_ratio`, `volume_spike`
- mean reversion: `distance_from_moving_average`, `z_score`

---

## Persisted Features

`feature_engineering/` owns calculations that are:

- persisted as reusable datasets
- reusable across strategies, runtime services, research jobs, and health checks
- schema-backed
- dataset-versioned
- lineage-tracked through dataset/version metadata
- suitable for expensive, shared, cross-strategy, or cross-platform features
- required when experiment/runtime reproducibility depends on reusing the exact
  same precomputed feature values

Current persisted feature services include:

- moving average features
- returns features
- volatility features computed from returns
- liquidity features such as rolling average volume and bid/ask spread
- simple regime labels from short/long moving averages

Persisted features are not required for every simple strategy. A strategy that
can deterministically compute a cheap indicator from the local bar window should
continue to use strategy indicators until a concrete reuse, cost, or
reproducibility requirement justifies persisted feature consumption.

---

## Strategy Registry Dependency Metadata

`StrategyDefinition.required_indicators` declares the strategy-level indicator
functions needed by a strategy. These names are validated against indicator
components in `ComponentRegistry`. This field is still dependency metadata, not
an execution path. It supports:

- auditability
- warmup/lookback review
- future indicator dependency resolution
- future automated strategy assembly

`StrategyDefinition.required_persisted_features` declares persisted feature
datasets a strategy directly consumes through `StrategyContext.features`. This
field is also declaration metadata today. It must remain empty unless the
strategy implementation actually reads persisted feature tables.

Current strategy declarations:

| Strategy | Required Indicators | Required Persisted Features |
|---|---|---|
| `stub` | none | none |
| `intentional_loser` | none | none |
| `random` | none | none |
| `moving_average_crossover` | `simple_moving_average` | none |
| `momentum` | `momentum` | none |
| `mean_reversion` | `z_score`, `simple_moving_average`, `rolling_standard_deviation` | none |
| `factor_based` | `momentum`, `z_score`, `rolling_standard_deviation`, `volume_ratio` | none |

No current registered strategy consumes `StrategyContext.features`, so no
current strategy declares persisted feature requirements.

---

## Simulation Feature Flow (TASK-2.1 — automatic resolution)

As of TASK-2.1, the simulation feature path is automatic when
`FeatureDependencyResolverService` is wired into `SimulationRunner`:

1. `FeatureDependencyResolverService.resolve()` reads `required_persisted_features`
   from the `StrategyDefinition`, queries the SoR for a validated
   `FeatureDatasetVersion` for each feature, and returns
   `SimulationFeatureDatasetRequest` objects.
2. `SimulationRunner` passes those requests to
   `SimulationWindowLoader.load_window(feature_datasets=...)`.
3. Loaded feature tables are stored in
   `SimulationWindowData.feature_tables_by_symbol`.
4. `StrategyContextBuilder.build_from_window()` exposes the current symbol's
   tables through `StrategyContext.features`.
5. Strategies may read `context.features[feature_name]` as a PyArrow table.

Strategies that declare no `required_persisted_features` are unaffected —
no feature requests are generated and no feature loading occurs.

See `docs/architecture/feature_dependency_resolution.md` for the full flow.

---

## Overlap Audit

| Calculation | Indicator | Persisted Feature | Classification | Notes |
|---|---|---|---|---|
| Simple moving average | `simple_moving_average` | `MovingAverageFeatureService` | duplicated and equivalence-tested | Same rolling mean definition for a shared window. |
| Exponential moving average | `exponential_moving_average` | none | indicator-only | No persisted EMA feature today. |
| Absolute momentum | `momentum` | none | indicator-only | Strategy value is absolute price delta, not percent return. |
| Percent return / rate of change | `rate_of_change` | `ReturnsFeatureService` | duplicated and equivalence-tested | Matches `pct_change(n)` for the same lookback. |
| Volatility on returns | `realized_volatility` | `VolatilityFeatureService` | duplicated and equivalence-tested | Same sample standard deviation when both operate on return series. |
| Volatility on prices | `rolling_standard_deviation` | none directly | duplicated but intentionally different | Factor strategy applies rolling std to closes; persisted volatility applies rolling std to returns. |
| Average volume | `average_volume` | `LiquidityFeatureService` | duplicated and equivalence-tested | Same rolling average volume definition. |
| Volume ratio | `volume_ratio` | derivable from liquidity feature | duplicated and equivalence-tested | Persisted dataset stores average volume; ratio is current volume divided by that average. |
| Z-score | `z_score` | none | indicator-only | No persisted z-score feature today. |
| Distance from moving average | `distance_from_moving_average` | none | indicator-only | Could be derived from moving-average feature later. |
| Regime label | none | `RegimeFeatureService` | feature-only | Persisted cross-system feature; no strategy indicator equivalent today. |
| RSI | `rsi`, `rsi_wilder` | none | indicator-only | No persisted RSI feature today. |

---

## Rules

- Strategy indicators are not deprecated.
- Duplicated calculations are allowed while they are equivalence-tested or
  explicitly documented as intentionally different.
- Persisted feature dependencies must not be declared unless the strategy reads
  `StrategyContext.features`.
- Simple strategies should not be forced onto persisted feature datasets.
- Feature dependencies are declarations for now, not automatic execution.
- ComponentRegistry validates strategy indicator names and describes reusable
  primitives, but it does not execute indicators.

---

## Deferred Follow-Ups

- Exact symbol-set lineage validation (currently only symbol count is checked).
- Universe-version enforcement (FeatureDatasetVersion does not persist universe_version_id).
- Computation-parameter specificity in feature resolution.
- Persisted feature consumption by built-in strategies (none currently declare requirements).
- Indicator execution resolver for computing `required_indicators`.
