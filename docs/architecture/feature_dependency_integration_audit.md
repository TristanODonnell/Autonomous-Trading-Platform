# Feature Dependency Integration Audit

## Purpose

This audit captures the current feature-engineering, feature-dataset,
strategy-dependency, and simulation-loading architecture before TASK-2.1.

The current platform already has persisted feature datasets and an explicit
simulation feature-loading path. What is not yet implemented is automatic
resolution from a strategy configuration to validated feature dataset requests.

## Current Persisted Features

Persisted feature support exists under `src/autonomous_trading_platform/feature_engineering/`.

| Feature | Status | Service | Parquet dataset | Columns |
|---|---|---|---|---|
| `returns` | implemented | `ReturnsFeatureService` | `FEATURE_RETURNS_DATASET` | `ret_1d`, `ret_5d`, `ret_20d` |
| `volatility` | implemented | `VolatilityFeatureService` | `FEATURE_VOLATILITY_DATASET` | `volatility_value` |
| `moving_average` | implemented | `MovingAverageFeatureService` | `FEATURE_MOVING_AVERAGE_DATASET` | `moving_average_value` |
| `liquidity` | implemented | `LiquidityFeatureService` | `FEATURE_LIQUIDITY_DATASET` | `avg_volume_value`, `bid_ask_spread` |
| `regime` | implemented | `RegimeFeatureService` | `FEATURE_REGIME_DATASET` | `regime` |

No persisted EMA, RSI, z-score, distance-from-MA, or volume-ratio dataset exists
today. Some of those remain intentionally indicator-only; they should not be
added for TASK-2.1 unless a strategy actually consumes them through
`StrategyContext.features`.

## How Feature Datasets Are Written

Feature jobs use this path:

1. `FeatureDatasetResolverService.resolve_source_bars()` resolves and loads a
   validated source bar dataset.
2. A feature job computes a dataframe through the feature service.
3. `FeaturePipelineGuardService` checks for a matching validated feature dataset
   using feature name, source dataset version, computation parameters, requested
   date coverage, and symbol count.
4. `FeatureDatasetWriterService.write_feature_dataset()` writes Parquet through
   `ParquetFeatureRepository.write_feature_frame()`.
5. The writer registers a `FeatureDatasetVersion` in the SoR repository.
6. The job marks the feature dataset `validated`.

Dataset metadata:

| Field | Current behavior |
|---|---|
| Dataset version ID | `generate_dataset_version(feature_name)`, e.g. feature-name plus timestamp/hash suffix. |
| Feature name | One of `returns`, `volatility`, `moving_average`, `liquidity`, `regime`. |
| Dataset name | `f"{feature_name}_features"` in `FeatureDatasetWriterService`. |
| Source dataset linkage | `source_dataset_version` and `source_manifest.source_dataset_version_id`. |
| Price basis | `underlying_price_basis` in SoR and `price_basis` in Parquet rows. |
| Symbol coverage | Count of unique symbols in the written frame. |
| Date coverage | `start_date` and `end_date` passed to the feature job. |
| Storage path metadata | `feature_datasets/{feature_name}/dataset_version=.../`. This does not match the actual Parquet root convention. |
| Actual Parquet layout | `data/features/{feature_name}/dataset_version={id}/symbol=.../year=.../month=.../*.parquet`. |
| Parquet schema metadata | Attached by `build_feature_metadata()` with dataset key, version, feature name, underlying dataset version, and underlying price basis. |
| Checksums | Contract field exists, but current feature writer passes an empty Parquet metadata checksum and stores `checksum=None` in SoR. |

Potential hardening issue: `FeatureDatasetWriterService.storage_path` uses a
logical `feature_datasets/...` path while `ParquetFeatureRepository` writes to
`features/...`. TASK-2.1 does not need to redesign this, but feature lookup code
should use `ParquetDataset` plus `dataset_version_id`, not this metadata string.

## Lineage Validation

Already implemented:

- `run_feature_pipeline_cycle._validate_feature_pipeline_lineage()` prevents
  explicit RAW runs from using non-`raw_bars` source datasets.
- The same guard prevents explicit ADJUSTED runs from using non-`adjusted_bars`
  datasets, requires adjusted price basis, and requires adjusted datasets to
  link back to a raw source dataset.
- `FeatureDatasetResolverService.resolve_source_bars()` validates explicit
  source dataset existence, price-basis match, and `validated` status.
- `FeatureDatasetVersionsRepository` can query latest or matching validated
  feature datasets by feature name, source dataset version, computation
  parameters, coverage dates, price basis, and source dataset version.

Partially implemented:

- Feature dataset rows track `source_dataset_version`, `underlying_price_basis`,
  `symbol_coverage`, `date_coverage_start`, and `date_coverage_end`.
- `find_matching_dataset()` validates coverage only by symbol count, not exact
  requested symbol set.
- Feature datasets do not store `universe_version_id`; `run_feature_pipeline_cycle`
  records universe information in run manifests and job metadata, but not in
  `FeatureDatasetVersion`.
- Simulation dataset resolution only validates bar dataset path and price-basis
  selection. It does not consult SoR lineage for feature datasets.

Missing for TASK-2.1:

- A simulation-side lineage validator for loaded feature datasets.
- Validation that each feature dataset's `source_dataset_version` equals the
  simulation bar dataset version.
- Validation that each feature dataset's `underlying_price_basis` equals the
  simulation request price basis.
- Validation that `date_coverage_start <= simulation_start_date` and
  `date_coverage_end >= simulation_end_date`.
- Exact symbol-set validation, unless the repository model is extended later to
  persist symbols. For TASK-2.1, the practical minimum is strict loader behavior
  plus symbol-count coverage.
- Universe-version validation. The feature dataset model does not currently
  persist universe version, so this should be deferred or recorded as a known
  limitation unless the model is extended in a separate task.

## Simulation Feature Loading

The explicit feature-loading path already exists:

1. Callers may create `SimulationFeatureDatasetRequest(feature_name, dataset,
   dataset_version)`.
2. `SimulationWindowLoader.load_window(..., feature_datasets=...)` reads each
   requested feature dataset with `feature_reader`.
3. Loaded feature tables are placed in
   `SimulationWindowData.feature_tables_by_symbol[symbol][feature_name]`.
4. `StrategyContextBuilder.build_from_window()` copies the current symbol's
   feature table dict into `StrategyContext.features`.
5. `StrategyContext.features` defaults to `{}` when no features are loaded.

Current limitation: `SimulationRunner.run()` always calls `load_window()` without
`feature_datasets`. The path is therefore manual and testable, but not connected
to strategy dependencies.

## Strategy Dependency Declarations

`StrategyDefinition` already includes:

- `required_indicators`
- `required_persisted_features`
- `warmup_bars_fn`
- `compute_warmup_bars()`

Current built-in strategy state:

| Strategy | Required indicators | Required persisted features |
|---|---|---|
| `stub` | none | none |
| `intentional_loser` | none | none |
| `random` | none | none |
| `moving_average_crossover` | `simple_moving_average` | none |
| `momentum` | `momentum` | none |
| `mean_reversion` | `z_score`, `simple_moving_average`, `rolling_standard_deviation` | none |
| `factor_based` | `momentum`, `z_score`, `rolling_standard_deviation`, `volume_ratio` | none |
| `composite_rule` | `momentum` | none |

`ComponentRegistry` also has indicator metadata including required bar fields,
warmup parameters, warmup formulas, supported price bases, and executable
implementations. It does not currently define persisted feature dependencies or
perform simulation feature resolution.

Warmup is partially wired. Registry definitions can compute precise strategy
warmup bars, but `SimulationRunner` currently derives warmup from selected
parameter names (`long_window`, `window`, `lookback`) and multiplies by 78 bars
per trading day. TASK-2.1 should use registry warmup metadata instead of this
heuristic.

## Current State Classification

Already implemented:

- Persisted feature services and jobs for returns, volatility, moving averages,
  liquidity, and regime.
- Versioned feature dataset SoR model and repository.
- Feature Parquet schemas and feature-specific Parquet layout.
- Feature pipeline cycle with selectable feature jobs.
- Basic RAW/ADJUSTED feature pipeline lineage guard.
- Manual `SimulationFeatureDatasetRequest` loading.
- `StrategyContext.features` contract and simulation context builder propagation.
- Strategy registry fields for required indicators, required persisted features,
  and warmup bars.
- Tests proving feature tables can reach `StrategyContext.features`.

Partially implemented:

- Feature dataset lookup exists but is not exposed as a simulation dependency
  resolver.
- Coverage validation checks date range and symbol count, not exact symbols.
- Source dataset and price-basis metadata are stored, but simulation does not
  validate feature lineage before loading.
- Warmup metadata exists in the registry, but simulation uses parameter-name
  heuristics.
- Component metadata describes indicators, but not persisted feature mappings.

Missing:

- Strategy config to `StrategyDefinition` dependency resolution in simulation.
- Translation from `required_persisted_features` to
  `SimulationFeatureDatasetRequest`.
- Feature dataset lookup by required feature, simulation dataset version, price
  basis, symbols, date range, and computation parameters.
- Simulation-time feature lineage validation before `load_window()`.
- Passing resolved feature requests into `SimulationWindowLoader.load_window()`.
- Recording resolved feature dataset versions in simulation run metadata and run
  manifests.
- Registry-driven warmup aggregation for simulation.
- Tests for automatic feature dependency resolution.

Should defer:

- Migrating existing indicator-driven strategies to persisted features.
- Adding new persisted feature jobs for EMA, RSI, z-score, or volume ratio.
- Redesigning Parquet layout or feature dataset SoR schema.
- Adding universe-version enforcement to feature dataset lookup unless a
  separate schema change stores `universe_version_id` on feature dataset rows.
- Exact symbol-set lineage validation unless symbol lists become first-class SoR
  metadata.

## TASK-2.1 Implementation Plan

1. Add a small simulation feature dependency resolver.
   - Input: strategy type/config, simulation bar dataset version, price basis,
     symbols, start/end dates.
   - Resolve the `StrategyDefinition` through `StrategyRegistry`.
   - Normalize parameters through the definition.
   - Read `required_persisted_features`.
   - Query `FeatureDatasetVersionsRepository` for matching validated datasets.
   - Validate source dataset version, price basis, date coverage, and minimum
     symbol coverage.
   - Convert matches to `SimulationFeatureDatasetRequest` objects.

2. Add an explicit feature-name to Parquet dataset mapping for simulation.
   - Reuse `FEATURE_DATASETS_BY_NAME` or move it to a neutral shared module if
     importing from `parquet_feature_repository` would create awkward coupling.
   - Keep the mapping limited to existing persisted features.

3. Wire the resolver into `SimulationRunner.run()`.
   - Build the strategy config and resolve `StrategyDefinition` before
     `load_window()`.
   - Pass resolved `feature_datasets` to `SimulationWindowLoader.load_window()`.
   - Leave behavior unchanged when `required_persisted_features == ()`.
   - Persist the resolved feature dataset IDs in `SimulationRun.execution_config`
     and `RunManifest.schema_definition` or artifact metadata.

4. Replace simulation warmup heuristics with registry warmup.
   - Use `defn.compute_warmup_bars(normalized_parameters)`.
   - Preserve the existing 78-bars-per-day loader behavior only as loader
     mechanics, not as dependency inference.
   - If multiple dependency sources are later introduced, aggregate using max.

5. Add tests before changing strategy declarations.
   - Use a synthetic test strategy definition or local registry fixture that
     declares a persisted feature.
   - Do not migrate built-in strategies as part of TASK-2.1.

## Recommended Files To Modify For TASK-2.1

Primary implementation:

- `src/autonomous_trading_platform/research/simulation/services/feature_dependency_resolver_service.py`
- `src/autonomous_trading_platform/research/simulation/simulation_runner.py`
- `src/autonomous_trading_platform/research/simulation/contexts/build_simulation_context.py`

Likely supporting changes:

- `src/autonomous_trading_platform/storage/parquet/repositories/parquet_feature_repository.py`
- `src/autonomous_trading_platform/storage/parquet/datasets.py`
- `src/autonomous_trading_platform/storage/sor/repositories/core/feature_dataset_versions_repository.py`
- `src/autonomous_trading_platform/strategy/registry/strategy_registry.py`
- `src/autonomous_trading_platform/strategy/registry/strategy_definition.py`

Only if needed:

- `src/autonomous_trading_platform/contracts/runtime/simulation_run.py`
- `src/autonomous_trading_platform/contracts/runtime/run_manifest.py`
- `src/autonomous_trading_platform/research/simulation/services/simulation_window_loader_service.py`

Files not recommended for TASK-2.1:

- Existing feature jobs, unless a test exposes a feature-name/schema mapping bug.
- Existing strategy implementations.
- Feature dataset SoR schema migrations.

## Existing Test Coverage

Relevant existing tests:

- `tests/scheduler/test_feature_pipeline_cycle.py`
- `tests/utilities/feature_pipeline_cycle_fixture.py`
- `tests/strategy/test_indicator_feature_equivalence.py`
- `tests/research/simulation/test_strategy_feature_context_contract.py`
- `tests/research/simulation/test_simulation_execution_engine_golden_path.py`
- `tests/research/simulation/test_lookahead_guard.py`
- `tests/strategy/test_strategy_dependency_metadata.py`
- `tests/strategy/test_strategy_definition.py`
- `tests/strategy/components/test_component_registry.py`
- `tests/strategy/components/test_component_strategy_registry_integration.py`

What they cover:

- Feature pipeline cycle can produce/register feature datasets in seeded
  scenarios.
- Indicator/feature formula equivalence for overlapping calculations.
- Manual feature tables are propagated into `StrategyContext.features`.
- Strategy registry metadata and warmup functions exist and are internally
  consistent.
- Component registry validates indicator metadata.

## Recommended Tests To Add

Add tests for TASK-2.1:

- `tests/research/simulation/test_feature_dependency_resolver_service.py`
  - resolves no requests when strategy declares no persisted features.
  - resolves a validated matching feature dataset into
    `SimulationFeatureDatasetRequest`.
  - rejects missing feature datasets.
  - rejects wrong source dataset version.
  - rejects wrong price basis.
  - rejects insufficient date coverage.
  - rejects insufficient symbol coverage.

- `tests/research/simulation/test_simulation_runner_feature_dependencies.py`
  - runner passes resolved feature requests into `SimulationWindowLoader`.
  - runner records resolved feature dataset IDs in simulation metadata.
  - no behavior change for current built-in strategies with no persisted
    features.

- `tests/research/simulation/test_simulation_warmup_resolution.py`
  - runner uses `StrategyDefinition.compute_warmup_bars()` rather than
    parameter-name heuristics.
  - warmup remains zero for strategies whose definition returns zero.

- Extend `tests/research/simulation/test_strategy_feature_context_contract.py`
  - loaded feature table remains available on `StrategyContext.features` through
    the automatic resolver path.

- Extend `tests/strategy/test_strategy_dependency_metadata.py`
  - any strategy declaring persisted features must have names present in the
    supported persisted feature mapping.
  - strategies with empty persisted dependencies continue to pass.

## Conclusion

TASK-2.1 should harden and wire the existing pieces rather than introduce new
feature jobs. The narrow missing layer is a resolver that translates strategy
dependency metadata into validated `SimulationFeatureDatasetRequest` objects and
passes those objects into the existing simulation window loader. The main
correctness risks are lineage validation, coverage validation, and replacing
simulation warmup heuristics with registry-derived warmup.
