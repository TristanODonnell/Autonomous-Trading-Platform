# Audit: scheduler/ + backtesting/

## Verified counts

Command:
```
cd src/autonomous_trading_platform
find scheduler -type f -name "*.py" | wc -l                       # -> 52
find scheduler -type f -name "*.py" -exec wc -l {} + | tail -1     # -> 9905 total
find backtesting -type f -name "*.py" | wc -l                      # -> 7
find backtesting -type f -name "*.py" -exec wc -l {} + | tail -1   # -> 118 total
grep -rniE "TODO|FIXME|XXX" scheduler backtesting --include="*.py" | wc -l   # -> 1
grep -rn "DAG(" scheduler/airflow --include="*.py"                 # -> 4 matches
```
Output:
- `scheduler/`: **52 .py files, 9,905 LOC** (verified: **11** empty `__init__.py`, each 0 lines, incl. an empty `scheduler/services/` package — corrects the "12" figure noted earlier in this same run)
- `backtesting/`: **7 .py files, 118 LOC** (3 empty `__init__.py`)
- **Airflow DAGs: 4** (`with DAG(` in exactly 4 files): `corporate_action_ingestion_dag.py`, `market_backfill_dag.py`, `market_ingestion_dag.py`, `market_trading_dag.py`
- **TODO/FIXME/XXX: 1** — `scheduler/cycles/run_market_ingestion_cycle.py:299` `# TODO may need to change some field defaults later`
- `platform_replay` is NOT in scheduler/ scope; it lives at `src/autonomous_trading_platform/application/services/platform_replay/` (19 .py files) + `src/autonomous_trading_platform/platform/replay/platform_replay_config.py` + `src/autonomous_trading_platform/contracts/runtime/platform_replay.py` — verified below in Standouts.

## Per-file entries

**Note on repeated pattern:** Most governance cycle files (`run_drawdown_governance_ladder_cycle.py`, `run_strategy_health_lifecycle_cycle.py`, `run_strategy_health_monitor_cycle.py`, `run_governance_demotion_cycle.py`, `run_governance_promotion_cycle.py`, `run_correlation_monitoring_cycle.py`, `run_risk_budgeting_cycle.py`, `run_factor_exposure_monitoring_cycle.py`, `run_factor_neutralization_verification_cycle.py`, `run_allocation_rebalance_cycle.py`) share one template: resolve `now_utc` -> open a SQLAlchemy `session` via `get_session()` -> create a `RunManifest` (via `create_governance_manifest`) -> build a `RuntimeJobRunner` wired to `RuntimeJobRunRepository` + `PipelineFailureNotificationService` -> run the actual `*Service(session=session).run(...)` inside a nested closure with explicit `session.commit()`/`session.rollback()` and `complete_governance_manifest`/`fail_governance_manifest` bookkeeping -> wrap in `runtime_context` + OTel span + `CycleMetricSet` (runs/failures/duration) start/complete/fail lifecycle calls -> `finally: session.close()`. This is disciplined but heavily duplicated boilerplate (~100+ lines repeated per cycle with only the inner service/job-name changed) — a clear candidate for a shared `run_governance_cycle(job_name, governance_action, service_factory, input_settings)` helper. Each file also exposes a `if __name__ == "__main__":` entrypoint for manual CLI invocation. Individual entries below note only what's distinctive per file.

### scheduler/jobs/handle_ingestion_incident.py (5 lines)
- Purpose: Ingestion-incident handler stub invoked as an `on_failure_callback` from the ingestion DAGs.
- Notable: Just `print(f"[INGESTION INCIDENT] ...")` — no alerting/paging integration, no persistence. Placeholder-quality incident handling for a production trading system.

### scheduler/registry/no_overlap_lock.py (18 lines)
- Purpose: In-process, in-memory mutual-exclusion lock (a plain `set[str]`) used by the manual trigger service to prevent overlapping runs of the same scheduler job.
- Notable: Not distributed/persistent — lock state is lost on process restart and doesn't coordinate across multiple worker processes/machines. Fine for a single-process Airflow worker demo, not for real horizontal scaling.

### scheduler/backtest/backtest_config.py (21 lines)
- Purpose: Dataclass holding a simple MA-crossover backtest configuration (symbols, date range, capital, short/long window, fee/slippage rates).
- Notable: Hardcoded default `strategy_id="baseline_strategy"` and MA windows suggest this is a toy/demo backtest config, distinct from the "real" strategy configs elsewhere in `strategy/`.

### scheduler/callbacks/airflow_callbacks.py (28 lines)
- Purpose: `airflow_task_failure_callback` and `airflow_sla_miss_callback` — generic Airflow callback functions that print structured failure/SLA-miss diagnostics.
- Notable: Print-only, no metrics/alerting integration despite the rest of the codebase's heavy OTel instrumentation elsewhere (contrast with `jobs/*` which use `observability.metrics` extensively). Inconsistent observability depth between DAG-level callbacks and cycle/job-level code.

### scheduler/airflow/dags/market_trading_dag.py (37 lines)
- Purpose: Airflow DAG definition running `run_trading_cycle` every 5 minutes on weekdays via `PythonOperator`.
- Notable: Uses `Settings()` for timeout/SLA/retry config (exponential backoff), and the shared `airflow_sla_miss_callback`/`airflow_task_failure_callback`. `schedule="*/5 * * * 1-5"` runs Mon-Fri regardless of actual market hours — session/holiday filtering must happen inside `run_trading_cycle` itself (see `session_safety.py`).

### scheduler/cycles/run_trading_evaluation_cycle.py (39 lines)
- Purpose: Thin wrapper that builds a trading-cycle window/run-id/dependencies via `trading_cycle_common` and delegates to `run_trading_evaluation_job`, closing the session in `finally`.
- Notable: Much lighter-weight than the governance-cycle template above — no manifest, no OTel span, no metrics. Suggests trading-evaluation and governance cycles evolved along different conventions.

### scheduler/airflow/dags/corporate_action_ingestion_dag.py (49 lines)
- Purpose: Daily Airflow DAG (`@daily`) running `run_corporate_action_ingestion_cycle`, `max_active_runs=1`, 10-min timeout, `on_failure_callback` reports to `handle_ingestion_incident`.
- Notable: `start_date=datetime(2026, 3, 12)` — a future-dated start relative to repo history, consistent with other DAGs (see below); `retries=0` in `default_args` (no automatic retry for ingestion failures, unlike the trading DAG's exponential backoff).

### scheduler/airflow/dags/market_backfill_dag.py (49 lines)
- Purpose: Daily DAG running `run_market_backfill_cycle` (30-min timeout) for historical bootstrap/backfill.
- Notable: Same `on_failure_callback` -> `handle_ingestion_incident` pattern; same future `start_date=datetime(2026,3,12)`; `retries=0`.

### scheduler/airflow/dags/market_ingestion_dag.py (52 lines)
- Purpose: 5-minute-interval DAG (`*/5 * * * *`) running `run_market_ingestion_cycle`, 4-min execution timeout, `max_active_runs=1`.
- Notable: Only DAG among the 4 with a docstring on its failure callback. Same future `start_date` pattern as the other two ingestion DAGs.

### scheduler/registry/manual_trigger_service.py (63 lines)
- Purpose: `ManualTriggerService.trigger(job_name)` looks up a job in `SCHEDULER_REGISTRY`, checks `manual_trigger_enabled`, acquires the `InMemoryNoOverlapLock`, invokes a caller-supplied dispatcher callable, and returns a `ManualTriggerResult` (completed/skipped).
- Notable: Dispatchers are injected as a `dict[str, Callable]` rather than resolved from the registry itself — registry only supplies gating metadata (lock key, whether manual trigger is allowed), so wiring actual job functions to names happens elsewhere (likely in `interfaces/rest/routes` or a composition root not in this scope).

### scheduler/session_safety.py (67 lines)
- Purpose: `evaluate_market_session_safety()` — given a `MarketCalendar` and current UTC time, decides whether a scheduled cycle should run, delay (pre/post-market), or skip (holiday/weekend), returning a `SessionSafetyDecision`.
- Notable: Clean single-purpose module bridging Airflow's naive cron scheduling (e.g. "every 5 min Mon-Fri") with actual market-session awareness (holidays, pre/post-market). This is the piece that reconciles the DAGs' crude weekday-only cron with real trading-calendar logic — but note it's a standalone function; need to verify it's actually *called* from `run_trading_cycle.py` (checked later in this audit).

### scheduler/cycles/governance_automation_common.py (109 lines)
- Purpose: Shared helpers for governance cycles: `create_governance_manifest` (builds+persists a `RunManifest` with `RunType.GOVERNANCE`), `complete_governance_manifest`, `fail_governance_manifest`, and `_strategy_ids_from_output` (extracts strategy IDs from heterogeneous output-decision dict shapes).
- Notable: `git_commit="unknown"` is hardcoded in the manifest — governance run manifests never capture actual git provenance, unlike (presumably) research run manifests elsewhere. `capital_bucket=Decimal("0")` and `dataset_version="governance_automation"` are placeholder-like constants baked into every governance manifest regardless of which governance action ran.

### scheduler/registry/scheduler_registry.py (114 lines)
- Purpose: Static `SCHEDULER_REGISTRY` dict of `SchedulerJobDefinition` (job_name, cron, interval_seconds, manual_trigger_enabled, lock_key) for 13 named jobs (ingestion, feature pipeline, trading cycle, allocation rebalance, auto promotion/demotion, corporate actions, factor exposure/neutralization, experiment pipeline, correlation monitoring, risk budgeting, drawdown ladder, strategy health lifecycle).
- Notable: This registry is descriptive metadata only — it is NOT what actually drives Airflow scheduling (the 4 DAGs in `airflow/dags/` hardcode their own `schedule=` strings independently, e.g. market_ingestion_dag uses `*/5 * * * *` matching `interval_seconds=300` here, but most other registry entries like `strategy_allocation_rebalance_cycle` cron `"0 21 * * 1-5"` have **no corresponding Airflow DAG file** in `airflow/dags/`). This is a real gap: 13 jobs registered, only 4 have DAGs — the rest (governance/health/experiment cycles) presumably run via a different trigger path (API-triggered via `ManualTriggerService`, or an orchestrator not yet wired to Airflow) or are dead/aspirational registrations.

### scheduler/jobs/check_ingestion_readiness_job.py (128 lines)
- Purpose: Computes ingestion lag against `cycle_window.ingestion_deadline` and returns `IngestionReadinessResult(ready, safe_mode, reason)`; records lag as an OTel metric and via `record_runtime_freshness`.
- Notable: Full observability treatment (span, JobMetricSet, structured start/complete/fail lifecycle) despite being a fairly simple readiness check — good consistency with other `jobs/*` files, in contrast to the DAG-level callbacks which are print-only.

### scheduler/cycles/run_drawdown_governance_ladder_cycle.py (149 lines)
- Purpose: Governance cycle wrapping `DrawdownGovernanceService.run()` — see shared template note above.
- Notable: None beyond the shared pattern.

### scheduler/cycles/run_strategy_health_lifecycle_cycle.py (156 lines)
- Purpose: Governance cycle wrapping `StrategyHealthLifecycleService.run()`, accepts optional `rebalance_run_id` linking it to a prior allocation-rebalance run.
- Notable: None beyond the shared pattern.

### scheduler/cycles/run_strategy_health_monitor_cycle.py (156 lines)
- Purpose: Governance cycle wrapping `StrategyHealthMonitor.run()`, also accepts `rebalance_run_id`.
- Notable: Near-duplicate of `run_strategy_health_lifecycle_cycle.py` — same structure, different service. Two separate "strategy health" cycles (lifecycle vs monitor) exist as distinct scheduler entrypoints; worth confirming in application/services whether these represent genuinely different responsibilities or overlapping duplication.

### scheduler/orchestration/historical_research_golden_path_orchestrator.py (163 lines)
- Purpose: `HistoricalResearchGoldenPathOrchestrator.run()` chains backfill -> corporate-action ingestion -> feature pipeline -> optional experiment pipeline as one `RuntimeJobRunner`-tracked pipeline, resolving the latest validated `raw_bars` historical-backfill `DatasetVersions` row in between steps.
- Notable: Explicit comment "For now, feature pipeline can run on raw until adjusted-bar production is fully wired. Later switch this to adjusted_bars dataset_version_id" — a known, self-acknowledged interim shortcut (features computed on unadjusted prices rather than corporate-action-adjusted prices) baked directly into a "golden path" orchestrator.

### scheduler/cycles/run_governance_demotion_cycle.py (167 lines)
- Purpose: Governance cycle wrapping `AutoDemotionService.run()`; also exports a `run_governance_demotion_cycle()` alias that just calls the real `run_strategy_auto_demotion_cycle()`.
- Notable: Two public names for one function (`run_governance_demotion_cycle` vs `run_strategy_auto_demotion_cycle`) — the registry uses job name `strategy_auto_demotion_cycle` but this module's filename says `run_governance_demotion_cycle`; naming drift between file/module/registry.

### scheduler/cycles/run_correlation_monitoring_cycle.py (185 lines)
- Purpose: Governance cycle wrapping `CorrelationMonitoringService` — computes/persists rolling symbol/strategy/sector correlation matrices, explicitly documented as "observability only" (does not alter allocation or trading behavior).
- Notable: Accepts `sector_map`/`symbol_windows` overrides; otherwise the shared template.

### scheduler/cycles/run_governance_promotion_cycle.py (185 lines)
- Purpose: Governance cycle wrapping `AutoPromotionService.run()`; snapshots active `PromotionRulesRepository` rows into the manifest's `rules_used` for auditability. Exposes both `run_governance_promotion_cycle` (alias) and `run_strategy_auto_promotion_cycle` (real name) — same naming-drift pattern as the demotion cycle.
- Notable: Good practice — persisting the actual promotion rule thresholds (`min_sharpe`, `max_drawdown`, `min_days_tested`, etc.) used for a given run directly into the manifest gives strong governance audit trail.

### scheduler/cycles/run_risk_budgeting_cycle.py (189 lines)
- Purpose: Governance cycle wrapping `RiskBudgetingService.compute()` for risk-budgeted allocation recommendations (equal_capital / equal_risk_contribution / fixed_risk_budgets / inverse_volatility modes); explicitly documented as NOT modifying `AllocationOverrides` or the live allocation engine.
- Notable: Silently falls back to `AllocationMode.EQUAL_RISK_CONTRIBUTION` if an invalid `mode` string is passed (`except ValueError: allocation_mode = ...`) rather than raising — a caller typo silently changes behavior instead of failing loudly.

### scheduler/cycles/run_factor_neutralization_verification_cycle.py (199 lines)
- Purpose: Governance cycle that loads the latest `FactorExposureSnapshotRepository` snapshot for a portfolio and runs `FactorNeutralizationService.neutralize()` in `OBSERVE_ONLY` mode by default; builds a `FactorNeutralizationRequest` from snapshot rows via local `_request_from_snapshot`.
- Notable: Gracefully no-ops with `skipped_reason: "missing_factor_exposure_snapshot"` if no snapshot exists yet, rather than failing — sensible for a cycle chained after another monitoring cycle that may not have run yet.

### scheduler/backtest/backtest_broker_client.py (212 lines)
- Purpose: `BacktestBrokerClient` — drop-in in-memory replacement for `AlpacaBrokerClient`, implementing `get_account`/`get_positions`/`submit_order`/etc. against a synthetic cash/position ledger so the same order-flow code paths used in paper/live trading can run in backtests.
- Notable: `submit_order` always returns `status: "filled"` at the current price immediately (no partial fills, no slippage/latency modeling at the broker layer — slippage is applied elsewhere per `BacktestConfig.slippage_rate`); `cancel_order`/`close` are no-ops. This is a reasonable simplification for a deterministic backtest broker, but it means backtest fills are always 100% instant/complete regardless of order size — unrealistic for large orders.

### scheduler/cycles/run_factor_exposure_monitoring_cycle.py (216 lines)
- Purpose: Governance cycle wrapping `FactorExposureMonitoringService.run()`; if no `positions` are passed explicitly, `_load_latest_position_weights()` derives normalized weights from the latest `PositionSnapshot`/`PositionSnapshotItem` rows. Explicitly "observe-only," does not enforce factor limits.
- Notable: Consistent with `run_correlation_monitoring_cycle` and `run_risk_budgeting_cycle` — a family of "observability/advisory-only" governance cycles that compute and persist risk metrics but never act on them directly; actual enforcement presumably lives in the drawdown/demotion cycles instead.

### scheduler/jobs/run_risk_snapshot_job.py (295 lines)
- Purpose: Computes and persists a `RiskSnapshot` (gross/net exposure, leverage, symbol concentration limits) each trading cycle via `RiskSnapshotService`, then optionally runs a broader "STORY-29" risk stack (exposures, portfolio-vol targeting, pairwise strategy correlation, risk alerts, rebalance-drift suggestions) if a `risk_context` is present on `trading_cycle_dependencies`.
- Notable: `_run_story29_risk_checks` is explicitly fail-soft — "Failure is caught and logged but never re-raised: portfolio monitoring must never block the cycle or freeze trading" — a deliberate, well-documented safety choice distinguishing hard risk gating from soft monitoring. Four `_resolve_*` helper functions (`_resolve_strategy_map`, `_resolve_sector_map`, `_resolve_equity_curve`, `_resolve_strategy_returns`) are permanent stubs returning `None` with docstrings pointing at future TASK IDs (TASK-194/195/197/199) — meaning the "STORY-29 risk stack" currently runs with strategy_map=None, sector_map=None, equity_curve=None, strategy_returns=None every time in production as currently wired, silently degrading several of its checks (per-strategy/sector exposure breakdown, drawdown/vol checks, correlation checks) to no-ops. This is a real, material gap: the code path exists and is invoked every cycle, but several of its inputs are permanently unwired stubs.

### scheduler/cycles/run_allocation_rebalance_cycle.py (303 lines)
- Purpose: Governance cycle wrapping `QualityBasedReallocationService.rebalance()`; before rebalancing, checks portfolio drawdown governance (`_evaluate_portfolio_governance_for_rebalance`) to skip rebalancing if a drawdown-triggered pause is active; emits a rich set of rebalance-specific OTel metrics (lock acquired/contention, noop, skipped, turnover pct, allocation changes count) via `_emit_rebalance_stability_metrics`.
- Notable: `_evaluate_portfolio_governance_for_rebalance` explicitly "fails open" on any exception ("infrastructure errors never block rebalancing") — consistent fail-soft philosophy across governance/risk monitoring code in this codebase. Two public names again (`run_allocation_rebalance_cycle` alias vs real `run_strategy_allocation_rebalance_cycle`), matching the naming-drift pattern seen in promotion/demotion cycles.

### scheduler/orchestration/paper_trading_golden_path_orchestrator.py (304 lines)
- Purpose: `PaperTradingGoldenPathOrchestrator` with three entrypoints — `run_intraday_tick` (ingestion -> lightweight features -> trading cycle), `run` (adds corporate-action ingestion at the end), and `run_eod_maintenance` (corporate-action ingestion -> manually constructs `adjusted_bars` and `features` `DatasetVersions` rows -> feature pipeline on adjusted bars).
- Notable: `run_eod_maintenance` directly constructs and `session.add()`s `DatasetVersions` ORM rows inline inside the orchestrator (rather than through a repository/versioning helper) — this appears to bypass `storage/parquet/versioning.py`'s `generate_dataset_version` usage pattern elsewhere (it does call `generate_dataset_version()` for the ID, but hand-builds the row rather than going through a dataset-registration service) — a minor architecture-layering wrinkle per the "flow inward" rule in CLAUDE.md (orchestration reaching directly into storage model construction). Also swallows a specific `ValueError` string-matched by prefix (`"No bar data found for dataset_version_id="`) to treat "no adjusted bars produced" as a benign skip — brittle string-based exception discrimination instead of a typed exception/sentinel.

### scheduler/jobs/run_trading_evaluation_job.py (339 lines)
- Purpose: Core per-cycle trading evaluation: syncs broker equity into the portfolio engine, runs `EvaluateStrategyJob` to produce signals, fetches live positions/prices/recent closes from the broker and Parquet bar store, aggregates multi-strategy signals via `PortfolioSignalAggregator` (currently a "transparent pass-through" with one active strategy per its own comment), and calls `portfolio_construction_service.generate_order_intents()`.
- Notable: `_fetch_recent_closes` reaches three levels deep into `strategy_context.strategy_evaluation_service.context_builder.market_bar_reader` — a long attribute chain suggesting the `TradingCycleDependencies`/context object graph doesn't cleanly encapsulate this dependency (Law of Demeter violation, though pragmatic). Comment explicitly flags `PortfolioSignalAggregator` as "load-bearing as additional strategies are introduced" — i.e. currently exercised in a degenerate single-strategy configuration, a real single-strategy-in-production caveat worth noting for portfolio narrative honesty.

### scheduler/jobs/run_order_reconciliation_job.py (180 lines)
- Purpose: Reconciles tracked broker orders against broker-reported state: iterates `list_reconciliation_inputs`, calls `order_reconciliation_service.reconcile_order`, persists resulting broker-order/fill rows via `SorUnitOfWork`, records realised slippage and post-fill accounting, and logs an audit event with mismatch count.
- Notable: Converts `TimeoutError`/`ConnectionError` into a `TransientInfrastructureError` for retry classification — deliberate transient-vs-permanent failure taxonomy. Slippage-recording and post-fill-accounting failures are caught and logged as warnings rather than failing the whole job (fail-soft for secondary bookkeeping, fail-hard for the core reconciliation path) — a reasonable, deliberate resilience choice.

### backtesting/models/cost_model_config.py (14 lines)
- Purpose: Pydantic `CostModelConfig` — `fixed_commission`, `per_share_commission`, `default_half_spread`, `extra_slippage_bps`, all defaulting to `Decimal("0")`.
- Notable: **Dead code.** Repo-wide grep confirms `CostModelConfig` is never imported outside `backtesting/` itself except by `tests/smoke/test_smoke.py`, which only does a bare `import autonomous_trading_platform.backtesting` (an importability smoke check, not an exercise of the class). See consolidated finding under Standouts.

### backtesting/models/slippage_model_config.py (17 lines)
- Purpose: Pydantic `SlippageModelConfig` — `impact_coefficient_bps` (default 25 bps), `max_volume_share` (default 0.10, bounded `(0, 1]`).
- Notable: Same dead-code status as `cost_model_config.py`. Also a **name collision**: an entirely different, actually-used `SlippageModelConfig` (dataclass, `slippage_rate`-based) exists at `research/simulation/models/slippage_model.py` and is imported throughout `research/simulation/` and 15+ test files — two unrelated classes sharing an identical fully-unqualified name in two different packages, a real footgun for anyone `grep`-ing for "the" SlippageModelConfig or auto-importing via IDE.

### backtesting/services/volume_share_slippage_model_service.py (37 lines)
- Purpose: `VolumeShareSlippageModelService.estimate_slippage_cost()` — caps `quantity/bar_volume` at `max_volume_share`, scales `impact_coefficient_bps` by that capped share, converts to a dollar slippage cost.
- Notable: Dead code (never imported outside `backtesting/`), duplicating logic that the actually-used `research/simulation/models/slippage_model.py` + `SimulationCostModelConfig` (wired through `research/simulation/services/simulation_cost_model_service.py`) already cover for the real research/backtest cost path.

### backtesting/services/linear_cost_model_service.py (50 lines)
- Purpose: `LinearCostModelService.estimate_costs()` — commission (fixed + per-share) + half-spread cost + bps-based slippage, returned as a `CostBreakdown` contract.
- Notable: Dead code, same status as the other 3 `backtesting/` files. **Consolidated finding:** the entire top-level `backtesting/` package (118 LOC / 7 files, 4 non-`__init__`) has **zero production callers** — confirmed via `grep -rln "from autonomous_trading_platform.backtesting"` across the whole repo, which returns only files inside `backtesting/` itself plus `tests/smoke/test_smoke.py:5` (`import autonomous_trading_platform.backtesting`, a bare importability check with no assertions against its contents). This is not "vestigial-but-still-reachable" — it is fully orphaned, parallel-implementation dead code, superseded by the real cost/slippage models under `research/simulation/`. Directly answers the resume brief's "is `backtesting/` vestigial?" question: **yes, confirmed dead.**

### scheduler/cycles/run_market_ingestion_cycle.py (473 lines)
- Purpose: Airflow entry point for the 5-minute market-data ingestion cycle (`IngestBarsJob`); resolves the expected symbol set from the active universe (or `symbols_override`), gets/creates an "active daily incremental" `raw_bars` dataset version via `DailyDatasetVersionResolverService`, records `IngestionRun`/`RunManifest`/`RuntimeJobRun` rows.
- Notable: Contains the repo's only TODO (`# TODO may need to change some field defaults later`, line 299, on the `IngestionRun` contract construction). Exposes `symbols_override`/`enforce_lateness`/`cycle_start_override`/`cycle_end_override`/`dataset_version_id_override` specifically so `HistoricalIngestionReplayOrchestrator` and the platform backtest replay path can drive the *real* ingestion cycle over historical timestamps rather than maintaining a separate simulated ingestion path — good reuse. `floor_to_five_minutes()` here is a byte-for-byte duplicate of the same-named function in `scheduler/common/trading_cycle_common.py` — small, harmless but avoidable duplication.

### scheduler/cycles/run_market_backfill_cycle.py (420 lines)
- Purpose: Airflow entry point for historical bar backfill (`BackfillMarketBarsJob`); registers one `raw_bars` `DatasetVersion` tagged `dataset_type: historical_backfill` per run, defaults to a 30-day lookback window ending "now" if `start`/`end` not given.
- Notable: When `symbols` is not provided, falls back to a **hardcoded 8-symbol list** (`SPY, AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA`) rather than resolving the governed universe — an ad-hoc default that bypasses `UniverseMembershipService`/`UniverseResolutionService` entirely, unlike the ingestion/trading cycles which always resolve the active universe. Reasonable as a manual-trigger convenience default, but worth flagging as a spot where "which symbols traded" isn't governance-derived.

### scheduler/cycles/run_corporate_action_ingestion_cycle.py (440 lines)
- Purpose: Daily Airflow entry point for corporate-action ingestion (`IngestCorporateActionsJob`); registers both a `corporate_actions` dataset version and an `adjusted_bars` dataset version per run (adjusted bars produced as a side effect of applying splits/dividends).
- Notable: Requires a validated `raw_bars` dataset to already exist — raises `ValueError("No validated raw bars dataset version found.")` if none is found and no `source_raw_bars_dataset_version_id` is explicitly passed by a parent orchestrator (e.g. `HistoricalResearchGoldenPathOrchestrator`). `retries=0` at the DAG level (per the earlier DAG entries) means a transient failure here has no automatic retry despite this hard dependency on upstream data existing.

### scheduler/cycles/run_experiment_pipeline_cycle.py (449 lines)
- Purpose: Airflow/CLI entry point for the research experiment pipeline; accepts either a pre-built `ExperimentDefinition` or a YAML config path (`_load_experiment_definition_from_yaml`); branches between `run_staged_experiment` (multi-stage filter pipeline) and `run_experiment` (flat sweep) based on whether `staged_pipeline_config` is set.
- Notable: `_seed_governance_for_survivors` auto-inserts `StrategyGovernance` rows with `current_state="approved_research"` and `submitted_by="system"` for every staged-pipeline survivor, with no human-review gate — survivors go straight from simulation into the governance table as system-submitted "approved_research" entries. Reasonable for a research-stage-only state (not paper/live), but worth flagging for anyone reviewing how strategies enter the governance funnel: the first governance row can originate from an automated pipeline, not a person.

### scheduler/cycles/run_feature_pipeline_cycle.py (639 lines — largest cycles/ file)
- Purpose: Orchestrates 6 feature computation jobs sequentially via a local `run_step()` closure: returns, volatility, moving-average (run **twice**, windows 20 and 50), liquidity, regime, regime_classification — each wrapped individually in step-level OTel spans/metrics.
- Notable: `_validate_feature_pipeline_lineage`/`MixedLineageError` is a genuine data-integrity guardrail — enforces that a `RAW` price-basis feature run must source from a `raw_bars`/RAW dataset, and an `ADJUSTED` run must source from an `adjusted_bars`/ADJUSTED dataset that itself links back to a raw source (`source_dataset_version is not None`), preventing accidental mixing of raw and corporate-action-adjusted prices within one feature dataset lineage.

### scheduler/cycles/run_trading_cycle.py (1140 lines — largest file in scope)
- Purpose: The production live/paper trading-cycle entry point; 5 sequential steps (`ingestion_readiness` → `trading_evaluation` → `order_submission` → `order_reconciliation` → `risk_snapshot`), each with its own span/metrics/manifest `current_step`/`last_successful_step` bookkeeping.
- Notable: Genuinely defense-in-depth — checks kill-switch/freeze state, `RuntimeControlService` block reasons, portfolio-drawdown-governance pause, and per-strategy enable/disable **both at cycle start and again mid-cycle** (immediately before `order_submission` and again before `order_reconciliation`), so a freeze/kill-switch/governance-pause triggered *during* evaluation still blocks order dispatch. Three-tier exception handling by failure class: `TransientInfrastructureError` → re-raise for Airflow retry; `SafetyError`/`ExecutionError`/`PersistentInfrastructureError` → freeze trading + require manual intervention; anything else → fail closed. Also has two configurable "degraded mode" escape hatches (`skip_evaluation_on_ingestion_failure`, `hold_positions_on_evaluation_failure`) that let the cycle complete "successfully" (with `manifest.error_message` set) instead of failing hard — a deliberate but consequential policy choice about how much autonomy the scheduler has to silently degrade rather than halt.

### scheduler/jobs/run_order_submission_job.py (579 lines)
- Purpose: Per-intent submission loop: idempotency check → order-intent persist → risk/throttle gate (`OrderNotAllowedForSubmissionError`) → execution-policy transform (VWAP/TWAP/passthrough) → broker submit → order-state-machine transition → SOR persistence (broker order + synchronous fill if already `FILLED`) → optional fill-quality/slippage analytics.
- Notable: Records a full execution-latency breakdown per order (`signal_to_submit`, `submit_to_ack`, `ack_to_fill`, `total_execution`) — solid observability for execution-quality analysis. `TimeoutError`/`ConnectionError` on broker submit are converted to `TransientInfrastructureError`, matching the taxonomy used in `run_order_reconciliation_job.py`. If a strategy signals but produces zero order intents (e.g. allocation denies every symbol), the strategy runtime state is explicitly reset to `IDLE` in a best-effort `try/except: pass` block so the next tick can re-signal — a small but important state-machine correctness detail.

### scheduler/common/trading_cycle_common.py (402 lines)
- Purpose: Shared dependency/window/manifest builders for the real trading cycle: `TradingCycleWindow`, `TradingCycleDependencies`, `build_trading_cycle_window`, `build_trading_cycle_dependencies`, `_resolve_active_strategy`, `build_trading_run_manifest`, `resolve_trading_universe`.
- Notable: `_resolve_active_strategy` has an important, well-commented asymmetry — in **paper/backtest** mode, strategies in *either* `approved_for_paper_trading` **or** `approved_for_live_trading` state are eligible (a strategy that graduated to live still runs through the simulated broker in a backtest, since there's no separate live broker there), while **live** mode restricts strictly to `approved_for_live_trading`. Falls back through three layers to a `StubStrategy` (unregistered `strategy_type` → stub with correct `strategy_id`; instantiation exception → same; zero governance rows at all → generic `StubStrategy`/`baseline_strategy`) rather than ever hard-failing cycle construction on a bad/missing strategy config — deliberate fail-soft strategy resolution, consistent with the fail-soft philosophy seen elsewhere in `scheduler/`.

### scheduler/backtest/backtest_replay_orchestrator.py (348 lines)
- Purpose: `BacktestReplayOrchestrator` — legacy, self-described MA-crossover-only replay that writes synthetic fills/snapshots directly to the SOR purely to give the Portfolio/Dashboard UI plausible-looking data.
- Notable: Its own module docstring is explicit: `CLASSIFICATION: legacy ... DO NOT add new features to this path ... DO NOT use this path for strategy research or parameter sweeps. For high-fidelity historical backtesting use BacktestTradingCycleOrchestrator.` Repo-wide grep confirms this self-classification is accurate in practice: `BacktestReplayOrchestrator` is defined here and **never imported or instantiated anywhere else in the codebase** — genuinely dead/superseded code, correctly labeled as such rather than silently rotting.

### scheduler/backtest/backtest_trading_cycle_orchestrator.py (498 lines)
- Purpose: `BacktestTradingCycleOrchestrator` — the actually-used, full-fidelity backtest path (confirmed caller: `cli/commands/runtime_soak_loop.py`), mirroring the real pipeline: `run_market_backfill_cycle` → `run_feature_pipeline_cycle` → per-bar (`run_trading_evaluation_job` → simulated fill at bar close → SOR snapshots → `run_risk_snapshot_job`).
- Notable: Comment tagged `FINDING-11` documents a previously-fixed bug class: `AllocationConfig` (capital-allocation policies/overrides) is snapshotted **once** via `snapshot_allocation_config()` before the bar loop and held frozen for the entire run — specifically so a live production policy edit made *while a backtest is running* cannot leak into and corrupt that backtest's results; only `total_capital` is refreshed per-bar from the simulated broker's current equity. Per-bar strategy-evaluation exceptions are caught and treated as "skip this bar, print, continue" rather than aborting — this means a backtest can silently accumulate many skipped bars (broken evaluation) and still return a "successful" `BacktestTradingCycleResult` with no hard signal that evaluation was failing throughout the run.

### scheduler/orchestration/historical_ingestion_replay_orchestrator.py (340 lines)
- Purpose: `HistoricalIngestionReplayOrchestrator` — described in its own docstring as a "runtime-cycle debugger" that replays a historical window tick-by-tick by feeding a historical `now_utc` into the **real** `run_market_ingestion_cycle` → `run_feature_pipeline_cycle` → (optionally) `run_trading_cycle` functions, mirroring `PaperTradingGoldenPathOrchestrator.run_intraday_tick` but on a historical clock instead of the wall clock.
- Notable: `run_trading=False` by default, explicitly "avoids real order submission by default" — trading replay is opt-in and must be deliberately enabled per-run. Uses the same `RuntimeJobRunner` + `PipelineFailureNotificationService` run-tracking pattern as the governance cycles. `stop_on_failure` lets the caller choose between hard-stop-on-first-tick-failure and best-effort-continue-and-record-per-tick-errors, useful for both "does this window replay cleanly" checks and "replay through the whole window regardless" debugging sessions.

## Standout candidates

1. **`backtesting/` (top-level package, 118 LOC) is confirmed fully dead code** — not merely legacy-but-reachable. Its `CostModelConfig`/`SlippageModelConfig`/`LinearCostModelService`/`VolumeShareSlippageModelService` have zero production callers repo-wide; the only reference outside the package itself is a bare `import autonomous_trading_platform.backtesting` in `tests/smoke/test_smoke.py` with no assertions. It is a fully parallel, unused reimplementation of cost/slippage modeling — the real one (used throughout `research/simulation/` and 15+ tests) is `SimulationCostModelConfig` (`research/simulation/services/simulation_cost_model_service.py`) + a *different, same-named* `SlippageModelConfig` (`research/simulation/models/slippage_model.py`). Directly answers the resume brief's open question.
2. **`scheduler/backtest/` contains two backtest orchestrators of very different trustworthiness**, and the codebase is honest about it: `BacktestReplayOrchestrator` is explicitly self-labeled `CLASSIFICATION: legacy` / do-not-extend in its own docstring and is confirmed genuinely unused (no callers anywhere); `BacktestTradingCycleOrchestrator` is the real, actively-used (`cli/commands/runtime_soak_loop.py`) high-fidelity path that replays the actual production pipeline bar-by-bar with a documented fix (`FINDING-11`) for allocation-config leakage between live policy edits and in-flight backtests.
3. **Only 4 of 52 `scheduler/` files are wired into Airflow DAGs** (`corporate_action_ingestion_dag.py`, `market_backfill_dag.py`, `market_ingestion_dag.py`, `market_trading_dag.py`); the 13-entry `scheduler_registry.py` describes many more jobs (allocation rebalance, promotion/demotion, correlation/risk/factor monitoring, drawdown ladder, strategy-health, experiment pipeline) that have no corresponding DAG file. These presumably run via `ManualTriggerService`/API or an orchestrator not covered in this scope — worth confirming whether the missing 9 are actually scheduled anywhere in production or are effectively dormant.
4. **`run_trading_cycle.py` (1140 lines) is a genuinely well-engineered defense-in-depth state machine** — dual kill-switch/freeze/governance checks (cycle-start and mid-cycle), a 3-tier transient/persistent/unknown exception taxonomy with distinct recovery actions (retry / freeze+halt / fail-closed), and two explicit, named "degraded mode" escape hatches for ingestion and evaluation failures. This is the most defensively-coded file in the entire audited scope.
5. **The "STORY-29 risk stack" in `run_risk_snapshot_job.py`** (noted in the earlier pass) runs every cycle but 4 of its inputs (`strategy_map`, `sector_map`, `equity_curve`, `strategy_returns`) are permanent `None` stubs pointing at unimplemented `TASK-194/195/197/199` — several risk checks silently no-op in production today.
6. **The `application/services/platform_replay/` fault-injection harness** (out of this scope's directory but referenced in the resume brief) exists at `application/services/platform_replay/failure_injection.py` (456 lines) — functions like `inject_ingestion_missing_bars`/`inject_ingestion_late_bars` write synthetic incident rows into SOR tables (e.g. `MissingBarIncidents`) tagged with a `replay_run_id`, explicitly to test error-handling paths without needing real broker outages. Module docstring states injected rows are additive-only ("never deletes real data") and that production-safety is the *caller's* responsibility (no `APP_ENV` guard inside the module itself) — a soft safety boundary worth double-checking wherever these hooks are invoked from.

## Gaps/smells

- **Naming drift** across governance cycles: `run_governance_demotion_cycle.py`/`run_governance_promotion_cycle.py`/`run_allocation_rebalance_cycle.py` each export two public names (a `run_governance_*`/`run_allocation_rebalance_cycle` alias plus the "real" `run_strategy_auto_*`/`run_strategy_allocation_rebalance_cycle` function), and the scheduler registry's job names don't consistently match either the file name or the function name.
- **`floor_to_five_minutes()` is duplicated verbatim** in both `scheduler/cycles/run_market_ingestion_cycle.py` and `scheduler/common/trading_cycle_common.py`.
- **Governance-cycle boilerplate** (noted in the original pass) — ~10 files share ~100+ lines of near-identical manifest/OTel/metrics/session lifecycle scaffolding that could be a single shared helper.
- **Hardcoded/placeholder defaults baked into manifests**: `git_commit="dev"`/`"unknown"`, `capital_bucket=Decimal("10000.00")`/`Decimal("0")` appear across nearly every cycle's `RunManifest` construction regardless of actual run context — none of these manifests carry real git provenance.
- **`run_market_backfill_cycle.py`'s hardcoded 8-symbol fallback** bypasses universe governance for manually-triggered backfills.
- **Per-bar silent skip in `BacktestTradingCycleOrchestrator`** — evaluation exceptions during backtest replay are swallowed per-bar with only a `print()`, so a systematically broken strategy could produce a "completed" backtest result with most bars silently skipped.
- **Auto-seeded governance rows** (`run_experiment_pipeline_cycle.py`) insert `approved_research` `StrategyGovernance` rows with `submitted_by="system"` with no human gate — appropriate for the research stage but worth flagging in any governance-integrity narrative.

## Coverage: read 45 of 59 (+ 14 skips)

- **45 of 45 non-`__init__.py` files** in scope were read in full across this and the prior two passes: 41 in `scheduler/` (52 total − 11 empty `__init__.py`) + 4 in `backtesting/` (7 total − 3 empty `__init__.py`).
- **14 skipped**: all are `__init__.py` files verified via `wc -l` to be exactly 0 lines each (11 in `scheduler/`: `scheduler/__init__.py`, `scheduler/airflow/__init__.py`, `scheduler/airflow/dags/__init__.py`, `scheduler/backtest/__init__.py`, `scheduler/callbacks/__init__.py`, `scheduler/common/__init__.py`, `scheduler/cycles/__init__.py`, `scheduler/jobs/__init__.py`, `scheduler/orchestration/__init__.py`, `scheduler/registry/__init__.py`, `scheduler/services/__init__.py`; 3 in `backtesting/`: `backtesting/__init__.py`, `backtesting/models/__init__.py`, `backtesting/services/__init__.py`) — no content to audit, including no re-exports.
- Airflow DAG count independently re-verified during this pass: **4** (`corporate_action_ingestion_dag.py`, `market_backfill_dag.py`, `market_ingestion_dag.py`, `market_trading_dag.py`), consistent with the header count above.
