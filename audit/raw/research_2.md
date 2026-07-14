# Audit: research/ part 2 (analysis, cache, calibration, checkpoints, config, experiments, intelligence, strategy_generation)

Base path: `src/autonomous_trading_platform/research/`

## Verified counts

Command:
```
cd src/autonomous_trading_platform/research && for d in analysis cache calibration checkpoints config experiments intelligence strategy_generation; do find $d -type f -name "*.py" | xargs wc -l | tail -1; done
```
Output (files counted via `find $d -type f`; all files are .py — no non-Python files present):

| Subdir | Files | LOC |
|---|---|---|
| analysis/ | 10 | 1140 |
| cache/ | 7 | 968 |
| calibration/ | 9 | 714 |
| checkpoints/ | 4 | 768 |
| config/ | 5 | 696 |
| experiments/ | 16 | 1613 |
| intelligence/ | 10 | 2513 |
| strategy_generation/ | 12 | 1311 |
| **Total** | **73** | **9723** |

TODO/FIXME/XXX: `grep -rnE "TODO|FIXME|XXX" <all 8 dirs>` → **none** (0 matches).

## Per-file entries

### analysis/__init__.py (1) & analysis/regimes/__init__.py (1)
- Purpose: Empty package markers (single blank/comment line each).

### analysis/regimes/regime_bucket.py (59 lines)
- Purpose: Defines the 5 regime dimensions (trend, volatility, liquidity, mean_reversion, risk) with 3 labels each, plus a frozen `RegimeBucket` value object.
- Notable: 5x3 = 15 fixed regime buckets; validation in `__post_init__` rejects unknown dimensions.

### analysis/regimes/regime_join_service.py (74 lines)
- Purpose: Joins persisted regime classification labels onto equity curves and trade logs.
- Notable: Explicit look-ahead-bias reasoning in docstring — exact timestamp joins only, no forward/backward fill; portfolio-level equity uses modal (most common) regime across symbols per timestamp. Genuine survivorship/leakage awareness.

### analysis/regimes/regime_metrics.py (168 lines)
- Purpose: Computes regime-conditioned performance metrics (total return, CAGR, Sharpe, Sortino, vol, max drawdown, trade stats) per regime bucket.
- Notable: Correct annualization via bars_per_year; Sortino denominator uses full-n downside deviation convention (documented as matching risk_metrics.py); max-drawdown from synthetic equity via cumprod + running peak; handles n<2 and cumulative<=0 edge cases. Broad `except (ValueError, ZeroDivisionError): pass` around trade metrics swallows errors silently.

### analysis/regimes/regime_transition_analysis.py (202 lines)
- Purpose: Detects regime label transitions in an equity curve, builds transition count matrices, episode duration stats, and pre/post-transition return + drawdown windows.
- Notable: Event-study-style +/- window analysis around transitions; O(n) episode segmentation. Timestamp lookup for windows is a linear scan per transition (`[j for j, ts in enumerate(br_timestamps) if ts == t.timestamp]`) — O(transitions x bars), fine at current scale but a smell.

### analysis/regimes/strategy_regime_profile.py (127 lines)
- Purpose: Builds per-dimension regime sensitivity scores (sharpe std/range, best/worst regime, robustness = min sharpe) and an overall StrategyRegimeProfile.
- Notable: `is_regime_robust` = min Sharpe across dimensions >= 0 with >=10 bars per bucket required for inclusion. Simple descriptive statistics, not ML.

### analysis/regimes/regime_analysis_service.py (244 lines)
- Purpose: Orchestrator: joins regimes, computes bar returns from equity curve, per-bucket metrics, sensitivity profile, transition analysis, persists artifacts.
- Notable: OpenTelemetry metrics (duration + per-dimension coverage %) and structured LogContext; warns when regime bucket coverage < 50%. Bar-return alignment comment shows care (return at i attributed to bar i+1's regime row).

### analysis/regimes/regime_analysis_result.py (56 lines)
- Purpose: Frozen result dataclass aggregating profile, transition analyses, and all bucket metrics; `summary()` for CLI output.

### analysis/regimes/regime_analysis_repository.py (208 lines)
- Purpose: Persists regime metrics, transition summaries, and strategy regime profiles to three hive-partitioned Parquet datasets.
- Notable: Casts to declared dataset schemas from `storage/parquet/datasets`; aggregates transition windows into per-(from,to) pair averages before persisting. Uses `# type: ignore[arg-type]` on list comprehensions where a typed dict would avoid it.

### cache/__init__.py (46 lines)
- Purpose: Public API re-exports for the research caching package (keys, caches, validation).

### cache/cache_identity.py (138 lines)
- Purpose: Frozen cache-key dataclasses: `StrategyGenerationCacheKey` (keyed on config_hash) and `SimulationCacheKey` (SHA-256 of canonical JSON over ~22 semantic fields), plus an 18-value `CacheInvalidationReason` StrEnum.
- Notable: SimulationCacheKey captures full lineage — dataset/universe/regime/feature versions, fill policy, latency bars, slippage config hash, commission, seed, stage/window role, calibration snapshot id, settlement days, dividend events hash. Serious reproducibility discipline; comments reference task IDs (F-06, A-02).

### cache/cache_key_builder.py (177 lines)
- Purpose: Factory functions building cache keys from StrategyConfig + SimulationRunConfig; deterministic hashing helpers.
- Notable: Canonical JSON (sorted keys, Decimals-as-strings, sorted symbols/dividend events) → 16-char truncated SHA-256 for sub-hashes. `slippage_model: Any` typed loosely (duck-typed on `model_type`/`config_summary()`).

### cache/cache_lookup_result.py (79 lines)
- Purpose: `CacheLookupResult` (hit/miss) + `CacheHitMetadata` provenance (cached run id, cached_at, source artifact, hit count) with `explain()` for debuggability.

### cache/cache_validation.py (110 lines)
- Purpose: `validate_simulation_lineage` — field-by-field compatibility check between cached and current SimulationCacheKey; first mismatch names the CacheInvalidationReason.
- Notable: Defence-in-depth against SHA-256 key_id collision. GAP: the checks list omits newer key fields — latency_bars, calibration_snapshot_id, adverse_threshold_bps, settlement_days, dividend_events_hash. Correctness is preserved because lookup is exact on key_id (which includes them), but the defence-in-depth layer lags the key schema.

### cache/simulation_result_cache.py (227 lines)
- Purpose: Thread-safe, optionally JSON-persisted, exact-match cache mapping SimulationCacheKey → prior run_id, so identical simulations are skipped.
- Notable: Lock-protected mutations; idempotent record (first run canonical); OTel counter per hit/miss; `stats()` reports "total_simulations_prevented". Smell: `_entry_to_key` on reload does not restore calibration_snapshot_id/adverse_threshold_bps/settlement_days/dividend_events_hash (defaults used), consistent with the lineage-check gap above; whole cache rewritten to disk on every mutation (O(n) per write).

### cache/strategy_generation_cache.py (191 lines)
- Purpose: Persistent dedup cache for generated strategy configs keyed on `config_hash()`; extends the engine's in-session `seen` set with cross-session provenance and hit/miss stats.
- Notable: check/record split (check does not record); `known_hashes()` returns frozenset for O(1) engine-side dedup.

### calibration/__init__.py, models/__init__.py, repositories/__init__.py, services/__init__.py (0 lines each)
- Purpose: Empty package markers (4 files).

### calibration/models/calibration_bucket.py (85 lines)
- Purpose: 3-axis calibration bucketing: policy type (market/limit/twap/vwap_lite/...), time-of-day session (open/midday/close/after-hours), order size tier; classifier functions.
- Notable: Honest DST shortcut documented in docstring — uses fixed UTC-4 offset for ET conversion, explicitly stating one-bucket misclassification near DST boundaries is acceptable for aggregation.

### calibration/models/calibration_snapshot.py (152 lines)
- Purpose: Versioned `SlippageCalibrationSnapshot` (calibrated impact coefficient, fallback min bps, max volume share) + per-bucket stats; JSON round-trip serialization.
- Notable: Derivation documented: impact_coefficient_bps = mean_slippage_bps / assumed_typical_volume_share (default 2%). MIN_CALIBRATION_SAMPLES=30. Decimal-as-string serialization avoids float drift.

### calibration/services/fill_quality_aggregator.py (132 lines)
- Purpose: Groups realized `FillQualityMetrics` SoR rows into calibration buckets; computes mean/median/p90 slippage and adverse-fill rate per bucket.
- Notable: All-Decimal arithmetic; p90 via sorted index (no interpolation — fine for the purpose); skips incomplete rows with structured-log reasons; deterministic sorted bucket output.

### calibration/services/slippage_calibration_service.py (223 lines)
- Purpose: Closed-loop calibration: realized paper-trading fill quality → sample-weighted mean/median slippage across sufficient buckets → calibrated VolumeShareSlippageModel parameters as a versioned snapshot.
- Notable: This is a genuine sim-to-real feedback loop (realized execution data recalibrates the simulator's cost model). Fallback to safe defaults with is_globally_calibrated=False when < 30 fills; coefficients clamped to [1, 500] bps to prevent overfitting to outliers. Statistical estimation, not ML — a weighted moment estimator with guardrails.

### calibration/repositories/calibration_snapshot_store.py (122 lines)
- Purpose: Thread-safe keyed store for calibration snapshots with optional JSON file persistence; load_latest by generated_at.
- Notable: Broad `except Exception` on load (logged via logger.exception). JSON-file "repository" rather than SoR-backed — lighter-weight than the platform's Postgres UnitOfWork pattern.

### checkpoints/__init__.py (29 lines)
- Purpose: Public re-exports for checkpoint/restart-plan types.

### checkpoints/research_checkpoint.py (140 lines)
- Purpose: `ResearchCheckpointIdentity` (frozen dataclass capturing full research-unit lineage: experiment/stage/task_type/pipeline/window_role/strategy/config_hash/dataset/price_basis/universe/feature datasets/regime dataset) with a SHA-256 `checkpoint_id` derived from canonical JSON, plus `ResearchCheckpoint` (mutable status/timestamps/artifact/cache linkage) with dict round-trip.
- Notable: `ResearchTaskType` enumerates 7 pipeline stage kinds (generation, simulation, walk_forward_fold, monte_carlo_trial, validation, regime_analysis, intelligence) — shows the full research pipeline taxonomy in one place. Deterministic hashing mirrors the cache-key design in `cache/cache_identity.py`.

### checkpoints/research_checkpoint_service.py (422 lines)
- Purpose: In-memory (optionally JSON-persisted) checkpoint store keyed by `ResearchCheckpointIdentity.checkpoint_id`; `run_simulation_unit` wraps a `SimulationRunnerLike.run` call with cache-lookup-first, checkpoint-status-second skip logic, marks running/completed/failed, and emits OTel duration + transition-count metrics plus structured logs on every state change.
- Notable: Genuine idempotent-restart engineering — a checkpoint identity mismatch on the same `checkpoint_id` raises `ValueError` ("Unsafe checkpoint identity mismatch") rather than silently overwriting, guarding against hash collisions corrupting resume state. `_persist()` rewrites the entire JSON store on every mutation (O(n) per write, same pattern as `simulation_result_cache.py`). `ResumeMode` (ALL/FAILED_ONLY/MISSING_ONLY) composes with `force_rerun` for restart-plan flexibility.

### checkpoints/research_restart_plan.py (180 lines)
- Purpose: `RestartPlanService.plan()` diffs a list of expected `ResearchCheckpointIdentity` units against persisted checkpoints + a cache-lookup dict, bucketing each into completed/missing/failed/cache_hit and deciding rerun vs. skip per configurable resume flags; detects "unsafe to resume" when a persisted checkpoint's identity dict differs from the requested unit's (hash reuse across mismatched configs).
- Notable: `RestartPlan.safe_to_resume` is a simple `not unsafe_to_resume_reasons` gate — a real safety check, not just an audit summary, that a caller is expected to consult before actually resuming a large experiment.

### config/__init__.py (29 lines)
- Purpose: Public re-exports for the Pydantic validated-config layer (ExperimentConfig, SimulationRunConfig, stage config models, strategy parameter validation).

### config/experiment_config.py (174 lines)
- Purpose: Frozen Pydantic `ExperimentConfig` — validates experiment_id/dataset_version/symbols/seed/cash/dates plus type-specific fields (train_ratio for TIME_SEGMENTATION, window/step days for ROLLING_WINDOW, universe_set for CROSS_UNIVERSE) before converting to the plain-dataclass `ExperimentDefinition` via `to_experiment_definition()`.
- Notable: Docstring explicitly calls out that `date.today()` defaults are rejected — dates must always be supplied, a deliberate anti-footgun for reproducible research (no silently-different backtest window depending on when the code runs).

### config/simulation_run_config.py (118 lines)
- Purpose: Frozen Pydantic `SimulationRunConfig` validating a single simulation run's strategy id/type (checked against the live strategy catalog via `strategy_type_exists`)/parameters/dataset/seed/symbols/dates/cash/settlement_days/dividend_events before a `SimulationRunRequest` is built.
- Notable: `settlement_days` field comment documents T+0/T+1/T+2 conventions; `dividend_events` documented as applying `shares_held × cash_amount_per_share` on the ex_date bar assuming split-adjusted prices — real total-return simulation mechanics, not just a stub field.

### config/stage_configs.py (337 lines)
- Purpose: Pydantic validation models (`FilterConfigModel`, `ScoringWeightsModel`, `ParallelStageConfigMixin`, `SimulationStageConfigModel`, `WalkForwardStageConfigModel`, `MonteCarloStageConfigModel`) that validate raw YAML dicts before constructing the corresponding pipeline-stage dataclasses via `to_dataclass()`.
- Notable: `WalkForwardStageConfigModel` cross-validates that `end_date - start_date >= train_days + test_days` so a misconfigured walk-forward window fails at config time rather than producing zero folds silently at runtime; `MonteCarloStageConfigModel` requires `n_runs >= 2` ("Monte Carlo to be meaningful, got {v}") — a small but real statistical-literacy guardrail.

### config/strategy_parameter_validators.py (42 lines)
- Purpose: Backward-compatible re-export shim — actual per-strategy-type parameter validators now live in `strategy/registry/validators`.
- Notable: Pure re-export, no logic; documents a refactor that moved validation logic out of research/ into strategy/.

### experiments/__init__.py, experiments/filtering/__init__.py, experiments/filtering/metrics/__init__.py, experiments/filtering/services/__init__.py, experiments/models/__init__.py, experiments/services/__init__.py (0 lines each)
- Purpose: Empty package markers (6 files).

### experiments/filtering/config.py (99 lines)
- Purpose: Frozen `FilterConfig` (pass/fail thresholds: min_sharpe, max_drawdown, min_trades, min_consistency_score, min_profit_factor, min_win_rate, max_return_variance, min_total_return, robustness_min_sharpe, robustness_min_profitable_windows) and `ScoringWeights` (w_sharpe/w_return/w_drawdown/w_consistency, validated >=0 in `__post_init__`).
- Notable: Docstrings cite internal task IDs (TASK-138/139/140) tying filter/scoring logic to a real backlog — suggests this was built incrementally against tracked requirements rather than all at once.

### experiments/filtering/filters.py (276 lines)
- Purpose: `apply_filters()` runs 8 core checks (Sharpe/drawdown/trades/consistency/profit-factor/win-rate/return-variance/total-return) then, only if all core checks pass, 2 robustness checks (per-window Sharpe floor computed by splitting the equity curve into `sm.n_windows` chunks; absolute count of profitable windows).
- Notable: `_check_robustness_per_window_sharpe` hardcodes `bars_per_year=252 * 78` (5-minute bars) inside the per-window Sharpe call rather than threading through the caller's actual bar frequency — a real inconsistency risk if this filter is ever used against daily-bar equity curves (the ratio would be annualized wrong, silently). Robustness deliberately gated behind core-pass ("no point stress-testing a strategy that already failed baseline").

### experiments/filtering/metrics/return_metrics.py (109 lines)
- Purpose: `total_return` and `cagr` (annualized on trading-bar clock via shared `BARS_PER_YEAR` constant) from an equity curve.
- Notable: Docstring is explicit that trading-day-year CAGR is ~1.45x the exponent of calendar-day CAGR and must be documented when reported externally — good methodological honesty rather than silently overstating annualized returns.

### experiments/filtering/metrics/risk_metrics.py (139 lines)
- Purpose: Sharpe, Sortino, volatility, max drawdown from bar-to-bar equity returns; `_bar_rf` converts an annual risk-free rate to a per-bar rate geometrically.
- Notable: Sortino downside deviation explicitly uses `n_total` (not `n_negative`) in the denominator, matching the empyrical/Bloomberg convention — documented rationale is "benchmark-comparable" values, correct and intentional design choice (not a bug), consistent with the `regime_metrics.py` convention noted in part 1.

### experiments/filtering/metrics/stability_metrics.py (152 lines)
- Purpose: `windowed_returns` (split equity curve into N equal chunks, chunk return), `return_variance`, `consistency_score` (fraction of profitable windows), `drawdown_recovery_times` (bars from drawdown-start to peak-recovery, excluding open episodes at simulation end).
- Notable: Simple, correct, well-documented descriptive statistics — no ML, no lookahead (recovery times require a completed episode).

### experiments/filtering/metrics/trade_metrics.py (199 lines)
- Purpose: FIFO buy/sell lot-matching (`_match_fifo`) to convert per-fill trade logs into closed round-trip PnLs, then win_rate/avg_win/avg_loss/profit_factor/largest_win/largest_loss.
- Notable: Correctly apportions fees pro-rata across partial lot closes; `safe_profit_factor` caps `inf` at 99.0 with an explicit docstring warning that raw `profit_factor` must not be passed into `statistics.mean()`/numpy aggregations — shows awareness of a real footgun (inf silently corrupting an average) rather than leaving it implicit.

### experiments/filtering/scoring.py (148 lines)
- Purpose: `score_strategy()` implements the linear composite formula (w_sharpe·sharpe + w_return·total_return − w_drawdown·|max_drawdown| + w_consistency·consistency_score) with per-term contributions retained for explainability; `rank_strategies()` sorts descending and truncates to top_n.
- Notable: Purely a weighted linear scoring rule — no ML, no learned weights; weights are static per-experiment config. Contribution breakdown per term is a nice debuggability feature (shows what drove a score).

### experiments/filtering/services/filter_score_service.py (145 lines)
- Purpose: `FilterScoreService.filter_and_rank()` batches `apply_filters` + `rank_strategies` over a list of simulation results, returning both the full pass/fail audit trail and the ranked survivors.
- Notable: `FilterScoreInput` dataclass deliberately does not import `SimulationRunResult` — comment states this keeps the dependency arrow one-way (service doesn't know about the simulation layer's result type), consistent with the layered-architecture discipline in CLAUDE.md.

### experiments/models/experiment_plan.py (47 lines)
- Purpose: `ExperimentType` enum (ab/sweep/time_segmentation/rolling_window/cross_universe) and the plain `ExperimentDefinition` dataclass carrying all fields needed to expand an experiment into simulation windows + strategy configs.

### experiments/services/experiment_orchestration_service.py (309 lines)
- Purpose: Top-level orchestrator — creates an `Experiment` SoR row, expands strategy configs (via `StrategyGenerationEngine` for parameter-space sweeps, or direct construction for AB/fixed sets), expands time windows per `ExperimentType` (train/test split, rolling walk-forward folds, cross-universe), runs each strategy/window through `SimulationRunner`, feeds results through `FilterScoreService`, and marks the experiment COMPLETED/FAILED in the repository; also supports a `staged_pipeline_config` fast path that delegates to `PipelineRunner`.
- Notable: `_rolling_windows` raises `ValueError` if zero windows are produced (window_size_days larger than range) instead of silently returning an empty experiment — fail-fast design. Broad `except Exception: mark_failed; raise` at the top level ensures experiment status is always recorded even on unexpected errors, then re-raises (doesn't swallow).

### intelligence/__init__.py (74 lines)
- Purpose: Public re-exports for the "ML-assisted research intelligence layer (TASK-2.5)": candidate ranking, overfitting estimation, regime similarity, feature vector builder, robustness prediction, strategy clustering, and the top-level orchestration service/summary.
- Notable: Module docstring itself states "All outputs are deterministic, explainable, and offline" — the package's own documentation disclaims true ML (no trained models, no black boxes) even while branding itself "ML-assisted."

### intelligence/research_feature_vector_builder.py (528 lines)
- Purpose: `ResearchFeatureVectorBuilder.build()` converts a `ValidationSummary` + `StrategyRegimeProfile` + strategy family/parameter-count metadata into a stable-ordered, normalised `[0,1]` `ResearchFeatureVector` of 35 named fields (7 validation, 9 overfitting, 4 walk-forward, 2 stress, 7 regime, 6 metadata), each carrying a `has_data` flag; missing inputs degrade gracefully to a neutral 0.5 default rather than raising.
- Notable: This is the single canonical feature-engineering layer that every other intelligence service consumes — all normalisation formulas (`_normalise_sharpe`: Sharpe 0→0.5, ±3→~1.0/~0.0; `_normalise_regime_robustness`; `_normalise_sensitivity`) are simple documented linear/affine maps, not learned scalers. `data_completeness` (fraction of fields with real data) is threaded through every downstream confidence score. `config_hash` (SHA-256, 16 chars) over strategy/experiment/dataset/field-values gives reproducibility/dedup for free.

### intelligence/candidate_ranking_service.py (319 lines)
- Purpose: `CandidateRankingService.rank()` computes a weighted linear composite score (7 components: robustness_overall, overfitting_resistance, regime_robustness, walk_forward_consistency, stress_resilience, parameter_stability, trade_reliability) over the feature vector, normalizes weights to sum to the active subset, flags weaknesses against fixed thresholds, and computes a separate "deployability_score" as an unweighted mean of 4 stricter signals.
- Notable: Explicit non-goal in module docstring: "This service does NOT promote strategies or enable trading. It is a prioritisation tool for research review" — a safety-boundary statement embedded directly in the ranking code, consistent with the platform's paper/live isolation discipline. Pure weighted-sum scoring — no regression, no learned coefficients.

### intelligence/overfitting_estimation_service.py (226 lines)
- Purpose: `OverfittingEstimationService.estimate()` aggregates 7 primary overfitting indicators (train/test degradation, fold instability, MC instability, regime concentration, parameter fragility, narrow-period alpha, low trade count) plus 2 regime-level signals into a single `overfit_probability` via weighted inversion (`risk = 1 - goodness`) and buckets it into LOW/MEDIUM/HIGH/CRITICAL risk bands with fixed thresholds (0.30/0.55/0.75).
- Notable: Module docstring states plainly "All estimation is heuristic and deterministic — no opaque ML classifiers" — this is the file that most directly answers the audit question: it is NOT machine learning (no trained model, no fitted parameters from data) but a hand-specified weighted-average risk aggregator over indicators computed elsewhere (presumably in a validation/TASK-2.4 module outside this scope). The writeup should describe this layer as "rule-based/heuristic scoring with ML-adjacent naming," not as ML.

### intelligence/regime_similarity_analysis.py (261 lines)
- Purpose: `RegimeSimilarityAnalyzer` builds a 20-element `RegimeFingerprint.fingerprint_vector` (5 dimensions × 3 labels of normalised per-label Sharpe + 5 per-dimension sensitivity scores) per strategy from a `StrategyRegimeProfile`, then computes cosine similarity between fingerprints, detects "regime specialists" (one dimension dominant by a fixed margin), and a portfolio-level `regime_diversification_score` (1 − average pairwise similarity).
- Notable: Cosine similarity and Euclidean-style vector construction are genuine (if simple) numerical techniques — hand-rolled, no external ML library, fully deterministic and inspectable. Reasonable, honest use of "similarity analysis" terminology (not oversold as ML).

### intelligence/strategy_clustering_service.py (340 lines)
- Purpose: `StrategyClusteringService.cluster()` implements single-linkage agglomerative hierarchical clustering from scratch (O(n²) full pairwise distance matrix, iterative closest-pair merge until a distance threshold or max_clusters is hit) over the 35-dim feature vectors, then flags clusters as "parameter spam" (near-duplicate parameter sweeps) via a low intra-cluster-variance threshold and detects per-cluster regime bias.
- Notable: This is a real, correctly-implemented classical unsupervised-learning algorithm (agglomerative clustering) — genuinely qualifies as ML/statistics, not just heuristic scoring, and the module docstring is explicit that it's deterministic single-linkage (no k-means random init, no stochastic elements) specifically so results are reproducible. Naive O(n²) per merge-iteration (repeated full scan for closest pair) means this doesn't scale past a few hundred candidates, but that' fine for research-batch sizes.

### intelligence/robustness_prediction_service.py (194 lines)
- Purpose: `RobustnessPredictionService.estimate()` computes a weighted average of 6 validation signals into `robustness_probability`, separately checks 5 "fragility floors" (hard per-signal minimums) that boost fragility_score regardless of the average, and classifies into SUITABLE/BORDERLINE/UNSUITABLE deployment suitability with a confidence score based on data completeness.
- Notable: Module docstring again explicit: "All estimation is statistical/heuristic — no black-box ML models." The "hard floor" fragility mechanism (any single catastrophic signal below threshold overrides an otherwise-good average) is a sensible, real risk-management pattern — prevents one bad walk-forward fold from being averaged away by good composite stats elsewhere.

### intelligence/research_intelligence_service.py (321 lines)
- Purpose: Top-level orchestrator — `analyze()` builds the feature vector then fans out to ranking/robustness/overfitting/regime-fingerprint services for one strategy candidate, producing a `ResearchIntelligenceSummary`; `rank_candidates()`/`cluster_candidates()` do the batch equivalents across many summaries, mutating `cluster_id` in place.
- Notable: Docstring explicitly disclaims: "This service does NOT: run simulations / promote strategies / enable trading / make autonomous decisions" — third instance of an explicit safety-boundary statement in this package, reinforcing that intelligence/ is a read-only research-analysis layer with no write path into execution. OTel metrics recorded per analysis (ranking score, overfit probability, cluster count).

### intelligence/research_intelligence_summary.py (70 lines)
- Purpose: `ResearchIntelligenceSummary` — a single serialisable dataclass aggregating feature vector + candidate score + robustness estimate + overfit estimate + optional regime fingerprint + (later-assigned) cluster_id, with `as_dict()` for persistence/API use.

### intelligence/research_intelligence_artifact_repository.py (190 lines)
- Purpose: Persists `ResearchIntelligenceSummary`/`StrategyCluster` batches to three hive-partitioned Parquet datasets (candidate_rankings, regime_fingerprints, cluster_assignments) using `storage/parquet/datasets` dataset descriptors.
- Notable: `_write()` manually partitions a DataFrame by column value and writes one `part-0.parquet` file per partition directory (own partitioning logic rather than calling into a shared writer helper visible in this file — worth checking storage/parquet/versioning.py for whether this bypasses the platform's central Parquet versioning helper mentioned in CLAUDE.md). `persist()` in `research_intelligence_service.py` wraps this call in try/except that only logs a warning on failure — persistence failures are non-fatal to analysis, a defensible choice for a research/analytics side-channel.

### strategy_generation/__init__.py (11 lines) & strategy_generation/generators/__init__.py (9 lines)
- Purpose: Public re-exports — engine/options/result types, and the three generator classes (Evolutionary/GridSearch/RandomSampling).

### strategy_generation/generation_result.py (107 lines)
- Purpose: `GenerationOptions` (frozen dataclass: seed, n_samples, population_size, generations, mutation_rate, include_debug/experimental, family/type allow-excludes, execution_mode, price_basis) and `GenerationSummary` (mutable counters: generated/accepted/duplicate/rejected counts, `Counter`-based rejection-reason/type/family distributions, full rejected/duplicate detail lists) plus `GenerationResult` (a `Sequence[StrategyConfig]` wrapper pairing configs with their summary).
- Notable: Every rejection and duplicate carries full parameters + generator name + reason in `*_details` lists — strong debuggability for why a candidate pool ended up smaller than requested.

### strategy_generation/compatibility.py (34 lines)
- Purpose: `strategy_is_generation_compatible()` — pure predicate gating a `StrategyDefinition` against `GenerationOptions` on 10 criteria (debug/experimental flags, family allow/exclude, strategy-type allow/exclude, intraday/daily support, raw/adjusted price-basis support), returning `(bool, reason_str | None)`.

### strategy_generation/generators/base_generator.py (27 lines)
- Purpose: `BaseStrategyGenerator` ABC — single abstract `generate()` method yielding `StrategyConfig` from a strategy_type + parameter_space + options; `last_summary` attribute for post-hoc stats.

### strategy_generation/generators/grid_search_generator.py (61 lines)
- Purpose: `GridSearchGenerator.generate()` — full Cartesian product (`itertools.product`) over sorted parameter-space value lists resolved via `ParameterSpaceResolver`; yields one `StrategyConfig` per combination, catching per-combo `ValueError` into the rejection summary rather than aborting the whole sweep.
- Notable: Exhaustive, deterministic grid sweep — exactly what the name promises, no surprises.

### strategy_generation/generators/random_sampling_generator.py (71 lines)
- Purpose: `RandomSamplingGenerator.generate()` — `n_samples` draws of `rng.choice()` per tunable parameter (seeded `random.Random`), independent per sample (no memory across draws, no replacement-avoidance/dedup at this layer — dedup happens one level up in `StrategyGenerationEngine.generate_result` via `config_hash()`).
- Notable: Simple uniform random sampling over the resolved discrete value lists — no distributional weighting, no Latin hypercube / Sobol quasi-random design; "random" here means literally `random.choice`, not a statistically-motivated sampling scheme.

### strategy_generation/generators/evolutionary_generator.py (113 lines) — KEY FINDING
- Purpose: `EvolutionaryGenerator.generate()` builds an initial "population" (registry defaults + `population_size - 1` random candidates), yields configs for all of them, then for `generations` rounds: for every member of the current population, deep-copies it and independently flips each tunable parameter to a new random choice with probability `mutation_rate`, yields the mutated child, and the *entire* mutated set (unconditionally, no culling) becomes the next generation's population.
- **This is NOT a real evolutionary algorithm.** There is no fitness function anywhere in this class or its callers within `strategy_generation/` — no backtest/Sharpe/return feedback is read before deciding what survives. There is no selection step (every parent's child always replaces it 1:1; there is no tournament/rank/elitism), no crossover/recombination between distinct parents, and no population-size pressure (each generation stays exactly `population_size` long via pure 1:1 replacement, so nothing is ever pruned). The class's own docstring is honest about this: `"""Minimal deterministic mutation-driven candidate generator."""` — it self-describes as a mutation-driven sampler, not an optimizer.
- Net effect: `EvolutionaryGenerator` is functionally a structured random-walk / random-mutation candidate generator that produces `population_size × (generations + 1)` candidate configs seeded from the registry defaults, useful for diversifying a candidate pool for later filtering/scoring/ranking (by `experiments/filtering/` and `intelligence/`) — but it performs no evolutionary search/optimization itself. Any actual "survival of the fittest" happens downstream, entirely outside this file, via `FilterScoreService`/`CandidateRankingService` on already-simulated results — this generator never sees a fitness value.
- Cross-check: `strategy_generation/composite_generation.py`'s `_selected_templates(method="evolutionary")` (see below) independently corroborates this — it also has no fitness feedback, just yields all templates plus a fixed number of purely mechanical single-field flips (voting aggregator ⇄ weighted_score aggregator) up to `options.generations`, deterministic and content-free with respect to any performance signal.

### strategy_generation/parameter_space_resolver.py (117 lines)
- Purpose: `ParameterSpaceResolver.resolve()` derives per-parameter candidate value lists from `StrategyRegistry` `ParameterSpec` metadata (tunable-only by default, unless overridden): booleans → `[False, True]`; strings → `[default]`; ints — full `range()` if `discrete` and span ≤20, else `{min, default, max}`; floats — stepped list (capped at 100 values) if `discrete` and `step` set, else `{min, mid, max}`. Validates overrides are known parameter names and within declared min/max bounds.
- Notable: This is the shared parameter-space logic used by grid, random, and evolutionary generators alike — a single source of truth for "what values are even legal/sane to try" derived from the strategy registry's declared parameter specs, not duplicated per-generator.

### strategy_generation/generators/utils.py (19 lines)
- Purpose: `make_config()` — normalizes params via `registry.normalize_parameters()`, computes `config.config_hash()`, and rebuilds the `StrategyConfig` with `strategy_id = f"{strategy_type}__{hash}"` (two-pass construction because the hash depends on the config and the id embeds the hash).

### strategy_generation/composite_generation.py (562 lines)
- Purpose: `generate_composite_rule_configs()` produces `composite_rule` strategy configs by combinatorially assembling indicator/rule/aggregator/filter "skeletons" from hardcoded module-level domain tables (`_INDICATOR_DOMAIN` classifies each of ~15 indicators as zero_centered/ratio/price_series/positive/0_100/boolean to determine which rule types are semantically valid), rather than via `ParameterSpaceResolver`/registry parameter specs.
- Notable: `_build_skeletons()` docstring documents the exact combinatorial pool size (~854 skeletons: threshold ~288, crossover ~104, comparison ~102, two-rule ~360) from which `method="random"` draws `n_samples`; `_validate_template_components()` checks every component against the live `ComponentRegistry` (executable + correct `ComponentType`) before accepting a template, so invalid combinations fail fast per-template rather than at simulation time. `method="grid"`/default yields the entire skeleton pool; `method="evolutionary"` (see cross-check above) is not fitness-driven. This is meticulously hand-curated domain knowledge (which indicator families pair with which rule types, ±10 threshold-schema bounds, DST-safe session tagging elsewhere) rather than a generic parameter sweep — the composite-rule generation path is structurally the most sophisticated of the three strategy_types-agnostic generators, but structurally, not statistically/ML sophisticated.

### strategy_generation/strategy_generation_engine.py (189 lines)
- Purpose: `StrategyGenerationEngine` — top-level facade. `generate_result()` checks `strategy_is_generation_compatible`, special-cases `composite_rule` to `generate_composite_rule_configs`, otherwise dispatches to the method-selected generator (`grid`/`random`/`evolutionary`), applies a second dedup pass on `config_hash()` (generators can themselves already skip some duplicates for composite; for grid/random/evolutionary dedup happens here), and merges the generator's own rejection summary into the aggregate `GenerationSummary`. `generate_for_family()` fans out over every strategy type in a `StrategyFamily` and merges summaries.
- Notable: Confirms `EvolutionaryGenerator` is one of three interchangeable, equally-weighted generation strategies behind a `method: str` switch — the engine treats "evolutionary" as just another sampling method alongside grid/random, with no special optimization loop, iterative-improvement callback, or fitness-driven early stopping anywhere in this orchestration layer either.

## Standout candidates

- `checkpoints/research_checkpoint.py` + `research_checkpoint_service.py` + `research_restart_plan.py`: genuine idempotent-restart engineering with hash-collision safety checks (`"Unsafe checkpoint identity mismatch"`) — the most production-grade reliability engineering seen in this batch.
- `cache/cache_identity.py`'s `SimulationCacheKey`: ~22-field lineage hash (dataset/universe/regime/feature versions, fill policy, slippage config, seed, calibration snapshot, dividend events) — serious reproducibility discipline, task-ID-referenced (F-06, A-02).
- `calibration/services/slippage_calibration_service.py`: a genuine closed-loop sim-to-real feedback mechanism — realized paper-trading fill quality recalibrates the simulator's slippage model, with sample-size gating (min 30 fills) and coefficient clamping against overfitting to outliers.
- `intelligence/strategy_clustering_service.py`: the one file in `intelligence/` that is genuinely unsupervised ML/statistics — a correctly-implemented from-scratch single-linkage agglomerative clustering algorithm (deterministic, no stochastic init) over the 35-dim feature vectors.
- `strategy_generation/composite_generation.py`: hand-curated combinatorial domain model (indicator-domain classification, threshold-schema bounds, ~854-skeleton pool) — the most bespoke/thoughtful of the three generation paths, though not ML.
- Multiple explicit safety-boundary docstrings in `intelligence/` (`candidate_ranking_service.py`, `research_intelligence_service.py`): "does NOT promote strategies / enable trading / make autonomous decisions" — consistent, repeated self-disclaiming of autonomy, matching the platform's paper/live isolation doctrine.

## Gaps / smells

- **`EvolutionaryGenerator` and composite_generation's `method="evolutionary"` path are not evolutionary algorithms** — no fitness function, no selection, no crossover, no population pressure; purely a deterministic random-mutation sampler branded "evolutionary." Any writeup or documentation calling this "genetic/evolutionary strategy search" would be overclaiming; it is candidate-pool diversification only. Actual selection happens entirely downstream (filtering/scoring/ranking on realized simulation results), decoupled from this module.
- `intelligence/overfitting_estimation_service.py` and `robustness_prediction_service.py` both explicitly self-disclaim ("no opaque ML classifiers" / "no black-box ML models") — they are hand-specified weighted-average/hard-floor heuristic scorers over indicators computed elsewhere, not fitted statistical or ML models. Only `strategy_clustering_service.py` (agglomerative clustering) and `regime_similarity_analysis.py` (cosine similarity) in `intelligence/` involve actual numerical/algorithmic technique beyond weighted sums.
- `cache/cache_validation.py`'s lineage-mismatch checklist has drifted behind `SimulationCacheKey`'s schema (missing latency_bars, calibration_snapshot_id, adverse_threshold_bps, settlement_days, dividend_events_hash) — correctness preserved by exact key_id matching, but defence-in-depth diagnostics are incomplete.
- `experiments/filtering/filters.py` hardcodes `bars_per_year=252*78` (5-minute bars) in the robustness per-window Sharpe check — silently wrong annualization if ever run against daily-bar equity curves.
- Two independent files (`cache/simulation_result_cache.py`, `checkpoints/research_checkpoint_service.py`) rewrite their entire JSON store on every single mutation — O(n) per write, a scaling smell for large experiment batches (though functionally correct).
- No non-Python files and zero TODO/FIXME/XXX across all 8 subdirectories — either genuinely finished code or debt tracked elsewhere (task IDs referenced in several docstrings point to an external backlog, e.g. TASK-138/139/140, F-06, A-02, TASK-2.5).

## Coverage: read 73 of 73 files in this batch's scope (analysis/ 10, cache/ 7, calibration/ 9, checkpoints/ 4, config/ 5, experiments/ 16, intelligence/ 10, strategy_generation/ 12). No skips.
