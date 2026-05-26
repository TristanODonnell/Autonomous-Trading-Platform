# Feature Dependency Resolution

## Overview

TASK-2.1 wired the existing feature-dataset infrastructure into the simulation
pipeline so strategies that declare persisted feature dependencies receive those
features automatically without manual caller configuration.

## Resolution Flow

```
SimulationRunRequest
        │
        ▼
SimulationRunner.run()
        │
        ├─ resolve_bars_dataset() ─────────────────────── bar dataset path
        │
        ├─ FeatureDependencyResolverService.resolve()
        │       │
        │       ├─ StrategyRegistry.get_definition(strategy_type)
        │       │       → required_persisted_features: tuple[str, ...]
        │       │       → compute_warmup_bars(parameters) → int
        │       │
        │       └─ for each feature_name:
        │               ├─ FEATURE_DATASETS_BY_NAME[feature_name] → ParquetDataset
        │               └─ FeatureDatasetVersionsRepository.find_for_simulation(
        │                       feature_name, source_dataset_version,
        │                       price_basis, start_date, end_date, min_symbol_count
        │                  ) → FeatureDatasetVersions row
        │
        ├─ _record_run_started(resolved_feature_dataset_ids=...)
        │
        └─ SimulationWindowLoader.load_window(feature_datasets=...)
                │
                └─ for each symbol × each feature_request:
                        feature_reader.read(...) → pa.Table
                        feature_tables_by_symbol[symbol][feature_name] = table
                                │
                                ▼
                        SimulationWindowData.feature_tables_by_symbol
                                │
                                ▼
                        StrategyContextBuilder.build_from_window()
                                │
                                ▼
                        StrategyContext.features[feature_name] = pa.Table
```

## Components

### FeatureDependencyResolverService

Location: `research/simulation/services/feature_dependency_resolver_service.py`

Inputs: strategy_type, strategy_parameters, simulation_dataset_version, price_basis, symbols, start_date, end_date.

Outputs: `ResolvedFeatureDependencies` containing:
- `feature_requests: list[SimulationFeatureDatasetRequest]`
- `resolved_feature_dataset_ids: dict[str, str]`
- `warmup_bars: int`

The resolver is optional in `SimulationRunner.__init__`. When `None`, the runner
still uses registry-derived warmup but generates no feature requests.

### FEATURE_DATASETS_BY_NAME

Defined in `storage/parquet/repositories/parquet_feature_repository.py`.

Maps feature names to `ParquetDataset` objects:

| Feature name     | ParquetDataset               |
|------------------|------------------------------|
| `returns`        | `FEATURE_RETURNS_DATASET`    |
| `volatility`     | `FEATURE_VOLATILITY_DATASET` |
| `moving_average` | `FEATURE_MOVING_AVERAGE_DATASET` |
| `liquidity`      | `FEATURE_LIQUIDITY_DATASET`  |
| `regime`         | `FEATURE_REGIME_DATASET`     |

Do not add entries for indicator-computed features (EMA, RSI, z-score, volume_ratio).

### FeatureDatasetVersionsRepository.find_for_simulation

Added in TASK-2.1. Queries for a validated feature dataset matching:
- `feature_name` and `source_dataset_version`
- `underlying_price_basis`
- `date_coverage_start <= start_date AND date_coverage_end >= end_date`
- `symbol_coverage >= min_symbol_count`

Returns the most-recent matching row ordered by `created_at desc`.

Unlike `find_matching_dataset`, this method omits `computation_parameters` so
strategies that declare a feature by name (not by specific computation
parameters) can resolve any validated variant of that feature.

## Lineage Validation

Validation is enforced at resolution time, before `load_window` is called.

| Check                                | Enforcement |
|--------------------------------------|-------------|
| Feature dataset must be validated    | `find_for_simulation` SQL filter |
| source_dataset_version must match    | `find_for_simulation` SQL filter |
| price_basis must match               | `find_for_simulation` SQL filter |
| Date coverage must span simulation   | `find_for_simulation` SQL filter |
| Symbol count must be sufficient      | `find_for_simulation` Python loop |
| Feature name must have dataset mapping | Checked before repository query |

Failures raise `FeatureDependencyError` with a clear, actionable message.

## Metadata Persistence

`resolved_feature_dataset_ids` is written to:
- `SimulationRun.execution_config["resolved_feature_dataset_ids"]`
- `RunManifest.schema_definition["feature_dependencies"]["resolved_feature_dataset_ids"]`

These provide lineage traceability from a simulation run back to its feature
dataset versions.

## Warmup Resolution

`SimulationRunner` now uses `StrategyDefinition.compute_warmup_bars(parameters)`
instead of the former parameter-name heuristic (`long_window * 78`).

The registry warmup functions return bars directly. The loader's
`_BARS_PER_TRADING_DAY` constant is still used internally to convert bar counts
to calendar days for the pre-fetch window, but is no longer used as a heuristic
multiplier in the runner.

| Strategy            | Old heuristic (long_window×78) | Registry warmup |
|---------------------|-------------------------------|-----------------|
| stub                | 0                             | 1               |
| random              | 0                             | 0               |
| moving_avg_crossover| long_window × 78              | long_window + 1 |
| momentum            | lookback × 78                 | lookback + 1    |
| mean_reversion      | window × 78                   | window          |
| factor_based        | max_window × 78               | max(windows)    |

## Known Limitations and Deferred Items

- Exact symbol-set lineage validation is not implemented; only symbol count is checked.
- Universe-version enforcement is deferred; `FeatureDatasetVersion` does not
  currently persist `universe_version_id`.
- Computation-parameter specificity is deferred; the resolver finds any validated
  dataset variant for the feature, not a specific window-size variant.
- Built-in strategies do not currently declare `required_persisted_features`. The
  resolver path is proven via synthetic test registrations and is ready for any
  strategy that adds feature declarations.


## TASK-2.2 Addition: regime_classification Dataset

The `regime_classification` feature (TASK-2.2) integrates cleanly into this
resolution flow without any resolver changes:

- **Registration**: `"regime_classification"` was added to `FEATURE_DATASETS_BY_NAME`
  in `storage/parquet/repositories/parquet_feature_repository.py`.
- **Strategy declaration**: `required_persisted_features=("regime_classification",)`
  triggers standard resolution.
- **Validation**: `FeatureDependencyResolverService.resolve()` selects a validated
  `regime_classification` dataset version matching `source_dataset_version`,
  `price_basis`, date coverage, and symbol count — same rules as all other features.
- **StrategyContext access**: `context.features["regime_classification"]` returns a
  `pa.Table` with columns `regime_trend`, `regime_volatility`, `regime_liquidity`,
  `regime_mean_reversion`, `regime_risk`, and explainability metadata.
