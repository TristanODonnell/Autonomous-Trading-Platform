# Audit: src/autonomous_trading_platform/strategy/

## Verified counts

- File count: `find src/autonomous_trading_platform/strategy -name '*.py' | wc -l` -> **62**
- LOC: `find src/autonomous_trading_platform/strategy -name '*.py' -exec cat {} + | wc -l` -> **5364**
- Concrete `BaseStrategy` subclasses: `grep -rn "class .*(BaseStrategy)" src/autonomous_trading_platform --include='*.py'` -> **8**:
  1. `MomentumStrategy` (implementations/momentum_strategy.py)
  2. `MeanReversionStrategy` (implementations/mean_reversion_strategy.py)
  3. `MovingAverageCrossoverStrategy` (implementations/moving_average_crossover_strategy.py)
  4. `FactorBasedStrategy` (implementations/factor_based_strategy.py)
  5. `StubStrategy` (implementations/stub_strategy.py)
  6. `RandomStrategy` (implementations/random_debug_strategy.py) — debug baseline
  7. `IntentionalLoserStrategy` (implementations/intentional_loser_strategy.py) — debug/validation
  8. `CompositeRuleStrategy` (composite/composite_rule_strategy.py) — DSL-driven
- TODO/FIXME/XXX: `grep -rn "TODO\|FIXME\|XXX" src/autonomous_trading_platform/strategy --include='*.py' | wc -l` -> **0**

**MACD claim check:** There is NO MACD strategy or MACD indicator anywhere in `strategy/`. `grep -rni macd src/autonomous_trading_platform --include='*.py'` matches only `application/services/platform_replay/initial_state_hooks.py:305`, which maps the alias `"macd_crossover"` -> `"moving_average_crossover"`. Any writeup claiming a "MACD strategy" is inaccurate; the closest real thing is `MovingAverageCrossoverStrategy` (SMA fast/slow crossover, not MACD line/signal-line). `exponential_moving_average` exists in `indicators/trend.py` but no MACD is built from it.

## Per-file entries

### src/autonomous_trading_platform/strategy/__init__.py (1 line)
- Purpose: Empty package marker (single blank line).

### src/autonomous_trading_platform/strategy/catalog.py (49 lines)
- Purpose: Backward-compatible shim re-exporting the strategy registry helpers under legacy names (`StrategyCatalogEntry = StrategyDefinition`, `list_strategy_types`, etc.).
- Notable: Clean deprecation-by-aliasing pattern; docstring explicitly steers new code to `strategy.registry`.

### src/autonomous_trading_platform/strategy/implementations/base_strategy.py (39 lines)
- Purpose: `BaseStrategy` ABC — the contract for all strategies: abstract `strategy_id` property + `evaluate_symbol(context: StrategyContext) -> Signal | None`.
- Notable: Minimal, well-documented interface; docstring encodes the key invariants (bar-close-only data, determinism, one Signal or None per symbol evaluation). Single-method interface keeps strategies pure functions of context — good design.

### src/autonomous_trading_platform/strategy/implementations/base_strategy_helpers.py (54 lines)
- Purpose: Shared helpers: deterministic `build_signal_id` via `uuid5(NAMESPACE_URL, seed)` keyed on run/strategy/symbol/bar/direction/params, plus duck-typed `get_close`/`extract_closes`/`get_volume`/`extract_volumes`.
- Notable: Deterministic UUIDv5 signal IDs are a genuine idempotency mechanism (same inputs -> same signal_id, enabling safe re-runs). Bar accessors accept both attribute and dict shapes — pragmatic but weakly typed (`Any`).

### src/autonomous_trading_platform/strategy/implementations/momentum_strategy.py (70 lines)
- Purpose: Momentum strategy: N-bar price difference via `indicators.momentum.momentum`, then `ThresholdRule` decides BUY/SELL.
- Notable: Real signal math (not a stub). Composes the reusable `ThresholdRule` primitive rather than inlining comparisons. Default `buy_above=0.0, sell_below=0.0` means any nonzero momentum fires — sensible only as defaults to be overridden.

### src/autonomous_trading_platform/strategy/implementations/mean_reversion_strategy.py (80 lines)
- Purpose: Z-score mean reversion: buy when z <= buy_below_z (default -2.0), sell when z >= sell_above_z (default +2.0); confidence scaled by |z/threshold| capped at 1.0.
- Notable: Constructor validates window > 0 and buy_below_z < sell_above_z. Full parameter provenance recorded in `Signal.params` (z_score, thresholds, reason). Genuine statistical logic; does not reuse ThresholdRule (inline branching) — minor inconsistency with sibling strategies.

### src/autonomous_trading_platform/strategy/implementations/moving_average_crossover_strategy.py (87 lines)
- Purpose: Classic SMA fast/slow crossover: computes previous+current fast/slow SMAs and delegates cross detection to `CrossoverRule`.
- Notable: Correct crossover semantics (checks previous relationship <= / >= vs current > / <, so it only fires on the crossing bar, not while merely above/below). Requires `long_window + 1` bars. This is the strategy aliased as "macd_crossover" in platform_replay — it is SMA crossover, not MACD.

### src/autonomous_trading_platform/strategy/implementations/factor_based_strategy.py (165 lines)
- Purpose: Multi-factor strategy combining momentum sign, z-score mean-reversion score, volume-ratio score, and a volatility penalty into a weighted score with buy/sell thresholds.
- Notable: The most elaborate hardcoded strategy: 4 indicators, per-factor weights (0.4/0.3/0.2/0.1 defaults), full factor decomposition dumped into `Signal.params` (excellent auditability). Scoring functions are crude step functions (momentum reduced to sign +-1; volatility always scores -0.25 when positive), so quantitative sophistication is modest but the structure is real.

### src/autonomous_trading_platform/strategy/implementations/stub_strategy.py (119 lines)
- Purpose: Placeholder strategy that BUYs/SELLs on 1-bar price change beyond a threshold; used to validate the strategy architecture.
- Notable: Predates the shared helpers: duplicates its own `_build_signal_id` and `_get_close` (its ID seed includes prices instead of params — divergent from `base_strategy_helpers.build_signal_id`). Honest naming; registered as debug-only in the registry.

### src/autonomous_trading_platform/strategy/implementations/random_debug_strategy.py (96 lines)
- Purpose: Seeded-random baseline strategy (BUY/SELL/None with configured probabilities) as a zero-edge benchmark for simulator validation.
- Notable: Deterministic via `random.Random(random_seed)`; probability params validated to [0,1]. Having a null-hypothesis baseline strategy is good research hygiene. Note: instance-level RNG means determinism depends on evaluation order across symbols.

### src/autonomous_trading_platform/strategy/implementations/intentional_loser_strategy.py (83 lines)
- Purpose: Debug strategy that deliberately inverts a naive momentum rule (sell after up-moves, buy after down-moves) to produce reliable losses in a long-only simulator — used to verify that PnL accounting/backtests can actually detect a losing strategy.
- Notable: Rare and thoughtful test artifact ("does my backtester show losses when it should?"). Comments mark the flipped directions explicitly.

### src/autonomous_trading_platform/strategy/implementations/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/indicators/momentum.py (133 lines)
- Purpose: Momentum indicators: `momentum` (price diff), `rate_of_change`, `rsi` (Cutler's SMA-based), `rsi_wilder` (Wilder's EMA smoothing, seeded SMA, requires 2*window+1 bars).
- Notable: Genuine, correct implementations. The `rsi` docstring is a standout: it explains Cutler vs Wilder path-dependence, the 1-3 point divergence in trends, and warns that literature thresholds (70/30) assume Wilder — evidence of real quant literacy, not copy-paste.

### src/autonomous_trading_platform/strategy/indicators/trend.py (28 lines)
- Purpose: `simple_moving_average` and `exponential_moving_average` (SMA-seeded, standard 2/(n+1) multiplier, iterative smoothing over full history).
- Notable: Correct textbook EMA. No MACD function despite EMA being available.

### src/autonomous_trading_platform/strategy/indicators/volatility.py (31 lines)
- Purpose: `rolling_standard_deviation` (sample stdev, n-1 denominator, window==1 -> 0.0) and `realized_volatility` (alias over returns).
- Notable: Uses sample (Bessel-corrected) variance — a deliberate statistical choice.

### src/autonomous_trading_platform/strategy/indicators/mean_reversion.py (30 lines)
- Purpose: `distance_from_moving_average` and `z_score` ((last - SMA)/rolling stdev), None-guarded incl. zero-stdev.
- Notable: Composes trend + volatility modules rather than duplicating math.

### src/autonomous_trading_platform/strategy/indicators/volume.py (40 lines)
- Purpose: `average_volume`, `volume_ratio` (last/avg with zero-guard), `volume_spike` boolean.
- Notable: Straightforward and correct.

### src/autonomous_trading_platform/strategy/indicators/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/signal_logic/base_signal_rule.py (23 lines)
- Purpose: DSL primitive base: frozen `SignalRuleResult` dataclass (direction/confidence/reason/params) + `BaseSignalRule` ABC with `evaluate()`.
- Notable: Every rule result carries a machine-readable `reason` and full params — consistent explainability discipline across the DSL.

### src/autonomous_trading_platform/strategy/signal_logic/threshold_rule.py (73 lines)
- Purpose: Reusable threshold primitive: buy_above/buy_below/sell_above/sell_below on a scalar, fixed confidence (default 0.55).
- Notable: Frozen dataclass; first-match-wins ordering (buy rules checked before sell) is implicit — overlapping thresholds silently prefer BUY.

### src/autonomous_trading_platform/strategy/signal_logic/comparison_rule.py (61 lines)
- Purpose: Reusable comparison primitive: BUY when left > right, SELL when left < right, each direction independently toggleable; named operands feed self-describing reasons/params.
- Notable: Clean; equality yields no signal.

### src/autonomous_trading_platform/strategy/signal_logic/crossover_rule.py (64 lines)
- Purpose: Reusable crossover primitive: detects fast/slow line crossings from previous+current values (default confidence 0.6).
- Notable: Correct edge-triggered semantics (requires prior <=/>= relationship, so no repeat-fire while above/below).

### src/autonomous_trading_platform/strategy/signal_logic/aggregation.py (134 lines)
- Purpose: Rule-combination layer: `LogicalAggregator` (AND/OR toward a target direction), `VotingAggregator` (min-votes majority), `WeightedScoreAggregator` (confidence-weighted score vs +-thresholds).
- Notable: Three genuinely different combination semantics; weighted aggregator validates positive weights and normalizes by total weight. `LogicalAggregator` AND requires every rule to match the target direction — sound.

### src/autonomous_trading_platform/strategy/signal_logic/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/components/__init__.py (18 lines)
- Purpose: Public re-export surface for the component DSL package: `ComponentDefinition`, `ComponentParameterSpec`/`ComponentParameterType`, `ComponentRegistry`/`get_component_registry`, `ComponentType`. Triggers `_registrations` import as a side effect.
- Notable: This is the "component registry" half of the claimed DSL — confirmed real.

### src/autonomous_trading_platform/strategy/components/component_type.py (14 lines)
- Purpose: `ComponentType` StrEnum taxonomy: INDICATOR, SIGNAL_RULE, FILTER, AGGREGATOR, EXIT_RULE, SIZING.
- Notable: FILTER/EXIT_RULE/SIZING types exist in the taxonomy but (per `_registrations.py`) are only ever registered as non-executable metadata placeholders — see below.

### src/autonomous_trading_platform/strategy/components/component_parameter_schemas.py (74 lines)
- Purpose: `ComponentParameterType` enum (INT/FLOAT/BOOL/STRING) + frozen `ComponentParameterSpec` dataclass + `int_parameter()`/`float_parameter()` factory helpers that set sensible defaults for `discrete`/`step`/`mutation_strategy`.
- Notable: Clean declarative parameter metadata designed for both validation and future search-space/optimizer use (`tunable`, `mutation_strategy` fields foreshadow a genetic/optimization layer that mostly isn't built yet in this package).

### src/autonomous_trading_platform/strategy/components/component_definition.py (87 lines)
- Purpose: Frozen `ComponentDefinition` dataclass — the canonical metadata record for one registered component (indicator/rule/filter/aggregator/etc.): implementation ref, required inputs/types, parameter specs, warmup metadata, compatibility, production-readiness flags.
- Notable: `__post_init__` enforces real invariants (metadata-only components can't be executable; executable components must have an implementation) and freezes/normalizes tuple and mapping fields via `object.__setattr__`. Well-engineered immutable metadata model.

### src/autonomous_trading_platform/strategy/components/component_registry.py (116 lines)
- Purpose: `ComponentRegistry` — order-preserving, lock-once registry of `ComponentDefinition`s with lookup/filter helpers (`list_components_by_type`, `list_compatible_components`, `list_generation_candidates`, `validate_component_reference`). Module-level singleton via `get_component_registry()`.
- Notable: Mirrors the `StrategyRegistry` design (same lock-after-register pattern) applied one level down at the primitive/component level. `list_compatible_components` uses `compatible_component_types` + `incompatible_components` metadata for DSL composability checks.

### src/autonomous_trading_platform/strategy/components/_registrations.py (472 lines)
- Purpose: Registers every concrete component into the canonical `ComponentRegistry`: 12 indicators (SMA, EMA, momentum, rate_of_change, Cutler RSI, Wilder RSI, z-score, distance-from-MA, rolling stdev, realized volatility, average volume, volume ratio, volume spike), 3 signal rules (threshold, crossover, comparison), 4 aggregators (voting, weighted_score, logical_and, logical_or), then locks the registry.
- Notable: **Confirms the DSL claim precisely** — indicators/rules/aggregators are real, executable, parameterized, with warmup formulas (`"window"`, `"lookback + 1"`, `"2 * window + 1"`) evaluated later via a restricted AST evaluator. However, FILTER/EXIT_RULE/SIZING component types (volatility/liquidity/regime/time filters; fixed/trailing/signal_based exits; fixed/volatility_adjusted/confidence_weighted sizing) are registered only as `metadata_only=True, is_executable=False, production_ready=False, experimental=True` placeholders with `implementation=None` — i.e. these are advertised in the taxonomy but not actually implemented. A writeup should not claim filters/exit-rules/position-sizing are part of the working DSL.

### src/autonomous_trading_platform/strategy/composite/__init__.py (31 lines)
- Purpose: Public re-export surface for the composite package (`CompositeRuleStrategy`, `build_composite_rule_strategy`, all `Composite*Config` pydantic models, execution-context/result types).

### src/autonomous_trading_platform/strategy/composite/component_evaluation_result.py (24 lines)
- Purpose: Frozen pydantic `ComponentEvaluationResult` — one explainability record per DSL component evaluated at runtime (id, name, type, passed/direction/confidence, reason, inputs, output, parameters).
- Notable: This is the backbone of the composite strategy's full per-bar audit trail (see `composite_rule_strategy.py`).

### src/autonomous_trading_platform/strategy/composite/component_execution_context.py (20 lines)
- Purpose: Pydantic `ComponentExecutionContext` carrying per-symbol, per-bar working state during composite evaluation: closes, volumes, returns, an `indicator_outputs` dict and an `indicator_cache` keyed by `(indicator_id, offset)`.
- Notable: The cache prevents recomputing the same indicator at the same offset when referenced by multiple rules.

### src/autonomous_trading_platform/strategy/composite/composite_strategy_config.py (358 lines)
- Purpose: Pydantic config schema for `CompositeRuleStrategy`: `CompositeInputReference` (indicator-output-or-literal, no-lookahead-guarded via `offset <= 0`), `CompositeIndicatorConfig`, `CompositeRuleConfig` (weighted entry/confirmation rules), `CompositeFilterConfig`, `CompositeAggregatorConfig`, `CompositeConfidenceConfig` (floor/cap), and the top-level `CompositeStrategyConfig` with a `model_validator` that cross-checks every component reference against the live `ComponentRegistry` (type match, executability, required inputs present, unknown indicator ids rejected, parameter min/max/type-checked). Also computes `warmup_bars()` by walking indicator/rule warmup formulas and reference offsets, using a tiny hand-rolled AST evaluator (`_evaluate_warmup_formula`/`_eval_ast_node`) restricted to `Constant`/`Name`/`+-*/` `BinOp` nodes.
- Notable: This is a genuinely rigorous declarative-strategy schema — `all extra="forbid"`, exhaustive parameter-type/range validation, and a warmup formula evaluator scoped to a tiny safe AST subset (not `eval()`). One of the strongest-engineered files in the whole `strategy/` package.

### src/autonomous_trading_platform/strategy/composite/composite_rule_strategy.py (458 lines)
- Purpose: `CompositeRuleStrategy(BaseStrategy)` — executes a `CompositeStrategyConfig` at runtime: builds an execution context from bars, evaluates indicators (with caching), evaluates filters (blocking), evaluates entry/confirmation signal rules (threshold/crossover/comparison), aggregates them (voting/weighted_score/logical_and/logical_or), scores final confidence (aggregation vs weighted mode, floor/cap clamp), and emits a `Signal` — or blocks at any stage (warmup/indicators/filters/aggregation) while recording a complete `explainability` dict (`last_explainability` property) describing exactly why.
- Notable: This is the concrete class instantiating the DSL — confirms "composite strategy built from declarative primitives" is real, not aspirational. The explainability trace (every indicator value, filter pass/fail, rule direction/confidence/reason, aggregation result, and final confidence composition) is unusually thorough for a portfolio project — arguably the standout design in this whole directory. Minor smell: `_evaluate_rule` special-cases the three known signal-rule component names via if/elif/raise rather than dispatching through `ComponentDefinition.implementation`, so adding a new signal-rule component to the registry alone is not sufficient — this method must also be updated (registry and executor are not fully decoupled).

### src/autonomous_trading_platform/strategy/composite/composite_strategy_builder.py (20 lines)
- Purpose: One-line factory `build_composite_rule_strategy(strategy_id, parameters) -> CompositeRuleStrategy`, validating parameters through `CompositeStrategyConfig.model_validate` first.

### src/autonomous_trading_platform/strategy/configs/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/configs/strategy_config.py (41 lines)
- Purpose: Top-level `StrategyConfig` pydantic model (`type`, `strategy_id`, `parameters`) used to instantiate any registered strategy; validates `type` against the live `StrategyRegistry`, normalizes `parameters` through the registry's schema, and provides a deterministic `config_hash()` (SHA-256 of sorted-key canonical JSON) for reproducibility tracking.
- Notable: `config_hash()` is a nice touch for experiment/run reproducibility (same config always hashes identically).

### src/autonomous_trading_platform/strategy/contexts/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/contexts/strategy_context_builder.py (173 lines)
- Purpose: `StrategyContextBuilder` — the bridge from persisted market data to a strategy-ready `StrategyContext`. `build()` reads bars from Parquet (via `HistoricalBarDatasetReader`/`ParquetBarRepository._row_to_market_bar`) over a computed lookback date window, falls back to a secondary dataset if empty, strictly filters to `timestamp < bar_timestamp` (no lookahead), and requires at least `lookback_bars` before returning a context; `build_from_window()` is a simulation-path variant that binary-searches (`bisect`) a preloaded `SimulationWindowData` by symbol for O(log N) lookups instead of scanning.
- Notable: Both paths call `LookaheadGuardService.assert_historical_only()` before constructing the context — an explicit, enforced anti-lookahead invariant rather than a convention. The lookback-day heuristic (`lookback_bars * 2 + 14` for intraday-ish windows, else `lookback_bars // 78 + 5` days assuming ~78 5-min bars/day) is a reasonable approximation but hardcodes the 78-bars/day assumption inline rather than deriving it from `BarInterval`.

### src/autonomous_trading_platform/strategy/contexts/strategy_runtime_context.py (24 lines)
- Purpose: Plain `@dataclass` bundling the five collaborators needed to run a strategy end-to-end at runtime: evaluation service, bar-readiness service, signal writer, checkpoint writer, run-manifest service.

### src/autonomous_trading_platform/strategy/contexts/build_strategy_runtime_context.py (119 lines)
- Purpose: Composition-root factory `build_strategy_runtime_context()` — wires a `Session`, a `BaseStrategy`, and dataset/lookback options into a fully-constructed `StrategyRuntimeContext` (universe reader, Parquet bar reader, lookahead guard, context builder, signal writer, checkpoint writer/reader, ingestion-status reader, evaluation service, readiness service, run-manifest service). Also defines `SqlAlchemyUniverseMembershipReader`, a thin adapter over `UniverseVersionRepository`.
- Notable: Classic manual-DI composition root — no framework, just explicit constructor wiring, consistent with the rest of the codebase's layering discipline.

### src/autonomous_trading_platform/strategy/contracts/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/contracts/strategy_bar_readiness_result.py (8 lines)
- Purpose: Tiny pydantic `StrategyBarReadinessResult` (`target_bar_timestamp`, optional `reason`) returned by the bar-readiness service.

### src/autonomous_trading_platform/strategy/contracts/strategy_context.py (35 lines)
- Purpose: `StrategyPositionSnapshot` + frozen `StrategyContext` pydantic model — the single input object every `BaseStrategy.evaluate_symbol` receives: identifiers, timestamps, `bars`, `features`, current `position`, and an open-ended `state` dict.
- Notable: `bars: list[Any]` is loosely typed (duck-typed bar objects, per `base_strategy_helpers.get_close`), consistent with the earlier-noted weak typing there.

### src/autonomous_trading_platform/strategy/contracts/strategy_evaluation_result.py (11 lines)
- Purpose: Pydantic `StrategyEvaluationResult` (strategy_id, bar_timestamp, `list[Signal]`) — the output of one full strategy evaluation pass across the universe.

### src/autonomous_trading_platform/strategy/factories/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/factories/strategy_factory.py (14 lines)
- Purpose: `StrategyFactory.build(config: StrategyConfig) -> BaseStrategy` — looks up the `StrategyDefinition` in the registry, normalizes parameters, and calls the definition's `builder` callable.
- Notable: Extremely thin — correctly delegates all real logic to the registry/definition rather than duplicating it.

### src/autonomous_trading_platform/strategy/jobs/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/jobs/evaluate_strategy_job.py (255 lines)
- Purpose: `EvaluateStrategyJob.run()` — the scheduled-job entry point that ties bar-readiness checking, strategy evaluation, signal persistence, checkpoint advancement, and run-manifest recording into one observed unit of work (OTel span + structured job-lifecycle metrics/logging via `record_job_started/completed/failed`). Also defines `SignalWriter` (thin wrapper over `SignalRepository.insert_many`) and the `StrategyCheckpointWriter` Protocol.
- Notable: Solid operational hygiene — every branch (no new bar, evaluated-with-signals) is traced and metriced, and failures are classified as `"transient"` vs `"unknown"` for alerting. `_build_run_manifest` hardcodes several fields (`environment="dev"`, `broker="alpaca"`, `capital_bucket=Decimal("0")`, `dataset_version="unknown"`, `universe_version="unknown"`, `git_commit="dev"`) — clearly a job still tuned for local/dev use rather than a fully wired production manifest.

### src/autonomous_trading_platform/strategy/registry/parameter_metadata.py (30 lines)
- Purpose: `ParameterType` StrEnum (INT/FLOAT/BOOL/STRING) + frozen `ParameterSpec` dataclass describing one strategy-level parameter's search-space (min/max, discrete/step, tunable, mutation_strategy).
- Notable: Structurally identical to `components/component_parameter_schemas.py` — same tunability/mutation-strategy metadata duplicated at strategy-level and component-level rather than shared from one module. Minor duplication smell.

### src/autonomous_trading_platform/strategy/registry/parameter_schemas.py (194 lines)
- Purpose: One strict pydantic schema per strategy type (`MovingAverageCrossoverParameters`, `MomentumParameters`, `MeanReversionParameters`, `FactorBasedParameters`, `RandomParameters`, `StubParameters`, `IntentionalLoserParameters(StubParameters)`), each with cross-field validators (e.g. `short_window < long_window`, `sell_below <= buy_above`, `buy_below_z < sell_above_z`, `sell_score_threshold < buy_score_threshold`). `SCHEMAS_BY_STRATEGY` dispatch dict + `normalize_with_schema()` helper.
- Notable: `extra="forbid"` everywhere plus explicit before-validators that reject bools disguised as ints/floats (`isinstance(value, bool)` guard against Python's `bool` being an `int` subclass) — a subtle correctness detail many codebases miss.

### src/autonomous_trading_platform/strategy/registry/strategy_definition.py (90 lines)
- Purpose: `StrategyDefinition` dataclass — full metadata record for one registered strategy type: identity, family, debug/production flags, default parameters + validator + typed schema, warmup-bars function, required indicators/features, generation `parameter_specs`, compatibility flags (long-only/shorting/intraday/daily/adjusted/raw), determinism flag, and the `builder` callable. Convenience methods: `normalize_parameters`, `validate_parameters`, `export_parameter_schema` (JSON schema), `compute_warmup_bars`.
- Notable: Richly annotated metadata model that clearly anticipates a strategy-generation/optimization system (parameter search ranges, mutation strategies, generation candidates) even though no genetic/optimizer module was found within `strategy/` itself — the scaffolding exists but the consumer is elsewhere or not yet built.

### src/autonomous_trading_platform/strategy/registry/strategy_family.py (15 lines)
- Purpose: `StrategyFamily` StrEnum: MOMENTUM, MEAN_REVERSION, TREND, FACTOR, COMPOSITE, ENSEMBLE, DEBUG.
- Notable: `ENSEMBLE` family is declared but no strategy is registered under it — another forward-looking placeholder.

### src/autonomous_trading_platform/strategy/registry/strategy_registry.py (149 lines)
- Purpose: `StrategyRegistry` — order-preserving, lock-once catalog of `StrategyDefinition`s (mirrors `ComponentRegistry`'s design). `register()` cross-validates each strategy's `required_indicators` against the live `ComponentRegistry` before accepting the registration. Rich query API: by family, debug vs production, generation candidates.
- Notable: The cross-registry validation at registration time (strategy registry checks indicator names against the component registry) is a real safety net — a typo'd `required_indicators` entry fails fast at import time rather than silently at runtime.

### src/autonomous_trading_platform/strategy/registry/_registrations.py (681 lines)
- Purpose: Registers all 8 strategy types into the canonical `StrategyRegistry`: builder functions, warmup-bar functions, and full `StrategyDefinition` records (with complete `ParameterSpec` search-space metadata) for `stub`, `intentional_loser`, `random` (all DEBUG family), `moving_average_crossover` (TREND), `momentum` (MOMENTUM), `mean_reversion` (MEAN_REVERSION), `factor_based` (FACTOR), and `composite_rule` (COMPOSITE) — then locks the registry.
- Notable: **Definitively confirms the concrete strategy list** — exactly matches the header's 8-subclass enumeration. Each entry's `required_indicators` tuple is cross-checked against `ComponentRegistry` at `register()` time (see `strategy_registry.py`). Registration order is explicitly documented as stable/append-only ("do not reorder entries").

### src/autonomous_trading_platform/strategy/registry/validators.py (115 lines)
- Purpose: Per-strategy-type `_validate_*` functions (thin wrappers delegating to the pydantic schemas in `parameter_schemas.py`) plus shared numeric-range helper functions (`_require_positive_int`, `_require_float_range`, `_require_non_negative_float`) and a `VALIDATORS` dispatch dict.
- Notable: The shared helper functions (`_require_positive_int` etc.) are defined but appear unused by the `_validate_*` functions themselves, which all just call `schema.model_validate(params)` — likely legacy from before validation moved fully into pydantic schemas; effectively dead code within this file.

### src/autonomous_trading_platform/strategy/services/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/strategy/services/strategy_bar_readiness_service.py (84 lines)
- Purpose: `StrategyBarReadinessService.get_next_ready_bar(now)` — computes the latest completed bar boundary (aligned to `bar_interval_minutes`, default 5), compares against the strategy's last-evaluated checkpoint, and checks ingestion-success status before declaring a bar ready to evaluate. Defines `IngestionStatusReader` (always returns `True` — a stub) and `StrategyEvaluationCheckpointReader` (real, backed by `StrategyRuntimeStateRepository`).
- Notable: `IngestionStatusReader.has_successful_bar_ingestion` unconditionally returns `True` — this is a stub/placeholder that does not actually check ingestion status; the readiness gate for "ingestion succeeded" is not really enforced yet. Worth flagging as a gap, not a finished safety check.

### src/autonomous_trading_platform/strategy/services/strategy_checkpoint_writer_service.py (40 lines)
- Purpose: `StrategyCheckpointWriter.mark_evaluated(bar_timestamp)` — upserts a `StrategyRuntimeState` row recording the last-evaluated bar timestamp (creating one with `StrategyState.IDLE` if none exists).

### src/autonomous_trading_platform/strategy/services/strategy_evaluation_service.py (87 lines)
- Purpose: `StrategyEvaluationService.evaluate()` — the per-bar orchestration loop: fetch universe symbols as of the bar timestamp, build a `StrategyContext` per symbol (skipping symbols with insufficient history), call `strategy.evaluate_symbol()`, collect non-None signals into a `StrategyEvaluationResult`.
- Notable: Clean single-responsibility orchestrator defined entirely against Protocols (`MarketBarReaderProtocol`, `StrategyContextBuilderProtocol`, `UniverseMembershipReaderProtocol`, `StrategyProtocol`) — fully swappable/testable without concrete storage dependencies.

### src/autonomous_trading_platform/strategy/registry/__init__.py (49 lines)
- Purpose: Package entrypoint for `strategy.registry`. Importing it triggers `from . import _registrations` as a side effect, which populates and locks the singleton `StrategyRegistry`. Re-exports the public API: `get_registry`, `StrategyDefinition`, `StrategyFamily`, `StrategyRegistry`, `ParameterSpec`/`ParameterType`, and all per-strategy pydantic parameter schemas (`MovingAverageCrossoverParameters`, `MomentumParameters`, `MeanReversionParameters`, `FactorBasedParameters`, `RandomParameters`, `StubParameters`, `IntentionalLoserParameters`).
- Notable: Import-time side-effecting registration (`from . import _registrations as _registrations  # noqa: F401`) is a deliberate "populate-and-lock on first import" pattern — mirrors the same idiom used by `components/__init__.py` for the `ComponentRegistry`. Docstring is precise and accurate about what importing the package does.

## Standout candidates

- `composite/composite_rule_strategy.py` + `composite/composite_strategy_config.py` — the declarative composite-strategy DSL is real, not aspirational: a pydantic config schema that cross-validates every component reference against the live `ComponentRegistry` (type, executability, required inputs, parameter ranges) at construction time, a restricted-AST warmup-formula evaluator (not `eval()`), and a runtime executor that produces a full per-bar `explainability` trace (every indicator value, filter pass/fail, rule direction/confidence/reason, aggregation result, final confidence composition). Arguably the most rigorously engineered corner of this whole directory.
- `strategy/indicators/momentum.py` `rsi`/`rsi_wilder` — genuine quant literacy: the docstring explains Cutler-SMA vs Wilder-EMA path dependence and warns that textbook 70/30 thresholds assume the Wilder variant.
- Dual-registry design (`registry/strategy_registry.py` cross-validating `required_indicators` against `components/component_registry.py` at registration time) — a real fail-fast safety net against typo'd indicator references, not just cosmetic layering.
- `contexts/strategy_context_builder.py` — explicit, enforced anti-lookahead invariant (`LookaheadGuardService.assert_historical_only()` called on every path, plus a strict `timestamp < bar_timestamp` filter) rather than a convention developers must remember.
- `implementations/intentional_loser_strategy.py` and `implementations/random_debug_strategy.py` — thoughtful test/validation artifacts (a guaranteed-losing strategy to verify PnL accounting detects losses; a seeded-random null-hypothesis baseline) that most portfolio codebases skip.

## Gaps/smells

- **MACD is not implemented.** `grep -rni macd` across `src/` matches only an alias string (`"macd_crossover" -> "moving_average_crossover"` in `platform_replay/initial_state_hooks.py`). The real strategy is SMA fast/slow crossover, not a MACD line/signal-line construct, despite `exponential_moving_average` existing in `indicators/trend.py`.
- **FILTER / EXIT_RULE / SIZING component types are placeholders.** `components/_registrations.py` registers volatility/liquidity/regime/time filters, fixed/trailing/signal-based exits, and fixed/volatility-adjusted/confidence-weighted position sizing only as `metadata_only=True, is_executable=False, production_ready=False, experimental=True` with `implementation=None`. They exist in the `ComponentType` taxonomy but do nothing at runtime — a writeup should not describe the DSL as supporting filters/exits/sizing today.
- **Ingestion-readiness gate is a stub.** `services/strategy_bar_readiness_service.py`'s `IngestionStatusReader.has_successful_bar_ingestion` unconditionally returns `True`; the "did ingestion actually succeed for this bar" check is not enforced.
- **Composite rule dispatch is not fully decoupled from the registry.** `composite_rule_strategy.py::_evaluate_rule` special-cases the three known signal-rule component names via if/elif/raise instead of dispatching through `ComponentDefinition.implementation` — adding a new signal-rule component to the registry alone is insufficient; this method needs a matching update.
- **Dead code / duplication:** `registry/validators.py` defines `_require_positive_int`/`_require_float_range`/`_require_non_negative_float` helpers that are unused (all `_validate_*` functions just call `schema.model_validate(params)`); `registry/parameter_metadata.py`'s `ParameterSpec` is structurally identical to `components/component_parameter_schemas.py`'s `ComponentParameterSpec` but duplicated rather than shared.
- **Dev-only manifest fields hardcoded in production code path:** `jobs/evaluate_strategy_job.py::_build_run_manifest` hardcodes `environment="dev"`, `broker="alpaca"`, `capital_bucket=Decimal("0")`, `dataset_version="unknown"`, `universe_version="unknown"`, `git_commit="dev"` — the job is not yet wired for a real production run-manifest.
- **Forward-looking scaffolding with no consumer yet:** `StrategyFamily.ENSEMBLE` is declared but nothing is registered under it; the `tunable`/`mutation_strategy`/`discrete`/`step` fields on both `ParameterSpec` and `ComponentParameterSpec` clearly anticipate a genetic/optimizer search layer that was not found anywhere in `strategy/`.
- **Weak/duck typing at the context boundary:** `StrategyContext.bars: list[Any]` and the shared `base_strategy_helpers` accessors (`get_close`, `extract_closes`, etc.) accept both attribute- and dict-shaped bars via `Any`, trading type safety for flexibility.
- **Minor inconsistency:** `mean_reversion_strategy.py` inlines its buy/sell branching rather than reusing the `ThresholdRule` primitive that `momentum_strategy.py` and others compose; `stub_strategy.py` predates `base_strategy_helpers` and duplicates its own `_build_signal_id`/`_get_close` with a divergent ID-seeding scheme (prices instead of params).

## Coverage: read 62 of 62

No skips. This pass located and read the one file missing from the prior two passes' output (`registry/__init__.py`); all other 61 files were already covered with substantive entries. File count and LOC verified via `find`/`wc -l` (62 files, 5364 LOC); concrete `BaseStrategy` subclass count (8) and TODO/FIXME/XXX count (0) verified via `grep -rn`.
