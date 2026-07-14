# Audit — research part 1 (simulation/, pipeline/, validation/, black_litterman/, execution/, services/, research/__init__.py)

Auditor scope: `src/autonomous_trading_platform/research/` subdirs: simulation (24 files), pipeline (12), validation (10), black_litterman (2), execution (6), services (2), plus `research/__init__.py`. Total 57 files. Every file read in full.

## Verified counts

Command (from `src/autonomous_trading_platform/research/`):

```
find simulation pipeline validation black_litterman execution services -name '*.py' | sort | xargs wc -l
find simulation pipeline validation black_litterman execution services -name '*.py' | wc -l   # -> 56 (+ root __init__.py = 57)
grep -rn -E "TODO|FIXME|XXX" simulation pipeline validation black_litterman execution services __init__.py   # -> no matches
```

Per-subdir totals (files / LOC, `wc -l`):

| Subdir | .py files | LOC |
|---|---|---|
| simulation/ | 24 | 3,874 |
| pipeline/ | 12 | 1,959 |
| validation/ | 10 | 2,445 |
| black_litterman/ | 2 | 673 |
| execution/ | 6 | 337 |
| services/ | 2 | 71 |
| research/__init__.py | 1 | 0 |
| **Total** | **57** | **9,359** |

TODO/FIXME/XXX in scope: **0**.

Empty (0-byte) files: `simulation/__init__.py`, `simulation/contexts/__init__.py`, `simulation/models/__init__.py`, `simulation/services/__init__.py`, `pipeline/__init__.py`, `pipeline/stages/__init__.py`, `pipeline/aggregation/__init__.py`, `services/__init__.py`, `research/__init__.py`, and — significantly — `pipeline/stages/regime_stage.py` and `pipeline/aggregation/regime_aggregator.py` (see claim checks).

## Claim verification (headline)

### Black-Litterman claim — VERIFIED with one caveat (file: `black_litterman/black_litterman_research_service.py`, 668 lines)

- **Market-implied prior returns**: VERIFIED. `compute_market_implied_prior` (line 274) implements the reverse-optimization equilibrium prior pi = delta * Sigma * w_mkt: `risk_aversion * covariance_matrix @ benchmark_weights`.
- **View/confidence matrices**: VERIFIED. `_build_views` (line 370) constructs the P pick matrix (absolute views: 1.0 on target; relative views: +1 long / -1 short), Q view vector, and Omega. Omega is derived from per-view confidence in `_omega_from_confidence` (line 422) using an Idzorek-style formula: `omega_i = (p_i' (tau*Sigma) p_i) * (1-c)/c`, floored at 1e-12, diagonal Omega. Callers may alternatively pass explicit `view_matrix`/`view_vector`/`confidence_matrix` (all-or-none validated by a Pydantic model_validator). Dimension, finiteness, and positive-diagonal checks on all matrices.
- **Posterior returns + covariance via matrix inversion**: VERIFIED. `compute_posterior_returns` (line 283) is the canonical BL posterior: `M = inv(tau*Sigma) + P' inv(Omega) P`; posterior mean = `inv(M) @ (inv(tau*Sigma) pi + P' inv(Omega) Q)`; posterior covariance = `inv(M)`. Uses `np.linalg.pinv` (pseudo-inverse) rather than `solve`/Cholesky — robust to singular input but numerically blunter. Caveat: the returned "posterior_covariance" is the estimation-error covariance `inv(M)` only; some formulations report `Sigma + inv(M)` for the predictive return distribution. The math is real matrix algebra, not a wrapper.
- **Feeds a mean-variance optimizer**: VERIFIED. `_build_allocation_proposal` (line 438) always computes closed-form unconstrained weights via `_unconstrained_weights` (line 518): `w = Sigma^-1 (mu - lambda*1) / delta` with the Lagrange multiplier `lambda = (1' Sigma^-1 mu - delta) / (1' Sigma^-1 1)` enforcing the budget constraint — correct textbook derivation. When constraints are supplied, it invokes `application/services/mean_variance_optimizer.MeanVarianceOptimizer.optimize(...)` with `OptimizationObjective.MAXIMUM_UTILITY`, `expected_return_source="black_litterman_posterior_research"`, and `MeanVarianceConfig(dry_run=True)`; raises on INFEASIBLE/FAILED/FALLBACK_USED solver status.
- **`_enforce_research_only` guard**: VERIFIED. Line 307: raises `ResearchOnlyViolationError(PermissionError)` unless `run_context` is in an explicit allow-list (`research_cli`, `experiment_pipeline`, `offline_simulation`, `backtest`, `dry_run_allocation_analysis`); block-list includes `live_trading`, `paper_trading`, `production_rebalance`, `automatic_portfolio_construction`, `runtime_allocation`. Both allow-list AND block-list enforced (unknown contexts are also rejected). Called first thing in `run()` (line 173). Caveat on the claim's wording: it is a string-context convention checked at the entry point, not an inspection of the actual call stack — a caller could lie about its context. Blocked contexts are also recorded in the persisted artifact diagnostics.
- **SHA-256 hashing of inputs/outputs**: VERIFIED. `stable_hash` (line 302) = SHA-256 over canonical JSON (`sort_keys`, compact separators). Artifact carries `input_hash`, `views_hash`, `output_hash`, `artifact_hash` (hash of inputs+outputs+input_hash), plus `covariance_snapshot_hash`/`covariance_matrix_hash`. All persisted to Postgres via `BlackLittermanResearchRepository` into `BlackLittermanResearchRunRow` with `metadata_json.research_only=True`.

### Multi-stage pipeline claim — PARTIALLY VERIFIED

Simulation -> walk-forward -> Monte Carlo stages exist and are real (`pipeline/stages/simulation_stage.py`, `walk_forward_stage.py`, `monte_carlo_stage.py`, orchestrated by `pipeline/pipeline_runner.py` with survivor narrowing between stages). **The regime stage does NOT exist**: `pipeline/stages/regime_stage.py` and `pipeline/aggregation/regime_aggregator.py` are 0-byte empty files; `stage_registry.py` line 38 has `# "regime": RegimeStage,   # future` commented out; `base_stage.py` docstring labels RegimeStage "(future)". Any writeup should say "simulation, walk-forward, and Monte Carlo stages; regime stage stubbed/planned".

### Walk-forward fold-consistency / Sharpe-degradation stability scores — see `validation/walk_forward_validation.py` and `validation/robustness_score.py` entries below (verified in part; details per file).

---

## Per-file entries

### src/autonomous_trading_platform/research/black_litterman/black_litterman_research_service.py (668 lines)
- Purpose: Research-only Black-Litterman service: resolves a covariance matrix (inline or from a persisted correlation/covariance snapshot), computes the equilibrium prior, builds P/Q/Omega from semantic views, derives the BL posterior, produces unconstrained + optionally constrained (MVO) allocation proposals, and persists a fully hashed artifact row.
- Notable: Real quant math throughout (see claim verification above). Strong input validation: covariance symmetry/finiteness, benchmark weights sum to 1 and non-negative, view dimension checks, positive Omega diagonal. Uses `pinv` everywhere instead of `solve` (numerically forgiving, slower, can silently regularize a singular Sigma). Structured lifecycle log constants (STARTED/PRIOR_COMPUTED/POSTERIOR_COMPUTED/COMPLETED/FAILED). Smells: `diagnostics["view_prior_returns"]` is always an empty list (dead placeholder); research-only guard is a string convention, not stack inspection; posterior covariance omits the `+Sigma` predictive term.

### src/autonomous_trading_platform/research/black_litterman/__init__.py (5 lines)
- Purpose: Re-exports `BlackLittermanResearchService`.

### src/autonomous_trading_platform/research/pipeline/pipeline_runner.py (200 lines)
- Purpose: Orchestrates a sequence of `BaseStage`s: feeds survivors from each stage into the next, aggregates all simulation results/filter outputs, records per-stage OTel metrics (runs, duration, survivors entered/passed) and spans.
- Notable: Funnel design ("final_survivors ... the elite set"). Breaks early with a warning if survivors empty. Observability is thorough (record_step_started/completed/failed + spans + survivor gauges). No persistence here — stages own their own checkpointing.

### src/autonomous_trading_platform/research/pipeline/stages/base_stage.py (117 lines)
- Purpose: ABC for pipeline stages + `StageResult` dataclass (simulation results, filter outputs, survivors; n_entered/n_passed/n_failed properties).
- Notable: Docstring explicitly lists RegimeStage and MonteCarloStage as "(future)" — MonteCarloStage has since been implemented but the docstring is stale for it; RegimeStage genuinely absent. Loader contract: each stage owns `from_dict(raw, simulation_runner)` deserialization.

### src/autonomous_trading_platform/research/pipeline/stages/stage_registry.py (69 lines)
- Purpose: Dict-based registry mapping YAML `type` strings ("simulation", "walk_forward", "monte_carlo") to stage classes; `load()` dispatches `from_dict`.
- Notable: `"regime": RegimeStage` commented out as future. Defaults missing `type` to "simulation" for backward compat with old YAML.

### src/autonomous_trading_platform/research/pipeline/stages/simulation_stage.py (271 lines)
- Purpose: Stage 1/2 ("cheap"/"intermediate"): one simulation per surviving strategy over a fixed window via `ParallelExecutionService`, then `FilterScoreService.filter_and_rank` gates survivors.
- Notable: Deterministic per-unit seeds via `DeterministicSeedService.derive_seed` (keyed on base seed, experiment, strategy, config hash, stage, window role). Optional checkpoint/resume via `ResearchCheckpointService`. Smell (fail-open): if ALL simulations return None, the stage returns `survivors=survivors` — every strategy passes a stage in which nothing was actually tested (lines 189-195). Same pattern in walk-forward and MC stages.

### src/autonomous_trading_platform/research/pipeline/stages/walk_forward_stage.py (555 lines)
- Purpose: Stage 3 ("heavy") walk-forward analysis: generates rolling train/test folds (`train_days`/`test_days`/`step_days`), runs train sims, filters, runs test sims only for train-passers, filters again; a strategy passes a fold iff it passes BOTH windows, and survives the stage iff it passes all folds (or >= `min_folds_passed`).
- Notable: Real walk-forward mechanics with anchored-rolling fold generation and config sanity checks in `__post_init__` (range must fit train+test). Separate train vs test FilterConfig/ScoringWeights — allows stricter out-of-sample thresholds. Deterministic seeds include fold_id. Latent bug: in `_run_fold` line 502, `train_sim_by_id[sid]` KeyErrors if any individual train simulation returned None (result filtered out at line 412) while others succeeded — per-strategy sim failure is not handled, only the all-failed case. Fail-open smell shared with simulation stage (all-sims-failed => survivors pass through, lines 285-291). Module-level `_parse_filter_config`/`_parse_scoring_weights` helpers appear dead (from_dict uses Pydantic `WalkForwardStageConfigModel` instead).

### src/autonomous_trading_platform/research/pipeline/stages/monte_carlo_stage.py (448 lines)
- Purpose: Stage 4 ("elite") Monte Carlo robustness gate: runs each surviving strategy `n_runs` times over the same window with seeds `base_seed + i`, aggregates via `MonteCarloAggregator`, survives iff per-run pass rate >= `min_pass_rate`.
- Notable: Good docstring articulating walk-forward = time robustness vs Monte Carlo = structural robustness (seed-varied slippage/ordering draws). Representative run for scoring = run whose Sharpe is closest to the median Sharpe (`_pick_representative_run`) so stage scores stay comparable. Smells: mutates `fo.filter_result.passed` post-hoc with `# type: ignore[attr-defined]` to override the verdict; if `_run_monte_carlo` yields zero results the strategy silently survives (lines 275-277, fail-open); seed scheme `base_seed + run_index` can collide across strategies/stages (unlike the hashed DeterministicSeedService used by other stages); duplicated `_parse_filter_config`/`_parse_scoring_weights` helpers also dead code here.

### src/autonomous_trading_platform/research/pipeline/aggregation/monte_carlo_aggregator.py (299 lines)
- Purpose: Pure-statistics aggregator for MC runs: per-metric distributions (mean/median/std/min/max via `statistics` module) for Sharpe, total return, max drawdown; per-run threshold evaluation; pass verdict = pass_rate >= min_pass_rate; deduplicated failure-reason summary.
- Notable: Deliberate design note: per-run filtering instead of filtering aggregate means, "Mean Sharpe of 1.2 could hide the fact that 40% of runs went badly negative" — sound reasoning. `MetricDistribution.coefficient_of_variation` computed but pass verdict only uses pass_rate (min_pass_rate); std/CV are informational. `_evaluate_runs` re-implements a subset of FilterConfig checks (sharpe/return/drawdown/trades) rather than reusing FilterScoreService — duplication risk if thresholds evolve. Missing metrics coerced to 0.0 ("safe" extractors), which can flip drawdown/return semantics silently.

### src/autonomous_trading_platform/research/pipeline/stages/regime_stage.py (0 lines)
- Purpose: Placeholder only — file is completely empty. Regime stage is NOT implemented.

### src/autonomous_trading_platform/research/pipeline/aggregation/regime_aggregator.py (0 lines)
- Purpose: Placeholder only — empty file.

### src/autonomous_trading_platform/research/pipeline/__init__.py, stages/__init__.py, aggregation/__init__.py (0 lines each)
- Purpose: Empty package markers.

---

## Per-file entries (continued: execution/, services/, simulation/, validation/, research/__init__.py)

### src/autonomous_trading_platform/research/__init__.py (0 lines)
- Purpose: Empty package marker for `research/`.

### src/autonomous_trading_platform/research/execution/__init__.py (27 lines)
- Purpose: Re-exports the deterministic-seed, execution-result/unit, and parallel-execution public API.

### src/autonomous_trading_platform/research/execution/deterministic_seed_service.py (43 lines)
- Purpose: Derives a reproducible int seed (bounded to `< 2**31`) from a `DeterministicSeedInputs` dataclass (base_seed, experiment/strategy/config/stage/window/fold/trial identity) via SHA-256 of canonical JSON.
- Notable: Same hash-then-truncate-to-8-bytes pattern used elsewhere in the codebase (BL service, artifact identity). Genuinely deterministic — used by simulation/walk-forward/MC pipeline stages to keep per-unit seeds collision-resistant and reproducible. `derive_seed` is the mechanism the walk-forward/simulation stages rely on (Monte Carlo stage notably does NOT use it — see part-1 MC stage entry — seed collision risk noted there).

### src/autonomous_trading_platform/research/execution/execution_result.py (38 lines)
- Purpose: `ExecutionFailure`/`ExecutionResult` (generic, frozen) dataclasses plus `ParallelExecutionError` aggregating failures sorted by `sort_key`.
- Notable: Clean generic typing; `succeeded` convenience property.

### src/autonomous_trading_platform/research/execution/execution_unit.py (15 lines)
- Purpose: Generic frozen dataclass wrapping a `Callable[[], T]` plus `unit_id`/`sort_key`/`metadata` for the parallel executor.

### src/autonomous_trading_platform/research/execution/parallel_execution_service.py (198 lines)
- Purpose: Local `ThreadPoolExecutor`-backed (or serial) executor for independent research units (simulations), with deterministic ordering, fail-fast support, and OTel duration/count metrics per unit.
- Notable: Explicit design rationale in the docstring for choosing threads over processes (session/repository objects aren't picklable). Per-unit `contextvars.copy_context()` avoids sharing a single Context across threads. `fail_fast` in the parallel path cancels only *pending* futures (already-submitted-and-running ones still complete) — reasonable, not a smell, just worth noting for behavior expectations. Results always re-sorted deterministically by `sort_key` regardless of execution order.

### src/autonomous_trading_platform/research/execution/result_ordering.py (16 lines)
- Purpose: Two one-line helpers sorting `ExecutionUnit`/`ExecutionResult` lists by `sort_key`.

### src/autonomous_trading_platform/research/services/research_dataset_resolver_service.py (71 lines)
- Purpose: Resolves a `dataset_version` + `PriceBasis` to a concrete Parquet dataset root path (`RAW_BARS_DATASET`/`ADJUSTED_BARS_DATASET`), raising `FileNotFoundError` if the version root doesn't exist on disk.
- Notable: Simple, correct; only supports RAW/ADJUSTED bases (raises on anything else).

### src/autonomous_trading_platform/research/simulation/artifact_identity.py (90 lines)
- Purpose: `SimulationArtifactIdentity` frozen dataclass — canonical collision-safe identity (run/experiment/strategy/dataset/stage/window + optional seed/universe/config-hash/dates) used to build deterministic hive-partition paths (`partition_path()`) and manifest dicts (`to_manifest_dict()`) for every persisted simulation artifact.
- Notable: `__post_init__` validates all six required fields are non-empty. Well-designed to prevent Parquet write collisions across folds/seeds/stages.

### src/autonomous_trading_platform/research/simulation/contexts/build_simulation_context.py (186 lines)
- Purpose: Composition-root factory (`build_simulation_context`) wiring together every simulation collaborator (bar reader, dataset resolver, window loader, cost/slippage/fill models, execution engine, strategy factory/context builder, experiment orchestration, caches, repositories) into a single `SimulationContext`.
- Notable: Hardcodes `VolumeShareSlippageModel()` and zero commission (`commission_per_share=Decimal("0.0000")`) as the default cost model regardless of caller preference — anyone using this factory gets frictionless commissions by default, only slippage cost applies. `_DEFAULT_TOTAL_CAPITAL = 100_000.00` and `_DEFAULT_UNIVERSE_SIZE = 5` are magic defaults for position sizing.

### src/autonomous_trading_platform/research/simulation/contexts/simulation_context.py (42 lines)
- Purpose: Plain dataclass bag holding every collaborator instance produced by `build_simulation_context` (bar reader, loader, repositories, runner, engine, orchestration service, caches).

### src/autonomous_trading_platform/research/simulation/models/cost_model_type.py (7 lines)
- Purpose: `CostModelType` StrEnum: `fixed_bps` / `volume_share` / `spread_aware`.

### src/autonomous_trading_platform/research/simulation/models/fill_model.py (99 lines)
- Purpose: `SimulatedFillModelConfig` — the single config object controlling all realism knobs for simulated fills: latency bars, volume-participation cap, probabilistic partial fills, limit-order touch-fill probability, order rejection probability, DAY-order expiry.
- Notable: Extremely thorough `__post_init__` validation (every probability bounded to `(0, 1.0]`, min/max fraction ordering enforced). Inline comments precisely document the semantics and interaction order of each knob (R-04/F-01/F-02/R-07 tags cross-reference a presumably external realism spec) — this reads as genuine, carefully-reasoned execution-realism modeling, not a stub.

### src/autonomous_trading_platform/research/simulation/models/slippage_context.py (13 lines)
- Purpose: Plain slots dataclass carrying quantity + bar OHLC/volume context into slippage models.

### src/autonomous_trading_platform/research/simulation/models/slippage_model.py (43 lines)
- Purpose: `SlippageModel` — fixed-bps slippage, deliberately marked "retained for testing... do not use as the realistic simulation default."
- Notable: Docstring self-flags as the naive fallback; real default is volume-share (see build_simulation_context.py).

### src/autonomous_trading_platform/research/simulation/models/spread_aware_slippage_model.py (72 lines)
- Purpose: Derives a half-spread-bps slippage estimate from OHLC range (`(high-low)/close/2*10000`), clamped to `[min_half_spread_bps, max_half_spread_bps]` and scaled by a multiplier.
- Notable: Real (if simple) market-microstructure proxy — reasonable when volume data is unreliable.

### src/autonomous_trading_platform/research/simulation/models/volume_share_slippage_model.py (103 lines)
- Purpose: Primary slippage model — `slippage_bps = impact_coefficient_bps * min(qty/bar_volume, max_volume_share)`, with an OHLC-based half-spread fallback when volume is absent. Includes `from_calibration_snapshot()` to build a model from empirically calibrated coefficients.
- Notable: Textbook linear market-impact model. The calibration-snapshot constructor implies an actual calibration pipeline exists elsewhere (`research/calibration/`, out of this scope) feeding real coefficients back into simulation — a nice, non-trivial closed loop if that pipeline is real (not verified in this pass — out of scope).

### src/autonomous_trading_platform/research/simulation/services/feature_dependency_resolver_service.py (159 lines)
- Purpose: Resolves a strategy's declared `required_persisted_features` into validated `SimulationFeatureDatasetRequest`s (looking up a matching, lineage-validated `FeatureDatasetVersion` per feature) and computes registry-derived warmup bar counts, raising `FeatureDependencyError` on any unresolvable dependency or unknown feature name.
- Notable: Fails loud and immediately (no silent fallback) — good practice for a research-integrity-sensitive path. Cleanly separates "what features does this strategy need" (registry) from "which validated dataset satisfies that need" (repository lookup).

### src/autonomous_trading_platform/research/simulation/services/lookahead_guard_service.py (83 lines)
- Purpose: Enforces no-lookahead invariants: `assert_historical_only` raises if any context bar's timestamp >= the simulation timestamp; `filter_historical_only` filters bars to strictly-before; `assert_timeline_strictly_increasing` guards against non-monotonic timelines.
- Notable: Small, focused, and actually invoked from the hot path in `simulation_execution_engine.py` (`_evaluate_signals`) and `simulation_runner.py` — a real, load-bearing anti-lookahead-bias guard, not decorative.

### src/autonomous_trading_platform/research/simulation/services/order_simulator_service.py (67 lines)
- Purpose: Thin wrapper generating `OrderIntent`s from signals via `PortfolioConstructionService.generate_order_intents`, then filtering out intents missing symbol/qty/side/order_type or with qty<=0.
- Notable: Appears unused by the current `simulation_execution_engine.py` hot path, which constructs orders directly via `_construct_orders`/`SimplePositionSizer` rather than through `PortfolioConstructionService`. Possibly a legacy/alternate order-construction path — worth flagging as possibly dead code (not confirmed dead without a repo-wide reference search, which is out of this file-reading pass' scope).

### src/autonomous_trading_platform/research/simulation/services/result_recorder_service.py (50 lines)
- Purpose: Thin pass-through writing trade logs, equity curve, per-bar metrics, positions, and signal log DataFrames to `ParquetSimulationRepository`, keyed by `SimulationArtifactIdentity`.

### src/autonomous_trading_platform/research/simulation/services/simple_position_sizer.py (86 lines)
- Purpose: Equal-weight position sizer — divides `total_capital` across `universe_size` symbols, converts BUY signals to whole-share target quantities (floor division), SELL/FLAT to zero.
- Notable: Explicitly labeled "simulation-only... pure math, no DB, no governance, no policies" — correctly scoped. Uses `Decimal` throughout with `ROUND_DOWN` for conservative (never over-buy) share counts.

### src/autonomous_trading_platform/research/simulation/services/simulation_cost_model_service.py (83 lines)
- Purpose: Applies a pluggable slippage model's fill price plus a per-share commission (with a minimum) to compute `SimulatedTradeCosts` (fill price, slippage notional/rate, commission, total cost) for a given side/reference price/quantity.
- Notable: Clean separation of "which slippage model" (injected) from "cost accounting" (this class). Real Decimal-precision cost accounting.

### src/autonomous_trading_platform/research/simulation/services/simulation_execution_engine.py (1,069 lines)
- Purpose: The core bar-by-bar simulation loop — for each timestamp: matures pending T+N settlements, executes previously-scheduled (latency-delayed) fills, evaluates strategy signals (lookahead-guarded), applies dividends, constructs order intents via the position sizer, plans child orders through `SimulatedExecutionModel` (TWAP/VWAP/limit/market policies), reserves/releases buying power, simulates fills, and records trade/position/equity/metric/signal/dividend rows.
- Notable: This is a genuinely sophisticated event-driven backtest engine — not a toy: tracks settled vs. unsettled vs. reserved cash separately with a real buying-power reservation system (`_try_reserve`/`_release_lost_intents`) that rejects orders exceeding available buying power; models T+N settlement lag via `PendingSettlement` records keyed by bar index (not wall-clock, so fully deterministic); applies cash dividends on ex-date to held positions; supports execution-latency scheduling (market orders fill same-bar at close, or N bars later at open) with a `scheduled: dict[int, list[OrderIntent]]` bar-indexed order book. **Smells**: `_record_metrics` and `_build_equity_row` hardcode `"bar_return": 0.0` and `"drawdown": 0.0` (lines 920, 978) — these are named/typed as real per-bar metrics but are dead placeholders never actually computed in this engine (return/drawdown metrics are computed downstream from the equity curve via `research/experiments/filtering/metrics/risk_metrics.py`, so the per-bar-metrics/equity-curve Parquet artifact itself carries misleading always-zero columns). `_build_trade_rows` also hardcodes `"slippage": 0.0` (line 1012) even though `SimulationCostModelService` computes exact `slippage_notional` per fill — the trade log never records the (real, computed) slippage value, a domain-relevant field silently dropped at the row-builder boundary.

### src/autonomous_trading_platform/research/simulation/services/simulation_execution_model.py (192 lines)
- Purpose: `SimulatedExecutionModel.plan()` converts a parent `OrderIntent` into `(bar_offset, child_intent)` pairs according to an `ExecutionPolicyConfig`'s `PolicyMode` (PASSTHROUGH/MARKET/LIMIT/TWAP/VWAP_LITE), reusing the live `TWAPSlicer`/`VWAPLiteSlicer`/`OrderTypeResolver` policy code without touching broker code.
- Notable: Deterministic UUID5 child-intent IDs derived from `(parent_intent_id, slice_index, policy_mode)` — reproducible replay. Genuinely reuses the *same* execution-policy slicing logic as live trading (`execution/policy/`), so simulated TWAP/VWAP behavior should match production behavior — a real shared-code guarantee rather than a simulation-only reimplementation. `_resolve_limit` swallows any `Exception` broadly to fall back to the original intent (documented as deliberate graceful-degradation, but still a broad except).

### src/autonomous_trading_platform/research/simulation/services/simulated_execution_service.py (401 lines)
- Purpose: Turns order intents + current bar OHLCV into `Fill`s, modeling: stochastic order rejection (F-02) before any type-specific logic; for limit orders, gap-open vs. intrabar-touch eligibility, then touch-probability gating (R-07); for both market and limit orders, volume-participation capping (R-04) and probabilistic partial fills (F-01); unfilled/partial remainders carry forward (or expire, if `expire_unfilled_limit_orders`).
- Notable: All stochastic draws go through a single injected, resettable `random.Random` instance (`reset_for_run`), which the `SimulationRunner` seeds per-run via `DeterministicSeedService`-independent `_init_rng(seed)` — giving fully reproducible stochastic fills. Deterministic UUID5 fill/order IDs keyed on `(run_id, intent_id, monotonic fill counter)`. Market order pricing correctly differentiates same-bar close (latency=0) vs. next-bar open (latency>=1) to avoid a subtle look-ahead pricing bug. This is real, carefully-sequenced microstructure simulation, not a thin wrapper — probability gates are layered in a specific, documented order (rejection → eligibility → touch → participation → partial-fill).

### src/autonomous_trading_platform/research/simulation/services/simulation_window_loader_service.py (365 lines)
- Purpose: Loads bounded-partition bar (and optional feature) data for a simulation window via `HistoricalBarDatasetReader`, with warmup-bar prepending (to seed indicators without generating trades), optional daily resampling, and a `shuffle_window_bar_timestamps` utility (presumably for a randomization/permutation-test validation mode).
- Notable: Explicitly documents "must not read entire dataset versions for windowed simulation access" — bounded-read discipline is a stated invariant. Calendar-day-per-trading-day overfetch heuristic (`7/5`) to guarantee enough warmup bars across weekends/holidays, then trims to the exact count. `_resample_bars_to_daily` correctly aggregates OHLCV (first open, max high, min low, last close, summed volume) rather than naively sampling one bar per day.

### src/autonomous_trading_platform/research/simulation/simulation_runner.py (664 lines)
- Purpose: Top-level per-strategy simulation orchestrator: derives a deterministic `run_id` (UUID5 over all inputs), resolves the dataset, resolves feature dependencies + warmup, records a `RUNNING` `SimulationRun`/`RunManifest`/adhoc `Experiment`/`StrategyConfig` row, loads the window, builds and executes the strategy via `SimulationExecutionEngine`, records artifacts, strips warmup rows before computing return/risk/trade/stability metrics, and records `COMPLETED`/`FAILED` status back onto the SoR rows.
- Notable: `_derive_run_id` (UUID5 over strategy/seed/dataset/price-basis/symbols/dates/experiment/stage/window) means identical simulation requests always reproduce the same run_id → same fill/intent IDs — a real determinism guarantee, not just seeded RNG. `RunManifest.schema_definition["determinism"]` self-documents `isolated_python_rng: True` but `isolated_numpy_rng: False` — an honest admission that NumPy's global RNG state is *not* isolated per run (a latent reproducibility gap if any collaborator uses `np.random` without an explicit Generator). Correctly strips warmup-period equity rows before computing Sharpe/CAGR/drawdown so warmup doesn't skew metrics.

### src/autonomous_trading_platform/research/validation/__init__.py (1 line)
- Purpose: Docstring-only package marker ("Advanced validation framework for research quality assurance").

### src/autonomous_trading_platform/research/validation/walk_forward_validation.py (167 lines)
- Purpose: `WalkForwardValidationService.analyze()` takes a list of per-fold `FoldValidationInput` (train/test Sharpe, drawdown, return, pass flags) and computes `fold_consistency` (passed/total), `train_test_degradation` ((avg_train − avg_test)/(|avg_train|+ε)), `fold_sharpe_cv` (stdev/|mean| of test Sharpes), and `fold_sharpe_stability` (1/(1+CoV)), plus an expanding cumulative-mean Sharpe curve and generated warnings.
- Notable: **Verified claim**: this is the file implementing "fold-consistency and Sharpe-degradation stability scores" — genuine `statistics` module math (mean/median/stdev), not a wrapper. Pure function of already-computed fold metrics (no simulation here); the actual walk-forward *simulation* lives in `pipeline/stages/walk_forward_stage.py` (part 1). `is_consistent`/`is_stable` threshold properties (0.6 / 0.5) are hardcoded rather than configurable on this class (thresholds do appear configurable at the orchestrator/robustness-weight level elsewhere).

### src/autonomous_trading_platform/research/validation/robustness_score.py (247 lines)
- Purpose: `RobustnessScoreBuilder` — fluent builder aggregating up to six independently-optional component scores (walk-forward consistency, MC stability, regime robustness, parameter stability, stress resilience, overfitting resistance) into a single `RobustnessScore.overall`, renormalizing weights over only the components actually supplied so partial validation runs remain comparable.
- Notable: Each `with_*` method documents its own normalization formula inline (e.g., CoV→stability via `1/(1+cv)`, regime score = base + Sharpe bonus − sensitivity penalty, capped/clamped). Weight renormalization over active components (not just zero-filling missing ones) is a legitimately good design choice — a validation run missing MC data isn't unfairly penalized. Default weights documented as reflecting "the platform's research philosophy" (walk-forward weighted highest at 0.30).

### src/autonomous_trading_platform/research/validation/validation_result.py (91 lines)
- Purpose: `ValidationStageResult` (frozen, score bounds-checked to [0,1] in `__post_init__`) and `ValidationSummary` (aggregates stage results + `RobustnessScore` + `overall_passed = robustness_score.overall >= min_robustness_score`), both with `as_dict()` serializers.

### src/autonomous_trading_platform/research/validation/overfitting_analysis.py (330 lines)
- Purpose: `OverfittingAnalyzer.analyze()` combines up to seven independent, individually-optional heuristic indicators (train/test Sharpe degradation, fold instability, MC instability, regime concentration, low trade count, narrow-period alpha concentration, parameter fragility) into a single weighted `overfitting_probability`, with the same active-component weight-renormalization pattern as `robustness_score.py`.
- Notable: Explicitly documents "No black-box ML models... fully decomposable for human review" as a design principle — genuinely true of the implementation (every indicator is a simple, explainable statistic). `_narrow_period_alpha` is a real (if simple) concentration statistic: fraction of total absolute bar-return contributed by the top-10% highest-absolute-return bars, computed via NumPy percentile — a legitimate proxy for "does performance depend on a few lucky bars."

### src/autonomous_trading_platform/research/validation/parameter_sensitivity_analysis.py (294 lines)
- Purpose: `ParameterSensitivityAnalyzer` sweeps each tunable strategy parameter across N evenly-spaced values (via a caller-supplied `run_fn: params -> (sharpe, drawdown, return)`), computing `sensitivity_score = clamp(std(Sharpe)/(range(Sharpe)+ε))`, `stability_score = 1 − sensitivity`, and the largest contiguous "stability region" of parameter values meeting a minimum Sharpe.
- Notable: Real sweep-based sensitivity analysis — the `RunFn` abstraction correctly decouples this from any specific simulation backend (testable with a mock, wired to `SimulationRunner` in production). `enable_parameter_sensitivity` defaults to **False** at the orchestrator level (confirmed in validation_orchestrator.py) because it requires N additional full simulation runs — an honest cost/completeness tradeoff, not silently skipped without acknowledgment.

### src/autonomous_trading_platform/research/validation/stress_test_service.py (280 lines)
- Purpose: `StressTestService.run()` applies seven deterministic return-transformation scenarios (2x/3x volatility, ±5%/10% one-time shocks, downside-only amplification, sign-flip trend reversal, 50bps liquidity-collapse cost) to an already-computed equity curve's bar returns, rebuilds the stressed equity curve, and recomputes real Sharpe/drawdown/return via the platform's own `risk_metrics`/`return_metrics` functions to determine pass/fail per scenario.
- Notable: Genuinely deterministic and fast (no re-simulation) by design — reuses the *actual* production risk/return metric functions rather than reimplementing them, so stressed Sharpe/drawdown are consistent with the rest of the platform. `survived` requires both a minimum Sharpe AND a minimum (least-negative) drawdown — a sound two-sided bar.

### src/autonomous_trading_platform/research/validation/survivorship_validation.py (227 lines)
- Purpose: `SurvivorshipValidationService.validate()` composes the platform's existing `SurvivorshipGuard` (config-level PIT anchor check) and `UniverseHistoryService` (point-in-time membership) into a validation-framework result: detects symbols in the experiment universe that only became available after experiment start ("future leak"), plus per-fold PIT membership checks for walk-forward folds.
- Notable: Correctly typed via `Protocol`/`runtime_checkable` rather than importing concrete guard classes, keeping this module decoupled and independently testable. All guard/history calls are individually try/excepted into errors/warnings rather than raising — appropriate for a validation-reporting layer (failures should be reported, not crash the pipeline), though this does mean a broken guard silently degrades to "skipped" if not read carefully.

### src/autonomous_trading_platform/research/validation/validation_artifact_repository.py (253 lines)
- Purpose: `ValidationArtifactRepository.persist()` writes each validation stage's output (robustness score, walk-forward result, stress test, overfitting analysis, sensitivity profile) to its own hive-partitioned Parquet dataset, independently try/excepted so one failing write doesn't block the others.
- Notable smells: `_persist_stress_test` contains a self-documented incompleteness — the comment reads "We need the full scenario results from the stage — not stored there... For now, write the summary as a single aggregated row... In production, the orchestrator should expose scenario-level rows," and the fallback row hardcodes `original_sharpe`/`stressed_sharpe`/`original_drawdown`/`stressed_drawdown` all to `0.0` regardless of actual values — so the persisted `validation/stress_test_results/` Parquet dataset does NOT actually contain the seven real per-scenario Sharpe/drawdown numbers computed by `StressTestService`, only a degenerate one-row summary. `_persist_sensitivity` similarly hardcodes `"reference_value": 0.0,  # not stored in summary dict` — another acknowledged gap. Both are honestly commented as known limitations rather than silently wrong, but a portfolio writeup claiming "stress test results persisted to Parquet" should be qualified: aggregate-level persistence works; scenario-level persistence does not (yet).

### src/autonomous_trading_platform/research/validation/validation_orchestrator.py (555 lines)
- Purpose: `ValidationOrchestrator.run_validation()` is the top-level entry point running seven ordered stages (survivorship, walk-forward, regime, Monte Carlo, overfitting, stress test, parameter sensitivity — each individually enable/data-gated), accumulating per-stage `ValidationStageResult`s, feeding each into `RobustnessScoreBuilder`, and returning a `ValidationSummary` with OTel metrics/spans and optional Parquet persistence.
- Notable: Pure orchestration over already-computed inputs (explicitly "does NOT run simulations directly" except optionally for parameter sensitivity) — correctly composable/testable. `_run_regime_validation` normalizes worst-regime-Sharpe from `[-2, 2]` to `[0, 1]` via a hardcoded linear formula `(worst_sharpe + 2.0) / 4.0` — an arbitrary but documented normalization range (a Sharpe outside ±2 would clamp, which is reasonable for a bounded score). Good OTel instrumentation (per-stage duration/run-count/status, final robustness-score gauge).

---

## Updated claim verification

### Walk-forward fold-consistency / Sharpe-degradation stability scores — VERIFIED (file: `validation/walk_forward_validation.py`, 167 lines)
`WalkForwardValidationService.analyze()` computes `fold_consistency = n_passed/n_folds`, `train_test_degradation = (avg_train − avg_test)/(|avg_train|+ε)`, and `fold_sharpe_stability = 1/(1+CoV(test_sharpes))` using the standard-library `statistics` module (mean/median/stdev) — real, auditable statistics, not a wrapper around a stub. Consumed directly by `validation_orchestrator.py` stage 2 and by `RobustnessScoreBuilder.with_walk_forward()`.

### Quant math assessment (this batch)
Every numerically-flavored module read in this batch (`walk_forward_validation.py`, `robustness_score.py`, `overfitting_analysis.py`, `parameter_sensitivity_analysis.py`, `stress_test_service.py`, plus the simulation-side `volume_share_slippage_model.py`/`spread_aware_slippage_model.py`/`simulation_cost_model_service.py`/`simulated_execution_service.py`) performs genuine, explainable statistics or execution-microstructure math (mean/stdev/CoV, percentile-based concentration, linear market-impact models, deterministic return-transformation stress scenarios recomputed through the platform's real risk-metric functions). None of it is a thin no-op wrapper. The one recurring caveat is in the *persistence* layer (`validation_artifact_repository.py`), where two of five artifact writers acknowledge in comments that they persist a degenerate/placeholder row instead of the full computed detail — the computation is real, but not all of it reaches Parquet.

---

## Standout candidates (for portfolio writeup)

- `black_litterman/black_litterman_research_service.py` — genuine textbook Black-Litterman (equilibrium prior, P/Q/Omega construction, closed-form posterior via matrix inversion, closed-form MVO weights with Lagrange multiplier), fully SHA-256-hashed and research-only-guarded.
- `simulation/services/simulation_execution_engine.py` — a real event-driven backtest engine: separate settled/unsettled/reserved cash tracking, bar-indexed T+N settlement, ex-date cash dividends, latency-aware order scheduling, buying-power-gated order acceptance.
- `simulation/services/simulated_execution_service.py` — layered, seeded-RNG execution realism: stochastic rejection, gap-open vs. intrabar limit-touch eligibility, touch-probability gating, volume-participation capping, probabilistic partial fills, all deterministically reproducible per run.
- `validation/validation_orchestrator.py` + `validation/robustness_score.py` — a coherent, composable multi-stage validation framework with honest weight-renormalization for partial data and full observability instrumentation.
- `simulation/services/simulation_execution_model.py` — simulation-side execution-policy planner that reuses the *same* TWAP/VWAP/order-type-resolver code as live trading, giving a genuine simulation/production behavioral parity guarantee for those policies.

## Gaps / smells (this batch)

- `simulation_execution_engine.py`: `bar_return` and `drawdown` fields in per-bar-metrics/equity rows are hardcoded `0.0` placeholders, never computed in this engine (misleading column names in the persisted Parquet artifact).
- `simulation_execution_engine.py` `_build_trade_rows`: `slippage` column hardcoded to `0.0` even though `SimulationCostModelService` computes an exact per-fill slippage notional — the computed value is silently dropped before reaching the trade log.
- `validation_artifact_repository.py` `_persist_stress_test`/`_persist_sensitivity`: both self-documented as writing degenerate placeholder rows (`0.0` for all per-scenario Sharpe/drawdown fields; `reference_value: 0.0`) instead of the real per-scenario/per-parameter detail the upstream services actually compute — a portfolio claim of "stress test / sensitivity results persisted to Parquet" needs the caveat that only aggregate summaries currently land there.
- `simulation/services/order_simulator_service.py` appears to have no caller in the current hot path (the execution engine builds orders directly via `_construct_orders`/`SimplePositionSizer`) — possible dead/legacy code, not confirmed without a broader reference search.
- `simulation_runner.py` manifest self-documents `isolated_numpy_rng: False` — NumPy's global RNG state is not run-isolated, a latent determinism gap if any strategy/indicator uses `np.random` directly.
- Confirms part-1 finding: regime pipeline stage (`pipeline/stages/regime_stage.py`, `pipeline/aggregation/regime_aggregator.py`) remains a 0-byte stub; `RegimeAggregator`/`RegimeStage` types are referenced only in `TYPE_CHECKING` blocks (e.g. `overfitting_analysis.py` imports `StrategyRegimeProfile` under `TYPE_CHECKING`), consistent with regime *validation* logic existing (via `regime_profile` inputs in `validation_orchestrator.py`) while the regime *pipeline stage* that would produce it does not.

## Coverage

- `simulation/`: 24 of 24 files read (including 4 empty `__init__.py`).
- `pipeline/`: 12 of 12 (covered in part 1, preserved above).
- `validation/`: 10 of 10 files read.
- `black_litterman/`: 2 of 2 (covered in part 1, preserved above).
- `execution/`: 6 of 6 files read.
- `services/`: 2 of 2 (`__init__.py` empty, `research_dataset_resolver_service.py` read).
- `research/__init__.py`: 1 of 1 (empty).
- **Total: 57 of 57 files read. No files skipped.**
