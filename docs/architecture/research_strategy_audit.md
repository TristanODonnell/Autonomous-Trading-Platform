# Research and Strategy Architecture Audit

Date: 2026-05-15

Scope note: the repository does not have top-level `strategy/` or `research/` folders. The audited code lives under `src/autonomous_trading_platform/strategy/` and `src/autonomous_trading_platform/research/`, with related research/backtest/replay paths in `src/autonomous_trading_platform/scheduler/`, `src/autonomous_trading_platform/cli/commands/`, `src/autonomous_trading_platform/storage/`, and `tests/`.

## 1. Folder Map

### Strategy

`src/autonomous_trading_platform/strategy/`

- `configs/strategy_config.py` - Pydantic strategy config with `type`, `strategy_id`, free-form `parameters`, canonical JSON, and config hash.
- `contracts/strategy_context.py` - Internal per-symbol strategy input: run, symbol, timestamps, bars, features, position, state.
- `contracts/strategy_evaluation_result.py` - Output from strategy evaluation: strategy id, bar timestamp, emitted signals.
- `contracts/strategy_bar_readiness_result.py` - Readiness result for runtime strategy evaluation.
- `contexts/strategy_context_builder.py` - Builds `StrategyContext` from Parquet for runtime or from preloaded `SimulationWindowData` for research simulation.
- `contexts/build_strategy_runtime_context.py` - Wires runtime strategy dependencies: universe reader, bar reader, strategy evaluation, readiness, signal writer, checkpoint writer, run manifest service.
- `contexts/strategy_runtime_context.py` - Dataclass bundle of runtime strategy services.
- `factories/strategy_factory.py` - Hard-coded strategy selector for all supported strategy types.
- `implementations/base_strategy.py` - Abstract `BaseStrategy` contract: `strategy_id` and `evaluate_symbol(context) -> Signal | None`.
- `implementations/base_strategy_helpers.py` - Shared close/volume extraction and deterministic signal id helper.
- `implementations/stub_strategy.py` - Simple threshold-on-last-close placeholder strategy, with its own duplicated signal id helper.
- `implementations/intentional_loser_strategy.py` - Debug strategy that intentionally flips simple price-move direction.
- `implementations/random_debug_strategy.py` - Seeded random BUY/SELL/None baseline.
- `implementations/moving_average_crossover_strategy.py` - SMA crossover strategy using `trend.simple_moving_average` and `CrossoverRule`.
- `implementations/momentum_strategy.py` - Momentum threshold strategy using `momentum.momentum` and `ThresholdRule`.
- `implementations/mean_reversion_strategy.py` - Z-score threshold strategy using `mean_reversion.z_score`; signal logic is inline, not through `ThresholdRule`.
- `implementations/factor_based_strategy.py` - Weighted factor score strategy using momentum, mean reversion, volatility, and volume indicators; scoring thresholds are hard-coded inside the class.
- `indicators/trend.py` - SMA and EMA.
- `indicators/momentum.py` - Momentum, rate of change, Cutler RSI, Wilder RSI.
- `indicators/mean_reversion.py` - Distance from moving average and z-score.
- `indicators/volatility.py` - Rolling standard deviation and realized volatility alias.
- `indicators/volume.py` - Average volume, volume ratio, volume spike.
- `signal_logic/base_signal_rule.py` - `SignalRuleResult` and abstract rule interface.
- `signal_logic/threshold_rule.py` - Generic threshold-to-signal rule.
- `signal_logic/crossover_rule.py` - Fast/slow crossover rule.
- `signal_logic/comparison_rule.py` - Generic comparison rule.
- `signal_logic/aggregation.py` - Logical, voting, and weighted aggregators.
- `services/strategy_evaluation_service.py` - Iterates universe symbols, builds contexts, calls a strategy, returns signals.
- `services/strategy_bar_readiness_service.py` - Determines next ready bar from ingestion and last evaluated checkpoint.
- `services/strategy_checkpoint_writer_service.py` - Writes last evaluated strategy runtime checkpoint.
- `jobs/evaluate_strategy_job.py` - Runtime job wrapper: readiness, evaluation, signal persistence, checkpoint, run manifest.

### Research

`src/autonomous_trading_platform/research/`

- `services/research_dataset_resolver_service.py` - Resolves raw vs adjusted bar Parquet dataset roots for a requested dataset version.
- `simulation/simulation_runner.py` - Main research run orchestrator: seed setup, dataset resolution, metadata creation, window loading, strategy construction, execution, artifact recording, metrics, run completion/failure.
- `simulation/contexts/build_simulation_context.py` - Wires research simulation dependencies. Contains hard-coded defaults for universe size, capital, cost model, slippage, base path, and lookback.
- `simulation/contexts/simulation_context.py` - Dataclass bundle for research services.
- `simulation/services/simulation_window_loader_service.py` - Bounded Parquet window loader with optional warmup bars and feature table loading.
- `simulation/services/simulation_execution_engine.py` - Bar-by-bar simulation engine: context building, signal evaluation, target sizing, order intent creation, fill simulation, cash/position updates, DataFrame artifact rows.
- `simulation/services/simulated_execution_service.py` - Fill model for market and limit orders with cost/slippage application.
- `simulation/services/simulation_cost_model_service.py` - Commission plus slippage cost calculations.
- `simulation/services/simple_position_sizer.py` - Research-only equal-capital whole-share target sizer.
- `simulation/services/lookahead_guard_service.py` - Guards strategy contexts and timelines against lookahead.
- `simulation/services/result_recorder_service.py` - Writes trade logs, equity curve, per-bar metrics, positions, and signal logs through the Parquet simulation repository.
- `simulation/models/fill_model.py` - Fill policy enum/config. `NEXT_OPEN` is declared but not implemented.
- `simulation/models/slippage_model.py` - Simple fixed-rate slippage model.
- `experiments/models/experiment_plan.py` - Dataclass experiment definition for AB, sweep, time segmentation, rolling window, and cross-universe experiments.
- `experiments/services/experiment_orchestration_service.py` - Creates experiments, expands strategy configs/windows, runs simulations, filters/ranks outputs, and marks experiment status.
- `experiments/filtering/` - Filter config, filter checks, scoring, metric calculators, and `FilterScoreService`.
- `pipeline/pipeline_runner.py` - Staged research pipeline runner passing survivors between stages.
- `pipeline/stages/base_stage.py` - Base staged pipeline contract and `StageResult`.
- `pipeline/stages/simulation_stage.py` - Single simulation pass per survivor plus filtering.
- `pipeline/stages/walk_forward_stage.py` - Train/test fold simulation and filtering.
- `pipeline/stages/monte_carlo_stage.py` - Repeated seeded simulation runs and pass-rate aggregation.
- `pipeline/stages/stage_registry.py` - Registry for loading stage objects from YAML.
- `pipeline/aggregation/monte_carlo_aggregator.py` - Aggregates Monte Carlo metric distributions.
- `pipeline/aggregation/regime_aggregator.py` - Regime aggregation support.
- `strategy_generation/` - Grid, random, and evolutionary strategy config generation.

### Related Entrypoints and Backtest/Replay

- `src/autonomous_trading_platform/cli/main.py` - Registers CLI domains, including `research`, `strategy`, `backtesting`, and `runtime`.
- `src/autonomous_trading_platform/cli/commands/research.py` - `research run-simulation`, `research run-experiment`, and `research generate-strategies`.
- `src/autonomous_trading_platform/cli/commands/strategy.py` - `strategy evaluate-bar` and `strategy inspect-readiness`.
- `src/autonomous_trading_platform/cli/commands/backtesting.py` - Large backtesting/governance fixture and verification command surface.
- `src/autonomous_trading_platform/scheduler/cycles/run_experiment_pipeline_cycle.py` - Scheduler/runtime job wrapper around experiment orchestration.
- `src/autonomous_trading_platform/scheduler/orchestration/historical_research_golden_path_orchestrator.py` - Historical path: backfill, corporate actions, features, optional experiment pipeline.
- `src/autonomous_trading_platform/scheduler/backtest/backtest_replay_orchestrator.py` - Legacy/simple SOR replay with inline MA crossover and synthetic fills.
- `src/autonomous_trading_platform/scheduler/backtest/backtest_trading_cycle_orchestrator.py` - Full-fidelity historical backtest path that reuses market backfill, feature pipeline, trading evaluation, simulated fills, snapshots, and risk.
- `src/autonomous_trading_platform/runtime/replay_debug.py` and `src/autonomous_trading_platform/runtime/services/replay_runtime_service.py` - Runtime replay/debug flows tested under `tests/runtime/`.

## 2. Strategy Layer Review

### Strategy Selection and Registration

Strategies are selected through hard-coded branches in `src/autonomous_trading_platform/strategy/factories/strategy_factory.py`. The CLI duplicates the supported type list in `src/autonomous_trading_platform/cli/commands/research.py` as `STRATEGY_TYPE_CHOICES`, while `src/autonomous_trading_platform/strategy/configs/strategy_config.py` has a third source of truth through a `Literal[...]` type. This is workable for the current small set, but it is already duplicated and should become a single registry before adding many strategies.

Current registered/constructible types:

- `stub`
- `intentional_loser`
- `random`
- `moving_average_crossover`
- `momentum`
- `mean_reversion`
- `factor_based`

### Parameter Handling

`StrategyConfig.parameters` is a free-form `dict[str, Any]` in `src/autonomous_trading_platform/strategy/configs/strategy_config.py`. `StrategyFactory.build()` casts values inline and supplies defaults. Individual strategy constructors validate only some invariants:

- Good: `MovingAverageCrossoverStrategy` validates positive windows and short < long.
- Good: `MeanReversionStrategy` validates window and z thresholds.
- Good: `FactorBasedStrategy` validates positive windows and score threshold ordering.
- Partial: `MomentumStrategy` does not validate lookback or threshold ordering in its constructor; the indicator raises if lookback <= 0.
- Partial: `RandomStrategy` validates probabilities but does not retain/report the actual seed value in params, only `random_seeded=True`.

There is no per-strategy parameter schema, no central validation, and no generated config documentation. This is a high-friction point for roadmap expansion.

### Indicator Calculation and Reuse

The indicator modules are small, deterministic, and reusable. Existing strategy usage:

- `MovingAverageCrossoverStrategy` uses `simple_moving_average`.
- `MomentumStrategy` uses `momentum`.
- `MeanReversionStrategy` uses `z_score`.
- `FactorBasedStrategy` uses `momentum`, `z_score`, `rolling_standard_deviation`, and `volume_ratio`.

The feature pipeline also has moving average, returns, volatility, liquidity, and regime services under `src/autonomous_trading_platform/feature_engineering/`, which means research strategy indicators and persisted feature generation are conceptually related but implemented separately. That is acceptable while strategies use raw bars only, but it becomes duplication once strategies depend on persisted features.

There is no indicator registry or feature dependency declaration. The simulation window loader can load feature tables, and `StrategyContext.features` exists, but current strategies do not declare or consume feature requirements.

### Signal Logic Organization

Reusable signal logic exists in `src/autonomous_trading_platform/strategy/signal_logic/`. It is used unevenly:

- `MovingAverageCrossoverStrategy` uses `CrossoverRule`.
- `MomentumStrategy` uses `ThresholdRule`.
- `MeanReversionStrategy` implements threshold logic inline.
- `FactorBasedStrategy` implements scoring and threshold logic inline instead of using `WeightedScoreAggregator`.
- `StubStrategy`, `IntentionalLoserStrategy`, and `RandomStrategy` build signals directly.

This is not yet a structural failure, but the split is inconsistent. `signal_logic` currently looks like a promising utility layer rather than the actual standard for strategy construction.

### Production-Ready vs Prototype/Legacy

Production-leaning:

- `BaseStrategy`, `StrategyContext`, `StrategyEvaluationService`, `EvaluateStrategyJob`, and `LookaheadGuardService` establish real contracts and runtime flow.
- `MovingAverageCrossoverStrategy`, `MomentumStrategy`, and `MeanReversionStrategy` are deterministic and simple enough to validate.
- Signal IDs are deterministic for most strategies through `base_strategy_helpers.build_signal_id`.

Prototype/debug/legacy:

- `StubStrategy`, `IntentionalLoserStrategy`, and `RandomStrategy` are explicitly debug or baseline strategies.
- `FactorBasedStrategy` is useful but overly specific: hard-coded factor scoring, no external schema, no feature dependency declaration.
- `BacktestReplayOrchestrator` implements inline MA logic instead of using `strategy/` and should be treated as legacy/demo SOR replay.
- Debug `print()` calls remain in `StrategyContextBuilder.build_from_window()` and `SimulationExecutionEngine._evaluate_signals()`, which is not production-ready for batch research runs.

## 3. Full Experiment Funnel

### 1. Experiment Request / CLI Command

Modules:

- `src/autonomous_trading_platform/cli/commands/research.py`
- `src/autonomous_trading_platform/scheduler/cycles/run_experiment_pipeline_cycle.py`
- `src/autonomous_trading_platform/scheduler/orchestration/historical_research_golden_path_orchestrator.py`

Inputs:

- CLI flags or YAML config.
- Optional direct `ExperimentDefinition`.

Outputs:

- `SimulationRunResult` summaries, filter outputs, staged pipeline summaries, DB runtime job records, run manifests.

Assessment:

- Well-structured for manual runs and scheduler wrapping.
- CLI has direct path vs orchestrated path, but comments say direct `run-simulation` has no DB writes; actual `SimulationRunner` still receives repositories from `build_simulation_context()` and records experiment/run/strategy metadata. The comment is misleading.
- Validation is mostly ad hoc.

Location:

- Keep in CLI/scheduler. Shared parsing/plan validation should move into research services.

### 2. Config Parsing

Modules:

- `research.py` `_load_experiment_from_yaml()`
- `research/experiments/models/experiment_plan.py`
- `research/pipeline/stages/stage_registry.py`

Inputs:

- JSON CLI strings, YAML experiment config.

Outputs:

- `ExperimentDefinition`, optional `StagedPipelineConfig`.

Assessment:

- YAML parsing supports staged pipeline config cleanly through `StageRegistry`.
- Missing schema validation and helpful error reporting.
- Defaults `start_date`/`end_date` to `date.today()` when omitted in YAML, which is risky for reproducibility.

Location:

- Move YAML/config parsing into a research config loader with Pydantic schemas.

### 3. Dataset/Version Selection

Modules:

- `research/services/research_dataset_resolver_service.py`
- `storage/parquet/datasets.py`
- `storage/parquet/paths.py`

Inputs:

- `dataset_version`, `price_basis`.

Outputs:

- `ResolvedResearchDataset` with dataset object, root path, schema metadata.

Assessment:

- Clear and focused.
- Only validates local Parquet path existence; does not verify SOR dataset version status, date coverage, symbol coverage, or lineage.

Location:

- Keep in research for now, later share with runtime if paper/live replay need the same version resolver.

### 4. Universe/Symbol Selection

Modules:

- CLI `_parse_symbols()`
- `ExperimentDefinition.symbols`
- `ExperimentDefinition.universe_version`
- `ExperimentOrchestrationService._cross_universe_windows()`
- Runtime path uses `UniverseVersionRepository` through `build_strategy_runtime_context.py`.

Inputs:

- Explicit symbols in CLI/YAML/plan.

Outputs:

- Symbol lists for simulations.

Assessment:

- Research mostly bypasses actual universe membership and treats symbols as explicit input.
- `universe_version` is recorded but not used to resolve symbols in research simulation.
- `SimulationRunner._record_run_started()` hard-codes `universe_version="v1"` in `SimulationRun`.

Location:

- Symbol lists can stay in research, but universe-version resolution should become a shared abstraction.

### 5. Feature Loading

Modules:

- `SimulationWindowLoader.load_window()`
- `SimulationFeatureDatasetRequest`
- `StrategyContext.features`

Inputs:

- Optional feature dataset requests.

Outputs:

- `feature_tables_by_symbol`.

Assessment:

- Infrastructure exists but is not wired through `SimulationRunRequest`, `ExperimentDefinition`, strategy declarations, or current strategies.
- Current strategy contexts built from windows ignore `feature_tables_by_symbol`.

Location:

- Keep loader in research, but define a strategy feature dependency contract before expanding factor strategies.

### 6. Strategy Construction

Modules:

- `StrategyConfig`
- `StrategyFactory`
- `ExperimentOrchestrationService._expand_strategy_configs()`
- `strategy_generation/*`

Inputs:

- Strategy config dicts and optional parameter space.

Outputs:

- Concrete `BaseStrategy` instances.

Assessment:

- Functional for the current set.
- Central factory, CLI choices, and config literal are duplicated.
- Parameter generation does not validate strategy-specific legal ranges beyond constructor failures at run time.

Location:

- Strategy construction belongs in `strategy/`; generation belongs in research.

### 7. Signal Generation

Modules:

- `SimulationExecutionEngine._evaluate_signals()`
- `StrategyContextBuilder.build_from_window()`
- strategy implementations
- `LookaheadGuardService`

Inputs:

- Window bars up to but not including the current timestamp.

Outputs:

- List of `Signal`.

Assessment:

- Lookahead discipline is explicit and tested.
- `StrategyContextBuilder` requires fixed `lookback_bars=300` in `build_simulation_context.py`; this can suppress strategies with shorter lookbacks when less than 300 historical bars exist.
- Warmup bars are loaded from strategy parameters, but only by checking common parameter names (`long_window`, `window`, `lookback`). This misses `momentum_lookback`, `mean_reversion_window`, and multi-indicator requirements.

Location:

- Signal generation contract should stay shared across research/runtime.

### 8. Order Generation

Modules:

- `SimulationExecutionEngine._construct_orders()`
- `SimplePositionSizer`
- runtime/paper flow separately uses `PortfolioConstructionService`.

Inputs:

- Signals, current positions, close prices.

Outputs:

- `OrderIntent` list.

Assessment:

- Research order generation is intentionally simple and deterministic.
- It bypasses `OrderSimulatorService`, despite that service wrapping `PortfolioConstructionService`.
- This creates divergence from runtime portfolio construction semantics.

Location:

- Keep simple sizing available for research, but introduce a shared portfolio construction adapter when comparing paper/live compatibility.

### 9. Fill Simulation

Modules:

- `SimulationExecutionEngine._simulate_fills()`
- `SimulatedExecutionService`
- `SimulationCostModelService`
- `SlippageModel`

Inputs:

- Order intents and current bars.

Outputs:

- `Fill` contracts.

Assessment:

- Clear and tested for market/limit basics.
- Current market fill policy is close-only. `NEXT_OPEN` exists but raises `NotImplementedError`.
- Fill IDs use `uuid4()`, which means artifact rows are not byte-for-byte deterministic even with fixed seeds.

Location:

- Research simulation, later shared with backtest/paper replay.

### 10. Portfolio/Account Updates

Modules:

- `SimulationExecutionEngine._apply_fills()`
- `CashLedgerService`
- `PositionLedgerService`

Inputs:

- Fills, current cash, positions, prices.

Outputs:

- Updated cash, positions, realized PnL.

Assessment:

- Good reuse of execution ledger services.
- No margin, reserved cash, buying-power, partial fill, rejected order, or multi-strategy capital bucket modeling.

Location:

- Shared ledger logic is correctly outside research; simulation orchestration can stay in research.

### 11. Metrics Calculation

Modules:

- `research/experiments/filtering/metrics/*.py`
- `SimulationRunner.run()`
- `SimulationExecutionEngine._record_metrics()`

Inputs:

- Equity curve and trade logs.

Outputs:

- Return, risk, trade, stability metric dataclasses; per-bar metrics DataFrame.

Assessment:

- Good separation of post-run metrics functions.
- Per-bar metrics are placeholders in places (`bar_return=0.0`, equity row `drawdown=0.0`, trade row `slippage=0.0`).
- Warmup rows are stripped before summary metrics.

Location:

- Metrics should stay in research for now; shared dashboard metrics can use separate application services.

### 12. Result Recording

Modules:

- `ResultRecorderService`
- `ParquetSimulationRepository`
- `SimulationRunsRepository`
- `MetricsSummaryRepository`
- `ExperimentsRepository`
- `RunManifestRepository`

Inputs:

- Simulation artifacts and metadata.

Outputs:

- Parquet artifact datasets, SOR simulation run rows, metrics snapshots, run manifests, experiment rows.

Assessment:

- Good intent: Parquet for artifacts, SOR for metadata.
- Artifact manifest only stores summary counts, not exact Parquet paths/checksums.
- No explicit dataset lineage from feature versions or universe version.
- Result recording is per `experiment_id` + `strategy_id`; repeated runs for the same strategy in Monte Carlo/walk-forward can collide unless repository partitioning includes run/window role internally.

Location:

- Keep split, but strengthen run-scoped artifact identity.

### 13. Artifact Persistence

Modules:

- `storage/parquet/repositories/parquet_simulation_repository.py`
- `storage/parquet/writer.py`
- `storage/parquet/schemas.py`

Inputs:

- DataFrames from execution engine.

Outputs:

- Trade logs, equity curve, per-bar metrics, positions, signal log.

Assessment:

- Good repository abstraction.
- Need stronger artifact manifest, schema/version checks in the run record, and collision tests for repeated strategy runs.

Location:

- Storage stays under `storage/`; research should only call repository/service interfaces.

### 14. Final CLI/Report Output

Modules:

- `cli/commands/research.py`
- `cli/formatters.py`

Inputs:

- `SimulationRunResult`, filter outputs, pipeline result.

Outputs:

- JSON summaries.

Assessment:

- Useful summaries for manual use.
- Does not print artifact paths, metrics summaries, run manifest id, or filter thresholds used.

Location:

- CLI output stays in CLI, with richer summary payloads supplied by research services.

## 4. Strategy Architecture Gaps

| Severity | Gap | Evidence | Recommendation |
| --- | --- | --- | --- |
| HIGH | Strategy registry/type source of truth is duplicated. | `strategy_factory.py`, `strategy_config.py`, and `cli/commands/research.py` all list strategy types. | Create a registry that stores type, class, parameter schema, defaults, and CLI choices. |
| HIGH | No strategy-specific parameter schemas. | `StrategyConfig.parameters` is free-form and factory casts inline. | Add Pydantic parameter models per strategy and validate before runs. |
| HIGH | No feature dependency contract. | `StrategyContext.features` and `SimulationWindowLoader.feature_tables_by_symbol` exist, but strategies do not declare or consume required features. | Add `required_bars`, `required_features`, and `warmup_bars` metadata to strategy definitions. |
| HIGH | Warmup/lookback inference is fragile. | `SimulationRunner` only checks `long_window`, `window`, or `lookback`; context builder always requires 300 bars. | Derive warmup and context lookback from strategy metadata. |
| MEDIUM | Signal logic utilities are inconsistently used. | `MeanReversionStrategy` and `FactorBasedStrategy` implement inline threshold/scoring logic. | Standardize rule composition or intentionally keep simple strategies direct and remove unused abstractions. |
| MEDIUM | Indicator registry is missing. | Indicators are plain functions under `strategy/indicators`. | Add registry only when strategies/features need dynamic dependency resolution. |
| MEDIUM | Indicator and feature calculations may diverge. | Strategy indicators duplicate concepts from `feature_engineering/services/*`. | Decide whether strategy indicators are in-memory research helpers or the canonical feature math; test equivalence for overlapping features. |
| MEDIUM | Multi-strategy runtime/research contracts are weak. | Simulation runner runs one strategy per run; experiment orchestrator loops externally. | Add portfolio-level experiment abstraction for combined strategies/capital allocation later. |
| LOW | Debug strategies live beside production candidates. | `stub`, `random`, `intentional_loser` are in `implementations/`. | Keep for now, but mark in registry as `debug=True` and exclude from production selection. |
| LOW | `StubStrategy` duplicates signal id generation. | It has a private `_build_signal_id()` instead of using `base_strategy_helpers.build_signal_id()`. | Clean up when refactoring strategy helpers. |

## 5. Research Architecture Gaps

| Severity | Gap | Evidence | Recommendation |
| --- | --- | --- | --- |
| CRITICAL | Backtest/replay semantics are split across three paths. | New research simulation, `BacktestReplayOrchestrator`, and `BacktestTradingCycleOrchestrator` each have different signal/order/fill semantics. | Declare one canonical research simulation path; label the others as legacy/demo or integration replay. |
| HIGH | Result artifact identity may collide for repeated runs. | `ResultRecorderService` writes by `experiment_id` and `strategy_id`; Monte Carlo and walk-forward repeat the same strategy id. | Partition/write artifacts by `run_id`, `stage_name`, and `window_role`. |
| HIGH | Dataset/universe lineage is incomplete. | Research records dataset version, but uses hard-coded universe version `v1` and does not record feature versions. | Store dataset SOR id/status, universe version, feature dataset versions, and artifact checksums in manifest. |
| HIGH | Simulation determinism is partial. | Seeds are set, but `uuid4()` is used for run/fill ids and artifact writes may include non-deterministic IDs. | Define deterministic vs operational run identity; use deterministic child IDs or record reproducibility boundaries. |
| HIGH | Configuration validation is ad hoc. | CLI/YAML parsing creates dataclasses directly; date defaults can use `date.today()`. | Add Pydantic experiment config schemas and reject incomplete YAML. |
| HIGH | Research order construction diverges from runtime portfolio construction. | `SimulationExecutionEngine._construct_orders()` bypasses `OrderSimulatorService` and `PortfolioConstructionService`. | Either formalize a research-only execution policy or adapt shared portfolio construction. |
| MEDIUM | Metrics contain placeholders. | `bar_return`, equity `drawdown`, and trade `slippage` are recorded as `0.0`. | Fill these fields or remove them until computed. |
| MEDIUM | Fill model is incomplete. | `MarketFillPolicy.NEXT_OPEN` exists but raises. | Implement next-open or remove from accepted config until supported. |
| MEDIUM | Feature loading is not integrated into experiments. | Feature loader exists but no experiment/strategy contract wires it. | Add feature dependency resolution after parameter schema work. |
| MEDIUM | CLI output omits artifact paths and metric details. | Research CLI prints run counts and IDs only. | Include artifact root/path, summary metrics, filter thresholds, and manifest IDs. |
| LOW | Debug output uses `print()`. | `StrategyContextBuilder.build_from_window()` and `_evaluate_signals()` print every context. | Replace with structured debug logging before large runs. |

## 6. Test Coverage Map

Current relevant tests:

- Indicators: no direct tests found for `strategy/indicators/*`.
- Signal logic: no direct tests found for `strategy/signal_logic/*`.
- Strategy implementations: `tests/strategy/test_stub_strategy.py` covers only `StubStrategy`.
- Strategy evaluation: `tests/strategy/test_strategy_evaluation_service.py`, `tests/strategy/test_strategy_evaluation_job.py`, `tests/strategy/test_strategy_bar_readiness_service.py`.
- Strategy-to-portfolio flow: `tests/strategy/test_strategy_to_portfolio_flow.py`.
- Simulation lookahead/context: `tests/research/simulation/test_lookahead_guard.py`.
- Simulation determinism metadata/seed setup: `tests/research/simulation/test_determinism_seed.py`.
- Fill/slippage/cost model: `tests/research/simulation/test_simulated_execution_service.py`, `test_simulator_fill.py`, `test_simulation_cost_model_service.py`, `test_simulator_slippage.py`.
- Simulation engine full loop: no direct focused tests found for `SimulationExecutionEngine.execute()`.
- Simulation runner full run: no full integration-style test found for `SimulationRunner.run()` with fake window/recorder/engine.
- Result recording: no direct tests found for `ResultRecorderService` or `ParquetSimulationRepository` in this area.
- Experiment orchestration: covered indirectly through `tests/scheduler/test_experiment_pipeline_cycle.py` with fake orchestration; no direct tests for `ExperimentOrchestrationService`.
- Staged pipeline: scheduler dispatch tests cover staged vs non-staged routing; no direct tests found for `SimulationStage`, `WalkForwardStage`, `MonteCarloStage`, or `StageRegistry`.
- Historical research golden path: `tests/scheduler/test_historical_research_golden_path.py`.
- Replay/backtest flows: runtime replay has `tests/runtime/test_replay_runtime_service.py`, `tests/runtime/test_runtime_replay_debug.py`, and broader `tests/runtime/test_risk_parameter_wiring.py`; no focused tests found for `scheduler/backtest/backtest_replay_orchestrator.py` or `backtest_trading_cycle_orchestrator.py`.
- CLI research commands: no direct parser/handler tests found for `cli/commands/research.py`.
- Backtesting CLI governance audit: `tests/cli/commands/test_backtesting_governance_audit.py`, but this is governance-focused, not simulation correctness.

Missing tests to add next:

1. Indicator golden-value tests for SMA, EMA, momentum, ROC, both RSI variants, z-score, volatility, and volume ratio.
2. Signal rule tests for threshold, crossover, comparison, voting, logical, and weighted aggregation.
3. Strategy implementation tests for moving average crossover, momentum, mean reversion, factor based, random determinism, and intentional loser.
4. `StrategyFactory` tests for all supported types, default parameters, invalid types, and invalid parameter values.
5. `SimulationExecutionEngine.execute()` golden-path test with two symbols, deterministic bars, signals, fills, equity, positions, and no lookahead.
6. `SimulationRunner.run()` integration test with fake dataset resolver/window loader/execution engine/recorder to verify metadata, metrics, recording, success, and failure.
7. `ResultRecorderService` and `ParquetSimulationRepository` tests that prove repeated `run_id`/stage/window artifacts do not overwrite each other.
8. Direct `ExperimentOrchestrationService` tests for AB, sweep, time segmentation, rolling window, cross-universe, and failure status.
9. Direct staged pipeline tests for simulation, walk-forward, Monte Carlo, and YAML stage loading.
10. Research CLI parser/handler tests for required flags, YAML loading, staged pipeline output, invalid JSON, and artifact summary output.

## 7. Roadmap Recommendation

### Fix Now

1. Make research simulation artifact identity run-scoped. Include `run_id`, `stage_name`, and `window_role` in Parquet writes and manifest artifacts.
2. Add a single strategy registry and remove duplicated type lists from factory/config/CLI.
3. Add per-strategy parameter schemas and validate experiment configs before running.
4. Replace fragile warmup/lookback inference with strategy-declared requirements.
5. Remove or gate debug `print()` calls in simulation/context building.

### Build Next

1. Define the strategy feature dependency contract and wire feature dataset versions into `SimulationRunRequest`.
2. Add direct tests for indicators, signal rules, all strategy implementations, simulation engine, orchestration, and result recording.
3. Decide canonical backtest semantics: research simulation vs full trading-cycle replay vs legacy dashboard replay.
4. Improve metrics artifacts: compute per-bar returns, drawdown, and realized slippage, or stop writing placeholder values.
5. Extend CLI outputs to include metrics, artifact paths, run manifest IDs, and filter thresholds.

### Defer

1. Full multi-strategy portfolio experiments with shared capital allocation.
2. Live/paper/research universal execution abstraction beyond the current shared ledger services.
3. Advanced fill models, partial fills, queue modeling, and next-open fill policy.
4. Dynamic indicator registry, unless feature dependency resolution needs it.
5. Evolutionary strategy generation hardening, until base experiment reproducibility is stronger.

## 8. Final Summary

Top 5 strengths:

1. Strategy evaluation has a clear `BaseStrategy` and `StrategyContext` contract.
2. Lookahead protection is explicit and tested.
3. Research simulation is decomposed into resolver, loader, runner, execution engine, recorder, and metrics.
4. Experiment orchestration already supports sweeps, segmentation, rolling windows, cross-universe, and staged pipelines.
5. Metadata persistence exists across experiments, simulation runs, metrics summaries, and run manifests.

Top 5 risks:

1. Research/backtest/replay semantics are split and can produce incompatible answers.
2. Strategy config, CLI choices, and factory registration are duplicated.
3. Parameter, feature, and warmup requirements are implicit.
4. Repeated experiment artifacts may not be uniquely run-scoped.
5. Test coverage is thin for actual strategy math, signal logic, full simulation execution, and result recording.

Top 5 recommended next tasks:

1. Create a strategy registry with parameter schemas and strategy metadata.
2. Make simulation artifacts uniquely keyed by `run_id` plus stage/window metadata.
3. Add strategy-declared lookback/warmup/feature requirements and use them in `SimulationRunner` and `StrategyContextBuilder`.
4. Write golden tests for indicators, signal rules, strategy implementations, and the simulation engine loop.
5. Mark `BacktestReplayOrchestrator` as legacy/demo and align future roadmap around either research simulation or full trading-cycle replay as the canonical backtest path.

Current folder structure is acceptable for now. `strategy/` is reasonably placed as shared domain logic, and `research/` is a reasonable home for simulation, experiment orchestration, filtering, and staged pipelines.

Later reorganization should focus on boundaries, not churn:

- Keep reusable strategy contracts, indicators, signal rules, and strategy implementations under `strategy/`.
- Keep experiment planning, generation, filtering, staged pipelines, and research-only simulation under `research/`.
- Move shared backtest/replay execution contracts into a common execution/simulation abstraction only after the canonical semantics are chosen.
- Keep storage repositories under `storage/`, but require research runs to emit a complete artifact manifest with paths, schema versions, checksums, lineage, and run IDs.
