# Audit: universe/ + ingestion/ + feature_engineering/

## Verified counts

Command:
```
for d in universe ingestion feature_engineering; do find src/autonomous_trading_platform/$d -name '*.py' | wc -l; find src/autonomous_trading_platform/$d -name '*.py' | xargs wc -l | tail -1; done
```
Output:
- `universe/`: **28 files, 4,407 LOC** (wc -l total)
- `ingestion/`: **29 files, 2,824 LOC**
- `feature_engineering/`: **27 files, 2,127 LOC**

TODO/FIXME/XXX markers:
```
grep -rniE 'TODO|FIXME|XXX' src/autonomous_trading_platform/{universe,ingestion,feature_engineering} | wc -l
→ 0
```

Empty `__init__.py` files (0–1 lines) are listed but not given full entries: universe has 4, ingestion has 10, feature_engineering has 5.

---

## universe/

### src/autonomous_trading_platform/universe/types.py (127 lines)
- Purpose: Shared universe dataclasses/protocols: `UniverseResolutionMode` enum (fixed version / active-as-of / historical rotation replay / custom), `ExperimentUniverseScope` (PIT-anchored), `UniverseTransition`, `RawSymbolRecord`, `CandidateGenerationConfig`, `UniverseRebalanceConfig`.
- Notable: Rebalance defaults are documented with rationale in comments (target size 20 "optimal for $1M capital ~$50K/position", 30% churn cap, retain-until-rank>30 / add-only-if-rank<=20 hysteresis band). `CandidateGenerationConfig` defaults: min price $1, min ADDV $5M, max 30% missing bars, 500 max symbols, explicit ranking weights (0.5 dollar-vol, 0.2 consistency, 0.2 completeness, -0.1 volatility).

### src/autonomous_trading_platform/universe/services/universe_rotation_service.py (608 lines)
- Purpose: Atomic universe rotation: resolve latest CANDIDATE version → propose rebalance → pre-activation safety validation → retire previous ACTIVE → activate new → persist `UniverseRotationRecord` audit row. Also `rollback()` to a prior version.
- Notable: CONFIRMS the claimed flow. `_validate_pre_activation` (pure, module-level) checks: non-empty membership, no duplicate symbols, symbol regex `^[A-Z][A-Z0-9._-]*$`, size bounds [1, 10,000], churn <= max_churn_pct unless `force_rotation`. Config-hash idempotency: if `proposed.config_hash == active.config_hash` and not forced, rotation is *skipped* with a `status="skipped"` rotation record (reason `config_hash_unchanged`) — the skip is still audited. Rollback does NOT mutate the target version; it builds a brand-new PROPOSED version copying the target's included members forward (`included_reason="rollback"`, name `rollback_<id8>_<date>`), preserving version immutability and the audit trail; rollback bypasses churn checks (force_rotation=True, churn=1.0). Structured step-by-step logging (version_inserted / members_inserted / active_retired / version_activated) plus rotation metrics. Smell: "atomic" relies on all steps sharing one SQLAlchemy session/transaction — there is no explicit transaction demarcation in this service (commit happens in the calling job); the `getattr(self, "_session", None)` flush guard is a test-fixture accommodation leaking into prod code (noted in recent commit history). Skipped rotation records are built but NOT inserted via `_rotation_repo.insert_record` in the skip path (record returned in result only — verify: insert_record is only called in completed paths, lines 282/400).

### src/autonomous_trading_platform/universe/services/universe_rebalance_service.py (540 lines)
- Purpose: Pure rebalance proposal engine: diffs active membership against a ranked candidate version and produces a PROPOSED UniverseVersion + members + `UniverseRebalanceRun` audit row. Two modes: bootstrap (no active universe) and hysteresis.
- Notable: Real hysteresis band to prevent thrashing: retain a holding until its candidate rank degrades past `retain_until_rank_greater_than` (30), only add new symbols ranked <= `add_only_if_rank_less_than_or_equal` (20). Churn budget = `int(max_churn_pct * active_size)`; mandatory removes (excluded from candidate / not in pool — i.e., delisted or degraded symbols) consume budget first, then optional removes worst-rank-first, then adds best-rank-first; overflow tracked as skipped_*_churn_limit. Removed symbols are persisted as member rows with `excluded_reason` (`removed:not_in_candidate_pool`, `removed:rank_degraded`, `removed:target_size_exceeded`) for auditability. Config hash = SHA-256 over sorted symbols + the four rebalance knobs (this is what makes rotation idempotent). Rich `audit_summary` JSON with top-10 lists per decision category.

### src/autonomous_trading_platform/universe/services/universe_candidate_builder.py (710 lines)
- Purpose: Candidate universe generation: pulls active tradable symbols from the raw market pool snapshot as-of a timestamp, computes per-symbol liquidity/quality metrics from 5-minute bars (Postgres MarketBar or versioned Parquet via `dataset_version_id`), filters, scores, ranks, and emits a CANDIDATE UniverseVersion with full per-symbol lineage.
- Notable: Point-in-time correctness: pool symbols and bars both bounded by `config.as_of`; window = as_of - 3x lookback calendar days to cover ~lookback trading days. Filters: min history days, min price, min ADDV, max missing-bar pct (bar completeness vs 78 expected 5-min bars/day = 6.5h x 12). Score = weighted percentile-rank composite (dollar-volume percentile + trading consistency + bar completeness - annualized close-to-close volatility percentile, stdev * sqrt(252)). Rejected symbols persisted as excluded members with reasons (`no_market_data`, `insufficient_history`, `price_below_minimum`, `insufficient_liquidity`, `excessive_missing_bars`, `beyond_max_universe_size`). Decimal arithmetic for money. Smells: `logger.disabled = False; logger.propagate = True` mutation at top of build_candidate (test-suite workaround); broad `except Exception` in `_fetch_parquet_rows` silently degrades to empty rows (symbol then rejected as no_market_data).

### src/autonomous_trading_platform/universe/services/universe_validation_service.py (296 lines)
- Purpose: Deep universe validation at three levels: contract-level (`validate` runs declarative rule sets from contracts/validators), row-level (`validate_version_row` adds member-count-vs-metadata and ACTIVE/effective_to consistency), and system-level (`validate_active` asserts exactly one active version, no effective-window overlaps).
- Notable: Checks unique ranks, liquidity lineage present for non-custom sources, symbols exist in the bars dataset, and symbols not inactive/delisted in the raw pool — a live survivorship/lifecycle check at validation time. Smells: several broad `except Exception: return []` fallbacks (missing table treated as "no problem"), duck-typed `getattr(rule_result, "violations", [])`, and a raw-SQL fallback path for the symbol-existence query.

### src/autonomous_trading_platform/universe/jobs/run_candidate_generation.py (106 lines)
- Purpose: CLI/job entry point wiring `UniverseCandidateBuilder` to persistence — builds a CANDIDATE version, persists included+excluded members, commits, and detaches objects (`session.expunge_all()`) so the result survives session close.
- Notable: `dry_run` rolls back instead of committing. `_materialize_result` forces attribute access on ORM objects before expunge to pre-load lazy attributes into `__dict__` — a deliberate pattern to avoid `DetachedInstanceError` after `session.close()`.

### src/autonomous_trading_platform/universe/jobs/run_raw_market_pool_refresh.py (123 lines)
- Purpose: Job that refreshes the raw tradable-symbol pool from Alpaca's broker `TradingClient.get_all_assets()`, gated by `MarketCalendarService.should_refresh_today` cadence check unless `force=True`.
- Notable: `AlpacaRawSymbolProvider` defined inline, implementing the `RawSymbolProvider` protocol used by `RawMarketPoolRefreshService`. Broker credentials pulled from `Settings`; `paper=` flag derived from `TradingEnvironment`. `dry_run` support via `session.rollback()`.

### src/autonomous_trading_platform/universe/jobs/run_rebalance.py (79 lines)
- Purpose: Job that proposes a rebalance from the latest CANDIDATE version against the currently ACTIVE version via `UniverseRebalanceService`, persisting the proposed version/members/rebalance-run unless `dry_run`.
- Notable: Auto-resolves `candidate_version_id` (latest candidate) and `active_version_id` (current active as-of `now_utc`) when not explicitly supplied; raises if no candidate versions exist yet.

### src/autonomous_trading_platform/universe/jobs/run_universe_rollback.py (60 lines)
- Purpose: Thin job wrapper around `UniverseRotationService.rollback()` — commits/rolls back the session and re-raises on exception after rollback.
- Notable: Confirms rollback-as-new-version-copy design at the job layer too; `dry_run` explicitly rolls back after computing the result rather than skipping the DB write path.

### src/autonomous_trading_platform/universe/jobs/run_universe_rotation.py (93 lines)
- Purpose: Scheduled rotation entry point — cadence gate (`should_rotate`: daily always, weekly only Mondays) then delegates to `UniverseRotationService.rotate()`.
- Notable: Builds its own session via `sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)` rather than using the shared `get_session()` helper used by sibling jobs — inconsistent session-construction pattern across the jobs/ directory (minor smell). `force_rotation` and `skip_cadence_check` both exposed for manual override.

### src/autonomous_trading_platform/universe/jobs/run_universe_selection_cycle.py (112 lines)
- Purpose: End-to-end one-shot cycle: cadence check → build candidate → guard against zero-member result → row-level validation → insert version/members → retire old ACTIVE → activate new, all in one job (distinct from the propose/rotate two-phase flow used elsewhere).
- Notable: This is a *direct* activation path that bypasses `UniverseRotationService`'s pre-activation churn/format/size validation entirely — it only calls `UniverseValidationService.validate_version_row` (structural checks), not `_validate_pre_activation`'s churn-cap/regex/size-bound checks. This is an inconsistency: two different "select and activate" code paths exist (`run_universe_selection_cycle` vs. `run_universe_rotation` → `UniverseRotationService.rotate`) with different safety-check coverage.

### src/autonomous_trading_platform/universe/providers/alpaca_screener_provider.py (175 lines)
- Purpose: `RawSymbolProvider` implementation that screens all active/tradable NYSE/NASDAQ/ARCA/BATS/AMEX US equities by trailing average dollar volume using Alpaca daily bars, for PIT-correct historical screening (usable for any past `as_of` date without lookahead).
- Notable: Batches symbol requests (default 200/batch) against Alpaca's IEX feed; per-batch failures are swallowed (`except Exception: continue`) as "partial coverage is acceptable" — a deliberate but silent degradation (no logging of which batches failed). Regex `^[A-Z]{1,6}$` filters out non-standard tickers (warrants, units, etc. with dots/hyphens) — narrower than the candidate builder's `^[A-Z][A-Z0-9._-]*$`, i.e., two different symbol-format definitions coexist in the codebase.

### src/autonomous_trading_platform/universe/services/experiment_universe_resolver.py (188 lines)
- Purpose: Resolves an `ExperimentUniverseScope` for a research experiment given its `ExperimentDefinition`, supporting four resolution modes (FIXED_UNIVERSE_VERSION, ACTIVE_AS_OF_START_DATE, HISTORICAL_ROTATION_REPLAY, CUSTOM), with legacy string-tag inference (`"uvid:<id>"`, `"replay"`) for backward compatibility.
- Notable: Every code path returns a PIT-anchored scope (`effective_timestamp` always set to experiment start, never "now") — directly enforces the "no mode resolves to live universe without explicit anchor" invariant claimed in the architecture. Raises `UniverseResolutionError` (not silent fallback) when a fixed version ID doesn't exist or a replay window has zero transitions.

### src/autonomous_trading_platform/universe/services/historical_universe_filter_service.py (43 lines)
- Purpose: Thin composition of `UniverseMembershipService` + optional `TickerLifecycleService` to produce a deduplicated, lifecycle-resolved symbol list for a given evaluation date, and to filter an arbitrary symbol list down to that active set.
- Notable: Simple pass-through/filter facade; no independent logic beyond delegation and set dedup.

### src/autonomous_trading_platform/universe/services/market_calendar_service.py (123 lines)
- Notable: `StaticNYSECalendarProvider` hardcodes NYSE holiday and early-close dates for 2025-2026 only (`_NYSE_HOLIDAYS`, `_NYSE_EARLY_CLOSES` frozensets) — this is a code smell / maintenance liability: the calendar will silently go stale/wrong for 2027+ without a code change (no algorithmic holiday calculation, e.g. no Easter/Thanksgiving-formula derivation). UTC market-open/close times are fixed approximations that do not adjust for DST (`_MARKET_OPEN_UTC`/`_MARKET_CLOSE_UTC` comments explicitly say so) — a real bug source twice a year (is_market_open_now would be off by an hour during one DST transition window depending on which side of the change).
- Purpose: Trading-day/holiday/early-close calendar plus cadence gating (`should_refresh_today`, daily/weekly) used by universe refresh/rotation jobs.

### src/autonomous_trading_platform/universe/services/raw_market_pool_refresh_service.py (215 lines)
- Purpose: Refreshes the raw tradable-symbol pool: fetches from a `RawSymbolProvider`, normalizes/dedupes (uppercases, strips), diffs against the previous complete snapshot to detect new/delisted symbols, persists a new immutable snapshot + membership rows, and upserts a `RawMarketSymbol` master record (`first_seen`/`last_seen` tracking) per symbol.
- Notable: This is the actual survivorship-bias-elimination substrate: `RawMarketSymbol.first_seen` is preserved across upserts (only set on first insert) so downstream logic can tell when a symbol entered the pool; `delisted_symbols` diff (previous minus current) is the mechanism candidate/rebalance logic uses to identify departures. `preview()` mirrors `refresh()` without any writes (dry-run), duplicating a fair amount of diff logic between the two methods (minor DRY smell).

### src/autonomous_trading_platform/universe/services/survivorship_guard.py (113 lines)
- Purpose: Pre-simulation validation raising `SurvivorshipBiasError` for unsafe experiment universe configurations — no resolved universe for ACTIVE_AS_OF_START_DATE, empty replay-transition window, or missing PIT anchor; plus `validate_symbols_not_future_leaked` to catch symbols that postdate the universe's effective timestamp.
- Notable: This is a distinct, purpose-built guard class (separate from `ExperimentUniverseResolver` and `UniverseValidationService`) whose sole job is survivorship-bias prevention — strong evidence the "survivorship bias elimination" claim is a first-class, explicitly named concern in the design, not just an incidental side effect of PIT-anchoring elsewhere. `validate_symbols_not_future_leaked` requires the caller to supply `known_future_symbols` externally (from corporate-action/IPO records) — the guard itself doesn't look these up, so its effectiveness depends on callers wiring this correctly (not verified elsewhere in this batch whether any caller actually does).

### src/autonomous_trading_platform/universe/services/ticker_lifecycle_service.py (99 lines)
- Purpose: Resolves a symbol through rename/merger/successor chains as of a given timestamp (`resolve_symbol_chain`), detects delisting (`is_delisted`), and cleans a universe symbol list by replacing renamed tickers and dropping delisted ones (`resolve_universe_symbols`).
- Notable: `resolve_symbol_chain` guards against infinite loops via a `seen` set (cycle detection) — defensive coding for potentially malformed lifecycle-event chains. This is the core symbol-lifecycle machinery referenced by the audit brief; ties directly into corporate-actions data (renames/mergers) for point-in-time correct symbol resolution during backtests.

### src/autonomous_trading_platform/universe/services/universe_history_service.py (133 lines)
- Purpose: Point-in-time historical universe lookup — active version as-of a timestamp, versions overlapping a window, and `build_replay_transitions` which reconstructs an ordered added/removed/retained symbol diff sequence across rotations for `HISTORICAL_ROTATION_REPLAY` mode.
- Notable: Confirms `UniverseTransition` construction logic — first entry in a window has no previous universe so `added_symbols` = all initial members, `removed`/`retained` empty; subsequent entries diff consecutive timeline entries. All methods take explicit `start`/`end`/`as_of` — no implicit "now" defaults, consistent with the PIT-anchoring invariant.

### src/autonomous_trading_platform/universe/services/universe_membership_service.py (51 lines)
- Purpose: Given a calendar date, resolves the active universe version and returns its member symbols, optionally passed through `TickerLifecycleService` resolution.
- Notable: Thin, no independent logic; distinguishes "raw" vs "resolved" (lifecycle-adjusted) symbol sets as two explicit methods rather than always resolving, letting callers opt in.

### src/autonomous_trading_platform/universe/services/universe_resolution_service.py (157 lines)
- Purpose: Central "single source of truth" service for resolving the active universe across all pipelines (trading, ingestion, features, simulation, replay) — `resolve_active` (raises `NoActiveUniverseError` if none), `resolve_active_or_none`, `resolve_active_members`, `resolve_for_simulation_window` (uses window *start*, not end, deliberately, "reflects what was known before the window began").
- Notable: Emits OTel-style structured logs (`LogContext`) and metrics (`record_universe_active_size`, `record_universe_resolution_latency`) on every resolution — the only universe service in this batch with first-class observability instrumentation. `assert_active_universe_exists` double-checks both existence and `status == ACTIVE` (defense against a version existing but somehow not active).

### src/autonomous_trading_platform/universe/services/universe_selection_service.py (66 lines)
- Purpose: Thin facade returning `(symbols, metadata)` tuples over `UniverseCandidateBuilder`, for callers that want a simple symbol-list result rather than the full `CandidateBuildResult` object graph.
- Notable: Explicitly documented as delegating all logic to the builder ("no duplicate selection path lives here") — good practice given how many other places in this codebase construct `CandidateGenerationConfig` and call the builder directly (jobs, this service, `run_universe_selection_cycle`) — at least the scoring logic itself isn't reimplemented.

### src/autonomous_trading_platform/universe/services/universe_snapshot_service.py (95 lines)
- Purpose: Builds/saves `UniverseSnapshot` rows (symbol list + SHA-256 version hash + criteria dict) via `UniverseSnapshotRepository`.
- Notable: File header explicitly marks this **DEPRECATED**, superseded by `UniverseVersionService`/`UniverseVersionRepository`. Confirms the codebase evolved from a simpler snapshot model to the richer versioned-rotation model described in the audit brief; the old model is still present in the tree (dead code risk / drift potential if anything still imports it — worth a grep in a later pass but out of scope here).

### src/autonomous_trading_platform/universe/services/universe_version_service.py (114 lines)
- Purpose: The successor to `UniverseSnapshotService` — builds `UniverseVersion` + `UniverseMember` rows with SHA-256 `config_hash` over the normalized symbol list, and `build_and_activate_version` which retires the current active version and activates the new one in one call (a simpler, non-audited alternative to `UniverseRotationService.rotate`).
- Notable: `build_and_activate_version` performs retire→insert→activate with **no pre-activation validation and no rotation-audit record** — a third, even-thinner "activate a universe" code path (alongside `run_universe_selection_cycle` and `UniverseRotationService.rotate`) with the weakest safety coverage of the three. Whether this is dead/legacy code or still wired into a live job would need a cross-reference grep (not done in this pass — flagged as a gap).

---

## Coverage note (universe/)
All 28 files in `universe/` now read: 4 `__init__.py` (empty, skipped per header) + 24 substantive files (5 from prior session + 19 this session). 28/28 complete.

---

## ingestion/

### src/autonomous_trading_platform/ingestion/corporate_actions/clients/alpaca_corporate_action_client.py (70 lines)
- Purpose: `fetch_corporate_actions()` — raw HTTP GET against Alpaca's `/v1/corporate-actions` REST endpoint (not the SDK) via `httpx`, auto-paginating via `next_page_token` until exhausted, merging `cash_dividends` + `reverse_splits` across pages.
- Notable: Only these two corporate-action categories are fetched/merged even though the normalization service (below) maps 8 action types (stock dividends, spinoffs, mergers, name changes) — the client silently never surfaces those other types since it only reads `cash_dividends`/`reverse_splits` keys from the response block. Synchronous `httpx.get` with no retry/backoff logic; `raise_for_status()` on transient failure just propagates.

### src/autonomous_trading_platform/ingestion/corporate_actions/jobs/ingest_corporate_actions_job.py (174 lines)
- Purpose: Job wrapper: runs `CorporateActionIngestionService.ingest_corporate_actions()`, then if any bars were adjusted, writes them to the `ADJUSTED_BARS_DATASET` Parquet dataset and registers a new `DatasetVersion` row (source=`corporate_action_adjustment`, price_basis=ADJUSTED) linking back to the source raw-bars dataset version.
- Notable: Standard job-lifecycle instrumentation (start/complete/fail metrics + OTel spans), consistent with the pattern seen in universe/ jobs and market_data jobs. `date_coverage_start`/`end` both pinned to `cycle_timestamp.date()` regardless of the actual adjusted-bar date range — a minor metadata-accuracy smell (coverage window on the registered dataset version doesn't reflect the true span of adjusted data if `fetch_start`/`fetch_end` span multiple days).

### src/autonomous_trading_platform/ingestion/corporate_actions/services/corporate_action_adjustment_service.py (155 lines)
- Purpose: Applies a corporate action's price/volume adjustment to a list of raw `MarketBar`s: multiplicative factor (`1/split_ratio`) for forward/reverse splits, additive subtraction of `cash_amount` for cash dividends; bars on/after `action.effective_date` pass through unadjusted.
- Notable: Real, correct adjustment math — Decimal arithmetic throughout, split factor rounds volume with `ROUND_HALF_UP`, rejects zero/negative split ratios and negative/None cash amounts. Only SPLIT_FORWARD/SPLIT_REVERSE/CASH_DIVIDEND are handled; any other `CorporateActionType` (stock dividend, spinoff, merger, name change — all of which the normalization service can parse) raises `ValueError` in `apply_action_to_bars`, so if such an action ever reaches this service the whole ingestion of that action aborts (caught upstream by the ingestion service's broad `except Exception` around the adjustment step, which re-raises after recording a failure metric — so it's a hard failure, not a silent skip). Adjusted bar gets a new `bar_id` computed via `build_bar_id(..., price_basis=PriceBasis.ADJUSTED)`, distinct from the raw bar's ID — correct dual-track raw/adjusted bar identity.

### src/autonomous_trading_platform/ingestion/corporate_actions/services/corporate_action_ingestion_service.py (361 lines)
- Purpose: End-to-end corporate-action ingestion: fetch from Alpaca client → per-action normalize → validate → `upsert` into SoR (idempotent — `result.created` gates downstream processing so re-ingesting the same action is a no-op past the upsert) → if split-type, pull pre-effective-date raw bars from the Parquet `ParquetBarRepository` and adjust them.
- Notable: Fine-grained OTel spans per action (normalize/validate/persist) plus a rich set of counters (`corporate_action_records_processed`, `*_normalization_failures`, `*_validation_failures`, `adjustments_applied`, `adjustment_failures`, `affected_bars_per_action`). Normalization failures and validation failures are caught and logged via `audit_logger.record_*` then `continue` (soft-skip per bad record, doesn't abort the whole batch) — but adjustment failures during the split-bar-rewrite step are NOT soft-skipped: the `except Exception` block records metrics/audit then `raise`s, aborting the entire ingestion run on one bad adjustment. Inconsistent failure-handling posture within the same method (skip vs. abort) depending on which stage fails.

### src/autonomous_trading_platform/ingestion/corporate_actions/services/corporate_action_normalization_service.py (101 lines)
- Purpose: Maps Alpaca's raw corporate-action JSON dict to the platform's `CorporateAction` contract; maps 8 provider type strings (`cash_dividend`, `stock_dividend`, `forward_split`, `reverse_split`, `spin_off`, `cash_merger`, `stock_merger`, `name_change`) to `CorporateActionType` enum values; computes `split_ratio = new_rate/old_rate` for splits.
- Notable: Requires `id`, `symbol`, `ex_date` present and non-blank or raises `ValueError`; raises (not defaults) on zero `old_rate` division and on non-numeric `cash`/rate fields — fails loudly rather than silently coercing bad data. As noted above, the *client* only ever fetches `cash_dividend`/`reverse_split` raw payloads in practice, so 6 of these 8 mapped types are effectively dead code paths under the current client (would only activate if the client were extended).

### src/autonomous_trading_platform/ingestion/corporate_actions/services/corporate_action_validation_service.py (21 lines)
- Purpose: Thin wrapper delegating to `contracts.validators.corporate_action.CORPORATE_ACTION_RULES` via the shared `run_rules` engine; `_validate_complex_checks` is an explicit no-op stub ("add checks here that are too awkward for rules") — placeholder for future extension, not currently exercised.
- Notable: No independent logic; all validation weight lives in the declarative rule set (not read in this pass — out of scope per audit brief's file list, contracts/ is a separate directory).

### src/autonomous_trading_platform/ingestion/helpers/bar_identity.py (18 lines)
- Purpose: `build_bar_id()` — deterministic SHA-256 hash over `symbol:interval:price_basis:timestamp.isoformat()`, giving every bar a content-addressed, idempotent ID (re-ingesting the same bar always produces the same ID → natural dedup key for upserts).
- Notable: This is the core idempotency primitive referenced across every bar-producing service in this batch (aggregation, ingestion, corporate-action adjustment).

### src/autonomous_trading_platform/ingestion/helpers/session.py (22 lines)
- Purpose: `classify_market_session()` — buckets a UTC timestamp (converted to America/New_York) into PREMARKET (4:00–9:30), REGULAR (9:30–16:00), POSTMARKET (16:00–20:00), or OVERNIGHT.
- Notable: Uses `zoneinfo` (proper DST-aware IANA tz conversion), so this session classifier — unlike `universe/services/market_calendar_service.py`'s hardcoded UTC offset constants — correctly handles DST transitions. Inconsistency across the codebase: two different approaches to ET/UTC conversion exist (proper zoneinfo here vs. fixed-offset approximation in universe's calendar service).

### src/autonomous_trading_platform/ingestion/market_data/clients/alpaca_historical_bars_client.py (37 lines)
- Purpose: Thin wrapper over Alpaca SDK's `StockHistoricalDataClient.get_stock_bars` for minute-bar historical fetch, used by the backfill path; generator that flattens the per-symbol response dict into a single bar stream.
- Notable: No pagination/chunking logic visible here — relies on the SDK client to handle Alpaca's response pagination internally (not verified in this pass).

### src/autonomous_trading_platform/ingestion/market_data/clients/alpaca_market_data_client.py (61 lines)
- Purpose: Module-level functions for constructing Alpaca live (`StockDataStream`) and historical (`StockHistoricalDataClient`) clients from `Settings` credentials, plus `fetch_minute_bars()` used by the incremental (non-backfill) ingestion job.
- Notable: `_get_credentials()` raises `ValueError` if API key/secret missing rather than silently proceeding — fail-fast credential check. No retry/backoff around `get_stock_bars` call.

### src/autonomous_trading_platform/ingestion/market_data/jobs/backfill_market_bars_job.py (138 lines)
- Purpose: Job wrapper: runs `MarketBackfillService.backfill()` then `DatasetVersionFinalizationService.finalize_backfill_dataset_version()`; raises `RuntimeError` if the finalized dataset's `validation_status` isn't `"validated"` (e.g., stayed `"incomplete"` because not all checkpoints completed) — turns a soft validation-state into a hard job failure.
- Notable: Standard job-lifecycle metrics/spans pattern shared across this batch.

### src/autonomous_trading_platform/ingestion/market_data/jobs/ingest_bars_job.py (320 lines)
- Purpose: Incremental (live/near-real-time) 5-minute bar ingestion job: fetches minute bars from Alpaca for expected symbols, feeds them through `BarIngestionService` (1-min → 5-min aggregation), tracks per-cycle received-vs-expected symbol coverage, writes completed 5-min bars to the `RAW_BARS_DATASET` Parquet dataset, records `SymbolDateCoverage`/`MissingBarIncidents` rows, and raises on SLA breach.
- Notable: CONFIRMS the SLA claim: `missing_ratio = len(missing_symbols) / len(expected_symbols)`; if `> 0.2` (20%), an `audit_logger.record_sla_breach` is emitted and, when `enforce_lateness=True` (live mode), a `RuntimeError` aborts the cycle — but in historical-replay mode (`enforce_lateness=False`) the same threshold is deliberately NOT enforced, with an explicit comment explaining why (symbols arrive sequentially per-symbol during replay, so early cycles always look artificially incomplete). Cycle-level idempotency via `IncrementalIngestionCheckpointService` keyed by `{dataset_version_id}:{cycle_timestamp.isoformat()}:incremental` — `can_start_cycle` returns `already_completed` for a re-run of a completed cycle, `retry_limit_reached` after 3 failed attempts, and reclaims stale `IN_PROGRESS` checkpoints past a 15-minute timeout (`stale_reclaimed`). Uses `write_table(..., allow_existing=True)` — appends to an existing dataset-version's Parquet files rather than requiring a fresh version per cycle (append-in-place pattern distinct from the backfill/candidate-generation "one version = one build" pattern seen elsewhere).

### src/autonomous_trading_platform/ingestion/market_data/services/bar_aggregation_service.py (145 lines)
- Purpose: Stateful in-memory buffer that assembles 5 consecutive 1-minute bars into one 5-minute bar per `(symbol, 5-min-bucket)` key; validates minute-bar shape (tz-aware timestamp, exact 1-minute duration), enforces exact bucket continuity (5 bars at exactly the expected consecutive minute timestamps, no gaps/dupes) before aggregating.
- Notable: `_handle_cross_bucket_gap` proactively drops any older, still-incomplete buffer entries for the same symbol once a newer bucket starts arriving (handles early-close/gap scenarios) — logged at debug level only, so a dropped partial bucket is easy to miss operationally (debug-level logging for what is effectively a data-completeness event is a minor observability smell). OHLC aggregation is textbook-correct (first open, last close, max high, min low, sum volume/trade_count); `vwap` intentionally uses the *last* minute bar's vwap rather than a volume-weighted recomputation across the 5 minutes — comment flags this as a known simplification kept for test compatibility, not a true VWAP of the bucket.

### src/autonomous_trading_platform/ingestion/market_data/services/bar_ingestion_service.py (211 lines)
- Purpose: Orchestrates the per-provider-bar pipeline: convert Alpaca `Bar` → canonical `MarketBar` → classify session (skip non-REGULAR-session bars entirely, no aggregation/persistence) → feed to `BarAggregationService` → on 5-min completion, validate via `BarValidationService`, flag late bars (>30s past `end_timestamp`, only when `enforce_lateness=True`) and suspected outliers (>20% price move vs. previous bar's close) via `quality_flags`, and reject (return `None`) late bars entirely rather than persisting them with a flag.
- Notable: Late bars are audit-logged (`record_bar_late`) but then dropped (`return None` — never reach the caller's completed-bars list), which is a real data-loss point: a late-arriving 5-min bar is neither persisted nor retried, only recorded as an audit event. Outlier bars ARE still returned/persisted (just flagged), an intentional asymmetry (outliers are suspicious-but-usable, late bars are unusable due to lookahead/staleness risk in a live pipeline). `next_bar_decision` field on the service is essentially instance-level mutable "last call result" state exposed for the caller to introspect ad hoc (used by the backfill service's commented-out debug print) — an unusual API shape (side-channel state instead of return value carrying the reason).

### src/autonomous_trading_platform/ingestion/market_data/services/bar_validation_service.py (96 lines)
- Purpose: `validate_bar` delegates to declarative `MARKET_BAR_RULES`; `is_late_bar` / `evaluate_outlier` are pure threshold functions (20% price move default, 10x volume multiplier default) with input validation (rejects negative `max_move_pct`, non-positive `max_volume_multiplier`).
- Notable: `evaluate_outlier` supports volume-based outlier detection (`reference_volume`/`max_volume_multiplier`) but `bar_ingestion_service.py` never passes `reference_volume` when calling `is_suspected_outlier` — so volume-outlier detection is implemented but dead/unused in the live pipeline; only price-move outlier detection is actually active.

### src/autonomous_trading_platform/ingestion/market_data/services/dataset_version_finalization_service.py (173 lines)
- Purpose: Finalizes a backfill's `DatasetVersion` row after all per-symbol-date checkpoints complete: if any checkpoint isn't COMPLETED, marks the dataset version `"incomplete"` with a list of failed checkpoints in `metadata_json`; otherwise reads `_metadata.json` sidecar, computes a SHA-256 aggregate checksum over all Parquet files (path + per-file checksum, in sorted-path order for determinism), persists per-file checksum rows, and marks `"validated"`.
- Notable: Real completeness gate — a backfill dataset version cannot become `"validated"` unless every expected checkpoint reports COMPLETED; this is what `backfill_market_bars_job.py` checks before allowing the job to succeed. Raises `FileNotFoundError` (not a silent skip) if the metadata sidecar or Parquet files are missing — fail-fast on a broken write.

### src/autonomous_trading_platform/ingestion/market_data/services/incremental_ingestion_checkpoint_service.py (185 lines)
- Purpose: Checkpoint state machine for the incremental (live) ingestion cycle: PENDING → IN_PROGRESS → COMPLETED/FAILED, keyed deterministically by `{dataset_version_id}:{cycle_timestamp.isoformat()}:incremental`, with a max-retry cap (default 3) and stale-IN_PROGRESS reclaim (default 15-min timeout).
- Notable: Deterministic checkpoint ID is the idempotency substrate for `ingest_bars_job.py`'s SLA-safe re-run behavior. Every state transition is audit-logged via `audit_logger._record_event` — calling a name-mangled-looking private method (`_record_event`) directly from a different class is a minor encapsulation smell (should probably be a public method on `AuditLoggingService`).

### src/autonomous_trading_platform/ingestion/market_data/services/ingestion_quality_recorder_service.py (23 lines)
- Purpose: Thin persistence facade — upserts `SymbolDateCoverage` and `MissingBarIncidents` rows inside one `SorUnitOfWork`.
- Notable: No independent logic; shared by both the incremental job and the backfill service, so coverage/incident bookkeeping is centralized in one place rather than duplicated (good).

### src/autonomous_trading_platform/ingestion/market_data/services/market_backfill_service.py (532 lines)
- Purpose: Historical backfill orchestrator: fetches bars for a symbol/date-range window from `AlpacaHistoricalBarsClient`, groups by `(symbol, date)`, and processes each symbol-date "chunk" through `BarIngestionService` (with `enforce_lateness=False`), writing completed 5-min bars via `BarChunkWriterService`, computing coverage/gap rows against an expected-timestamp set, and per-chunk checkpointing.
- Notable: CONFIRMS backfill idempotency at symbol-date granularity: `_process_chunk` checks for an existing COMPLETED checkpoint (id `{ingestion_run_id}:{symbol}:{bar_date.isoformat()}`) and returns early (0 processed) if already done — safe to re-run a partially-failed backfill without reprocessing completed symbol-days. On failure mid-chunk, the checkpoint is marked FAILED with incremented `retry_count` and the exception re-raised (aborts the whole backfill run, not just that chunk — no per-chunk try/continue at the top-level loop, contrast with the incremental job's more granular per-symbol soft-skip pattern). Contains a **second, independently-maintained hardcoded US market holiday calendar** (`_US_MARKET_HOLIDAYS`, explicit dates 2022–2025 only) — same staleness risk flagged for `universe/services/market_calendar_service.py`'s `StaticNYSECalendarProvider`, and now confirmed as a duplicated, not-shared, source of truth (two independent hardcoded holiday lists exist in the codebase, both stopping at 2025, both needing manual yearly updates with no algorithmic fallback). Correctly excludes half-days from the holiday set (documented as intentional — Alpaca's actual returned bar count handles shortened sessions via the missing-bar/partial-completeness logic instead).

---

## Coverage note (ingestion/)
All 19 substantive files in `ingestion/` now read (10 empty `__init__.py` skipped per header, 19+10=29 matches verified count). 29/29 complete.

---

## feature_engineering/

### src/autonomous_trading_platform/feature_engineering/services/returns_feature_service.py (56 lines)
- Purpose: Computes `ret_1d`/`ret_5d`/`ret_20d` per symbol via `pandas.groupby("symbol")[price_column].pct_change(N)` — real, correct simple-return math (not a stub), warmup rows correctly NaN.
- Notable: Attaches lineage columns (`underlying_dataset_version`, `price_basis`, `year`, `month`) directly onto the feature frame — this is the pattern the downstream volatility/regime services rely on when they require the same lineage columns be present.

### src/autonomous_trading_platform/feature_engineering/services/moving_average_feature_service.py (37 lines)
- Purpose: Simple moving average via `groupby("symbol")[price_column].rolling(window, min_periods=window).mean()` — real rolling-window math, `min_periods=window` ensures no partial-window leakage (warmup rows are NaN, not a partially-computed average).
- Notable: No independent logic beyond a single rolling mean; column-presence validation raises `ValueError` on missing required columns.

### src/autonomous_trading_platform/feature_engineering/services/volatility_feature_service.py (58 lines)
- Purpose: Rolling standard deviation of a returns column (`groupby("symbol")[returns_column].rolling(window, min_periods=window).std()`) — real realized-volatility math (not annualized here; annualization, e.g. `*sqrt(252)`, happens instead in `universe/services/universe_candidate_builder.py`'s scoring, so the raw feature output is per-bar-interval std, unannualized).
- Notable: Requires the full lineage column set (`date`, `underlying_dataset_version`, `price_basis`, `year`, `month`) as input — i.e., this service is designed to consume `ReturnsFeatureService`'s output directly, not raw bars.

### src/autonomous_trading_platform/feature_engineering/services/liquidity_feature_service.py (54 lines)
- Purpose: Rolling average volume (`groupby("symbol")[volume_column].rolling(window, min_periods=window).mean()`) plus an optional bid-ask spread column (`ask - bid`) if bid/ask columns are present in the input frame, else `pd.NA`.
- Notable: Real math, not a stub, but the bid-ask spread computation is a straight subtraction with no NaN/negative-spread sanity check (a crossed/locked-market bid>ask input would silently produce a negative "spread" with no validation catching it).

### src/autonomous_trading_platform/feature_engineering/regimes/regime_type.py (105 lines)
- Purpose: Shared regime taxonomy: 5 `StrEnum`s (`TrendRegime`, `VolatilityRegime`, `LiquidityRegime`, `MeanReversionRegime`, `RiskRegime`) and 5 frozen dataclasses (per-dimension classification results + `RegimeClassificationResult.as_dict()` flattening all 20 output columns for the writer).
- Notable: Pure data shapes, no logic — the single source of truth for regime-related enum values referenced across all 4 classifiers and the orchestration service.

### src/autonomous_trading_platform/feature_engineering/regimes/regime_classification_service.py (163 lines)
- Purpose: Orchestrates the 4 regime classifiers (trend/volatility/liquidity/mean-reversion) into one multi-dimensional classification frame, left-merging their independently-computed frames on `(symbol, timestamp)`, then derives a composite `regime_risk` (RISK_ON/RISK_OFF/NEUTRAL) from the trend+volatility combination.
- Notable: `_derive_risk_regime` logic: RISK_ON = bull trend AND not-high-volatility; RISK_OFF = bear trend AND high-volatility; everything else with both signals present = NEUTRAL — a sensible, explicit composite rule, not ML-derived. If a `returns_frame` isn't supplied, computes a naive inline 1-period `pct_change` rather than reusing `ReturnsFeatureService` — same return-calc logic duplicated in two places (minor DRY smell, though the duplication is trivial one-liner logic so low risk).

### src/autonomous_trading_platform/feature_engineering/regimes/classifiers/trend_regime_classifier.py (85 lines)
- Purpose: Classifies bull/bear/sideways from short-MA vs long-MA (default 50/200, i.e. golden/death-cross logic) AND 20-day rolling-return sign agreement; `sideways` covers both "MA/return disagreement" and "MA spread too small" cases implicitly (anything not meeting the bull/bear mask).
- Notable: Real, non-trivial classification logic with confidence scoring (normalized MA spread, saturates at 5%) — not a stub. Warmup rows (either MA still NaN) get `regime=None` explicitly, distinguishing "not yet computable" from "sideways" — a correctness detail many naive implementations miss.

### src/autonomous_trading_platform/feature_engineering/regimes/classifiers/volatility_regime_classifier.py (106 lines)
- Purpose: Classifies high/normal/low volatility via **expanding** (not fixed-window) percentile rank of rolling realized volatility within each symbol's own history — high >80th pctile, low <20th pctile.
- Notable: Explicitly documented and implemented to avoid lookahead bias: `s.expanding(min_periods=1).rank(pct=True)` only uses data up to and including the current row, so the percentile/threshold at time T never depends on future data — this directly matters for backtest correctness (a fixed-window/whole-history percentile would leak future information into historical bars). Threshold values (the actual vol level at the 20th/80th percentile) are stored per-bar for explainability, computed the same lookahead-safe way via `expanding().quantile()`.

### src/autonomous_trading_platform/feature_engineering/regimes/classifiers/liquidity_regime_classifier.py (89 lines)
- Purpose: Classifies high/normal/low liquidity via expanding percentile rank of rolling average dollar volume (close × volume) within each symbol's own history — same lookahead-safe pattern as the volatility classifier.
- Notable: Structurally near-identical to `volatility_regime_classifier.py` (same percentile/confidence math, different input signal) — the two classifiers don't share a common base class or helper for the expanding-percentile-rank + confidence-from-boundary-distance pattern, so that logic (roughly 15 lines) is duplicated 3 times across trend/volatility/liquidity classifiers (well, trend uses a different confidence formula; volatility and liquidity are near-identical duplicates of each other). Minor DRY smell, not a correctness issue.

### src/autonomous_trading_platform/feature_engineering/regimes/classifiers/mean_reversion_regime_classifier.py (121 lines)
- Purpose: Classifies trending/mean_reverting/undefined using two orthogonal signals that must agree: rolling z-score volatility (`zscore_std`, vs. its own expanding median) and normalized trend strength (`|rolling_return| / realized_vol`, vs. a fixed 0.5 boundary); `undefined` when the two signals disagree.
- Notable: Most mathematically sophisticated of the four classifiers — z-score computed from rolling mean/std of price, guards divide-by-zero via `.replace(0, np.nan)` on both `rolling_std` and `realized_vol` denominators. Confidence = geometric mean (`sqrt(a*b)`) of two independent signal-distance metrics — a real, deliberate design choice, not hand-wavy.

### src/autonomous_trading_platform/feature_engineering/services/regime_feature_service.py (50 lines)
- Purpose: A *second, simpler* regime classifier — bull/bear/sideways purely from short-MA vs long-MA comparison (no return-sign confirmation, no confidence score, no risk regime). Docstring explicitly says "intentionally basic for the initial version."
- Notable: Coexists with the much richer `RegimeClassificationFeatureService`/`RegimeClassificationService` stack (4 classifiers, confidence scores, risk regime) as a parallel, still-actively-wired code path (see `regime_feature_job.py` below) — both are live jobs producing differently-named feature datasets (`"regime"` vs `"regime_classification"`), not legacy/dead code, but a clear "two implementations of a similar concept coexisting" pattern, same shape as the universe/ snapshot-vs-version and selection-cycle-vs-rotation duplications noted in that section.

### src/autonomous_trading_platform/feature_engineering/services/regime_classification_feature_service.py (60 lines)
- Purpose: Thin adapter mapping the feature-job contract (bars_frame + optional pre-computed returns_frame) onto `RegimeClassificationService.compute()`.
- Notable: No independent logic — a deliberate adapter layer, not duplication of the classification logic itself.

### src/autonomous_trading_platform/feature_engineering/services/feature_dataset_resolver_service.py (142 lines)
- Purpose: Resolves the source bars dataset for a feature job — either a specific `dataset_version_id` (validated to match the requested `price_basis` and to have `validation_status == "validated"`) or the latest validated dataset for that price basis; loads per-symbol Parquet slices via an injected `parquet_reader` and concatenates into one frame.
- Notable: Raises (not silently substitutes) if a requested dataset version has the wrong price_basis or isn't validated — a real gate preventing feature computation from running on unvalidated/wrong-basis source data. `load_bars_frame` requires explicit `symbols`/`start_date`/`end_date` (no "load everything" implicit default) — consistent with the PIT-discipline pattern seen throughout universe/ and ingestion/.

### src/autonomous_trading_platform/feature_engineering/services/feature_dataset_writer_service.py (123 lines)
- Purpose: Writes a computed feature frame to Parquet via an injected `parquet_writer`, registers a `FeatureDatasetVersion` contract (initially `validation_status="unvalidated"`), and exposes `mark_validated`/`mark_failed` to transition state after the caller runs validation.
- Notable: `mark_validated` computes a SHA-256 checksum over `dataset_version_id:feature_name:source_dataset_version:computation_parameters` — this checksums the *identity/config* of the dataset, not the actual output bytes/content (unlike `ingestion/market_data/services/dataset_version_finalization_service.py`, which hashes actual Parquet file contents) — a materially weaker integrity guarantee: two runs with the same parameters but different (e.g. buggy) computed output would get the same checksum. All feature jobs call `write_feature_dataset` then unconditionally `mark_validated` immediately after — validation happens via the separate `FeatureValidationService` calls in the job (raises before ever reaching `mark_validated`), so `mark_failed` appears to be defined but never actually invoked by any of the 6 jobs read in this batch (dead code path — jobs let exceptions propagate rather than catching and calling `mark_failed`).

### src/autonomous_trading_platform/feature_engineering/services/feature_pipeline_guard_service.py (82 lines)
- Purpose: Idempotency/dedup gate — checks via an injected `feature_dataset_repository.find_matching_dataset(...)` whether an equivalent feature dataset (same feature_name, source_dataset_version_id, symbols, date range, computation_parameters) already exists, to avoid recomputation.
- Notable: All 6 feature jobs call `get_existing_feature_dataset` and short-circuit-return the existing dataset if found — this is the feature-layer's idempotency mechanism, structurally parallel to `IncrementalIngestionCheckpointService` in ingestion/ and config-hash idempotency in universe/ rotation, though implemented via a repository-side "find matching" query rather than a deterministic hash — the actual matching logic isn't visible here (delegated to `feature_dataset_repository`, not read in this pass — out of scope, storage/ layer).

### src/autonomous_trading_platform/feature_engineering/services/feature_validation_service.py (119 lines)
- Purpose: Central declarative validation helpers (required columns, non-empty, no-NaN with warmup-allowance flag, numeric range) plus per-feature-type convenience wrappers (`validate_returns`, `validate_volatility`, `validate_moving_average`, `validate_liquidity`, `validate_regime`, `validate_regime_classification`).
- Notable: `validate_volatility` enforces `minimum=0.0` on the volatility column (std dev can't be negative — a real sanity check) but no analogous upper-bound sanity check anywhere (e.g. a runaway/garbage volatility value would pass). All 6 jobs call `allow_warmup_nans=True` except `RegimeFeatureJob`, which calls `allow_warmup_nans=False` — meaning the simple regime feature would raise `ValueError` on any warmup-period NaN row, while every other feature job explicitly tolerates warmup NaNs; whether `RegimeFeatureJob` is exercised against data windows long enough to never hit this (>=200 bars for the default long_window) isn't verified in this pass — potential latent bug if ever run against a short window.

### src/autonomous_trading_platform/feature_engineering/jobs/returns_feature_job.py (102 lines)
- Purpose: Standard job scaffold: resolve source bars → check pipeline guard (return existing if found) → compute via `ReturnsFeatureService` → validate → write → mark validated.
- Notable: Confirms the guard-check-before-compute pattern used identically across all 6 jobs in this directory (resolve → guard.get_existing → [compute → validate → write → mark_validated]).

### src/autonomous_trading_platform/feature_engineering/jobs/moving_average_feature_job.py (107 lines)
- Purpose: Same scaffold wired to `MovingAverageFeatureService`, output column hardcoded to `"moving_average_value"` (job-level constant, overriding the service's own `sma_{window}` default naming).
- Notable: No independent logic beyond the standard scaffold.

### src/autonomous_trading_platform/feature_engineering/jobs/volatility_feature_job.py (121 lines)
- Purpose: Same scaffold, but computes `ReturnsFeatureService` internally first (to get `ret_1d`) then feeds that into `VolatilityFeatureService` — the only job in this batch that chains two feature services together rather than calling one directly.
- Notable: This means volatility features are always recomputed from returns inline within this job rather than reading an already-persisted `"returns"` feature dataset (even if one exists) — a missed reuse opportunity given the pipeline guard/dedup infrastructure exists specifically to avoid redundant computation, though the redundant work here is cheap (one `pct_change` call).

### src/autonomous_trading_platform/feature_engineering/jobs/liquidity_feature_job.py (122 lines)
- Purpose: Same scaffold wired to `LiquidityFeatureService`.
- Notable: Minor bug: `output_columns` is built as `[avg_volume_output_column, "bid_ask_spread"]` unconditionally (lines 64-67), then if bid/ask columns are present, `"bid_ask_spread"` is appended a *second* time (lines 69-70) — `output_columns` ends up containing `"bid_ask_spread"` twice when bid/ask data is available. Harmless in practice (duplicate entries in a required-columns check just get validated twice), but it's dead/redundant code indicating the conditional-append logic was likely meant to replace the unconditional inclusion rather than duplicate it.

### src/autonomous_trading_platform/feature_engineering/jobs/regime_feature_job.py (110 lines)
- Purpose: Same scaffold wired to the "basic" `RegimeFeatureService`, feature_name=`"regime"`, `allow_warmup_nans=False` (the one exception among all 6 jobs, see note under `feature_validation_service.py` above).
- Notable: Confirms this basic regime job is still actively wired (not dead code) alongside the richer regime_classification job.

### src/autonomous_trading_platform/feature_engineering/jobs/regime_classification_feature_job.py (135 lines)
- Purpose: Same scaffold wired to `RegimeClassificationFeatureService`, feature_name=`"regime_classification"`, validates via `validate_regime_classification` (checks all 5 regime dimension columns present) and `allow_warmup_nans=True`.
- Notable: Exposes all of `RegimeClassificationService`'s tunable windows/percentiles as job-level kwargs with matching defaults (50/200 trend, 20 vol/liquidity/zscore windows, 80/20 percentiles) — full parameterization surfaced to the job caller, consistent with the "real, tunable TA math" conclusion for this whole directory.

---

## Coverage note (feature_engineering/)
All 22 substantive files in `feature_engineering/` now read (5 empty `__init__.py` skipped per header, 22+5=27 matches verified count). 27/27 complete.

---

## Overall coverage: 84/84 files read (28 universe/ + 29 ingestion/ + 27 feature_engineering/). 0 skips beyond the 19 empty `__init__.py` files explicitly noted in the header (4+10+5=19), which contain no logic to audit.

---

## Standout candidates

1. **`universe/services/universe_rotation_service.py`** — the atomic propose→validate→retire→activate→audit rotation flow, config-hash idempotency, and rollback-as-new-version-copy design are all real and correctly implemented; strongest single piece of governance machinery in the batch.
2. **`universe/services/survivorship_guard.py`** + **`universe/services/raw_market_pool_refresh_service.py`** — survivorship-bias elimination is a first-class, explicitly named, purpose-built concern (not incidental), with `first_seen`/`last_seen` tracking as the substrate.
3. **`feature_engineering/regimes/classifiers/volatility_regime_classifier.py`** / **`liquidity_regime_classifier.py`** — genuinely lookahead-safe design: expanding (not fixed-window) percentile ranks ensure a bar's regime label never depends on future data, which matters directly for backtest validity.
4. **`ingestion/market_data/services/market_backfill_service.py`** + **`incremental_ingestion_checkpoint_service.py`** — real per-symbol-date and per-cycle idempotent checkpointing (deterministic IDs, retry caps, stale-lock reclaim) safely supports re-running partially-failed jobs.
5. **`ingestion/market_data/jobs/ingest_bars_job.py`** — the 20%-missing-bar SLA breach mechanism is real and correctly differentiates live-mode (hard failure) vs. historical-replay-mode (soft, explicitly documented as intentional) enforcement.
6. **`ingestion/corporate_actions/services/corporate_action_adjustment_service.py`** — correct Decimal-based split/dividend adjustment math with proper rounding and dual raw/adjusted bar identity via distinct `price_basis`-keyed `bar_id`s.
7. **`feature_engineering/regimes/classifiers/mean_reversion_regime_classifier.py`** — most mathematically sophisticated classifier in the batch (two-signal agreement gate, geometric-mean confidence, divide-by-zero guards).

## Gaps / smells

1. **Three divergent "activate a universe" code paths** with inconsistent safety coverage: `UniverseRotationService.rotate()` (full churn/format/size validation), `run_universe_selection_cycle.py` (structural validation only, bypasses churn-cap checks), and `UniverseVersionService.build_and_activate_version()` (no pre-activation validation, no audit record at all). Whether the third is still live or dead code was flagged but not resolved in this pass.
2. **Two independently-maintained, both-stale hardcoded US market holiday calendars**: `universe/services/market_calendar_service.py` (`StaticNYSECalendarProvider`, 2025-2026 only) and `ingestion/market_data/services/market_backfill_service.py` (`_US_MARKET_HOLIDAYS`, 2022-2025 only) — duplicated source of truth, both will silently go wrong for undated future years with no algorithmic fallback.
3. **Two DST-handling approaches coexist**: `ingestion/helpers/session.py` correctly uses IANA `zoneinfo` for ET conversion; `universe/services/market_calendar_service.py` uses fixed UTC-offset constants that explicitly do not adjust for DST (self-documented as an approximation) — real twice-yearly bug risk in the universe calendar service.
4. **Two symbol-format regexes coexist**: candidate builder allows `^[A-Z][A-Z0-9._-]*$`, Alpaca screener provider allows only `^[A-Z]{1,6}$` — inconsistent definitions of "valid ticker" in the same universe pipeline.
5. **Corporate-action client only fetches 2 of 8 supported action types**: `alpaca_corporate_action_client.py` only pulls `cash_dividends`/`reverse_splits`, so the normalization service's mappings for stock dividends, spinoffs, mergers, and name changes are currently unreachable dead code paths given the live client.
6. **Inconsistent failure-handling posture within one method**: `CorporateActionIngestionService.ingest_corporate_actions()` soft-skips (continue) on normalization/validation failures per-record, but hard-aborts (re-raise) the entire batch on any single adjustment failure.
7. **Late 5-minute bars are silently dropped, not persisted-with-flag**: `BarIngestionService.handle_minute_bar` audit-logs late bars via `record_bar_late` but then returns `None` — the bar is never written to Parquet or retried, an asymmetry vs. outlier bars (which are flagged but still persisted).
8. **Volume-based outlier detection is implemented but dead**: `BarValidationService.evaluate_outlier` supports a `reference_volume`/`max_volume_multiplier` check, but the only caller (`BarIngestionService`) never passes `reference_volume` — only price-move outliers are actually detected in the live pipeline.
9. **`FeatureDatasetWriterService.mark_failed` appears unused** by any of the 6 feature jobs read (all let exceptions propagate instead of catching and marking `"failed"`); its `mark_validated` checksum hashes dataset *identity/parameters*, not actual output content — a materially weaker integrity guarantee than the content-hash checksum used in `ingestion/market_data/services/dataset_version_finalization_service.py`.
10. **Two coexisting regime-classification implementations**: the "basic" `RegimeFeatureService` (bull/bear/sideways from MA cross only, no confidence) and the full `RegimeClassificationService`/4-classifier stack are both actively wired as separate jobs producing differently-named feature datasets — not dead code, but duplicated conceptual surface area, same pattern as the universe/ snapshot-vs-version duplication.
11. Minor: `liquidity_feature_job.py` double-includes `"bid_ask_spread"` in its `output_columns` list when bid/ask data is present (harmless but indicates leftover/incorrect conditional logic); `volatility_feature_job.py` always recomputes returns inline rather than reusing a persisted `"returns"` feature dataset despite the pipeline-guard/dedup infrastructure existing for exactly that purpose.
12. `survivorship_guard.py`'s `validate_symbols_not_future_leaked` depends on callers externally supplying `known_future_symbols` — not verified in this pass whether any real caller actually wires this correctly (flagged, not resolved).

## Coverage: read 84 of 84 (28 universe/ + 29 ingestion/ + 27 feature_engineering/). No skips beyond 19 trivially-empty `__init__.py` files (noted explicitly in the header, not given individual entries as they contain zero logic).
