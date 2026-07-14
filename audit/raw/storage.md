# Audit: src/autonomous_trading_platform/storage/

Auditor scope: entire storage layer (SoR Postgres/SQLAlchemy + Parquet datasets). All counts computed, not estimated.

## Verified counts

```
$ find src/autonomous_trading_platform/storage -name '*.py' | wc -l
170

$ find src/autonomous_trading_platform/storage -name '*.py' -print0 | sort -z | xargs -0 wc -l | tail -1
 13073 total

$ grep -rc "class .*(Base)" src/autonomous_trading_platform/storage --include='*.py' | ... (sum)
75 ORM model classes subclassing Base (declarative_base defined in storage/sor/models/base.py)

$ grep -rn "^class .*Repository" src/autonomous_trading_platform/storage --include='*.py' | wc -l
73  -> 72 concrete repository classes + 1 BaseRepository (sor/repositories/base.py)
    Breakdown: 69 SoR repositories (core + queries) + 3 Parquet repositories
    (ParquetBarRepository, ParquetFeatureRepository, ParquetSimulationRepository)

$ grep -rE "TODO|FIXME|XXX" src/autonomous_trading_platform/storage --include='*.py' | wc -l
0
```

## Claim verification (headline)

**UnitOfWork claim ("SorUnitOfWork wires ~29 repositories onto one SQLAlchemy session with commit/rollback semantics and transaction reuse")** — SUBSTANTIALLY TRUE, exact number is **30**, not 29. `storage/sor/services/unit_of_work.py` lines 100-129 instantiate exactly 30 repository attributes, each passed the same `Session`. `__enter__` reuses an existing transaction (`if not self.session.in_transaction(): self.session.begin()`); `__exit__` commits on success, rolls back on exception. **Smell:** `_started_transaction` is assigned in `__enter__` but never read — `__exit__` commits/rolls back unconditionally, so a nested `SorUnitOfWork` inside an outer transaction will commit the *outer* transaction on exit. The transaction-reuse bookkeeping is vestigial.

(Parquet claims and repository-bypass verified per-file below; see writer.py / versioning.py entries and Gaps section.)

## Per-file entries

### src/autonomous_trading_platform/storage/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/errors/errors.py (2 lines)
- Purpose: Defines `DatasetCorruptionError(RuntimeError)`, used by the Parquet reader to signal checksum/metadata mismatches.

### src/autonomous_trading_platform/storage/parquet/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/parquet/compute_checksum.py (27 lines)
- Purpose: `compute_table_checksum` hashes a normalized (metadata-stripped) Arrow IPC stream with SHA-256; `compute_file_checksum` streams a file in 1MB chunks through SHA-256.
- Notable: Table checksum strips schema metadata before hashing so re-attaching metadata doesn't change the checksum — correct design to avoid circular hashing (metadata itself contains the checksum).

### src/autonomous_trading_platform/storage/parquet/datasets.py (270 lines)
- Purpose: Declares ~24 frozen `ParquetDataset` dataclasses (key, schema, schema_version, root path parts, partition columns) spanning raw/adjusted bars, corporate actions, 6 feature families, 5 simulation outputs, 3 regime-analysis outputs, 5 validation-framework outputs, 3 research-intelligence outputs.
- Notable: `SIMULATION_INPUTS_DATASET` has a placeholder empty schema (`pa.schema([])`) with a `# placeholder for now` comment — dead/unfinished dataset definition.

### src/autonomous_trading_platform/storage/parquet/mappers.py (48 lines)
- Purpose: `bars_to_arrow` converts `list[MarketBar]` domain contracts into a PyArrow table matching `BAR_SCHEMA`.
- Notable: Defensively casts dictionary-encoded string columns back to plain `string()` and forces `quality_flags` to `list<string>` — guards against PyArrow's type-inference quirks (dictionary encoding / list<null> for empty lists) causing schema drift across partition files.

### src/autonomous_trading_platform/storage/parquet/metadata.py (115 lines)
- Purpose: Builds/encodes/decodes the sidecar metadata dict attached to every dataset's Arrow schema (dataset_name, schema_version, dataset_version, ingestion_timestamp, checksum, plus feature/artifact-specific fields); `validate_required_metadata` enforces the 5 required keys are present.
- Notable: `encode_metadata`/`decode_metadata` round-trip metadata as UTF-8 bytes since Arrow schema metadata is `dict[bytes, bytes]`.

### src/autonomous_trading_platform/storage/parquet/paths.py (63 lines)
- Purpose: Path-building helpers: `get_data_root` (env `DATA_ROOT`, default "data"), `format_partition_value` (dates → ISO, strings → uppercased if short alpha, e.g. symbols), `dataset_version_root`, `partition_path`, `partition_file_path`.
- Notable: `format_partition_value` silently uppercases any short alphabetic string (`len<=10`) assuming it's a ticker symbol — a generic-looking helper with a market-data-specific assumption baked in.

### src/autonomous_trading_platform/storage/parquet/reader.py (268 lines)
- Purpose: `read_dataset` (whole-dataset read + metadata/schema-version validation), `list_partition_files` (resolves month-partition parquet files for a symbol/date range, preferring compacted `data.parquet` over `part-*.parquet` fragments), and `HistoricalBarDatasetReader` class offering `read_with_pyarrow`, `read_with_duckdb` (default, single-threaded, falls back to pyarrow on DuckDB OOM), and `_verify_file_checksums` which cross-checks file checksums against the `checksums` SoR table via `SorUnitOfWork`.
- Notable: Cross-layer coupling — the Parquet reader imports `SorUnitOfWork` directly to verify checksums against Postgres, meaning Parquet reads can depend on the SoR being reachable. `_verify_file_checksums` is defined but not called anywhere in this file (dead code, or invoked externally — not verified here).

### src/autonomous_trading_platform/storage/parquet/schemas.py (463 lines)
- Purpose: Defines every PyArrow schema (bars, corporate actions, 6 feature schemas, 5 simulation schemas, 3 regime-analysis schemas, 5 validation schemas, 3 intelligence schemas) referenced by `datasets.py`.
- Notable: Purely declarative; consistent identity-column prefixes (`_REGIME_ANALYSIS_IDENTITY`, `_VALIDATION_IDENTITY`, `_INTELLIGENCE_IDENTITY`) shared via list-concatenation across related schemas — good reuse pattern.

### src/autonomous_trading_platform/storage/parquet/writer.py (179 lines)
- Purpose: Canonical dataset writer. `prepare_partition_columns` auto-derives `date`/`year`/`month` partition columns for bar/corporate-action tables; `write_table` computes a checksum, attaches full metadata to the schema, enforces immutability via `_ensure_dataset_version_is_new` (raises `FileExistsError` if any `*.parquet` or `_metadata.json` already exists under the version root, unless `allow_existing=True`), writes Hive-partitioned Parquet via `pyarrow.dataset.write_dataset`, then emits a `_metadata.json` sidecar with row/file counts and total size.
- Notable: CONFIRMS the audit claim — append-once (`FileExistsError` on existing version), Hive-style auto-partitioning, checksum-per-write, and a JSON metadata manifest are all real and located exactly here. However `allow_existing=True` is an escape hatch that disables the immutability check entirely (`existing_data_behavior="overwrite_or_ignore"`), and `BarChunkWriterService` (below) does not use this writer at all.

### src/autonomous_trading_platform/storage/parquet/versioning.py (12 lines)
- Purpose: `generate_dataset_version` builds a version string from a normalized dataset name + UTC timestamp + 8-char uuid suffix.

### src/autonomous_trading_platform/storage/parquet/repositories/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/parquet/repositories/parquet_bar_repository.py (142 lines)
- Purpose: `ParquetBarRepository.get_raw_bars_before_date` reads raw bars for a symbol via `HistoricalBarDatasetReader` (duckdb engine) and filters/sorts them client-side before a cutoff timestamp, mapping rows back to `MarketBar` contracts.
- Notable: Pulls a live SQLAlchemy `Session` via `get_session()` in `__init__` purely to hand to the reader (for checksum verification plumbing), coupling a Parquet repository's construction to DB session availability even when checksum verification is never invoked in this class's own methods.

### src/autonomous_trading_platform/storage/parquet/repositories/parquet_feature_repository.py (183 lines)
- Purpose: `ParquetFeatureRepository` writes feature frames (returns, volatility, moving_average, liquidity, regime, regime_classification) to their respective Parquet datasets, auto-deriving `date`/`year`/`month`/lineage columns and validating column names+types against the target schema before write.
- Notable: `write_feature_dataset` uses `existing_data_behavior="overwrite_or_ignore"` directly via `pyarrow.dataset.write_dataset` rather than going through `writer.write_table` — this is a **second, parallel write path that bypasses the append-once/immutability guarantee** enforced in `writer.py`.

### src/autonomous_trading_platform/storage/parquet/repositories/parquet_simulation_repository.py (231 lines)
- Purpose: `ParquetSimulationRepository` writes/reads simulation artifacts (trade logs, equity curve, per-bar metrics, positions, signal log) keyed by `SimulationArtifactIdentity` (experiment/strategy/run/stage/window_role), with per-output convenience methods and `read_equity_curve`.
- Notable: Same bypass pattern as the feature repository — writes go straight through `pyarrow.dataset.write_dataset(existing_data_behavior="overwrite_or_ignore")`, not `writer.write_table`, so simulation artifacts have no append-once enforcement, no checksum, and no `_metadata.json` sidecar despite being "Parquet datasets" in the same package.

### src/autonomous_trading_platform/storage/parquet/services/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/sor/models/__init__.py (124 lines)
- Purpose: Aggregates all ORM model modules under one import surface so `Base.metadata` sees every table for Alembic autogeneration; re-exports `Base`.

### src/autonomous_trading_platform/storage/sor/models/base.py (17 lines)
- Purpose: Declares the shared SQLAlchemy `declarative_base()` (`Base`) with an explicit naming convention for indexes/constraints/FKs, ensuring deterministic, greppable constraint names in migrations.

### src/autonomous_trading_platform/storage/sor/models/helpers/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/sor/models/helpers/sa_types.py (99 lines)
- Purpose: Custom SQLAlchemy `TypeDecorator`s shared across models: `UTCDateTimeType` (rejects naive datetimes on bind, normalizes to UTC on read, with an explicit SQLite-naive-string workaround), `MoneyType`/`QuantityType` (Numeric-backed, strictly reject non-`Decimal` Python values), `JSONStringListType` (native Postgres `ARRAY(String)`, JSON-serialized `Text` on SQLite/other dialects for cross-dialect testability).
- Notable: `MoneyType`/`QuantityType.process_bind_param` raise `TypeError` for anything but `Decimal` — a strict guard preventing float precision bugs from silently entering money/quantity columns store-wide.

### src/autonomous_trading_platform/storage/sor/models/allocation_overrides.py (28 lines)
- Purpose: ORM model for `allocation_overrides` — operator-issued per-strategy caps (max % of capital, max position size, max drawdown) with expiry.

### src/autonomous_trading_platform/storage/sor/models/allocation_rebalance_history.py (39 lines)
- Purpose: ORM model for `allocation_rebalance_history` — one row per rebalance cycle run (status, trigger source, churn %, allocation delta, JSONB result summary).

### src/autonomous_trading_platform/storage/sor/models/audit_logs.py (25 lines)
- Purpose: ORM model `AuditLogRow` for `audit_logs` — generic system event log (event_type, component, message, JSONB metadata).
- Notable: Uses `synonym("metadata_")` to expose the DB column named `metadata` (a reserved attribute name on declarative `Base`) as `event_metadata` in Python — a common SQLAlchemy workaround, applied consistently elsewhere (`fills.py`, `corporate_actions.py` use `meta` instead).

### src/autonomous_trading_platform/storage/sor/models/black_litterman_research_runs.py (55 lines)
- Purpose: ORM model for `black_litterman_research_runs` — persists one full Black-Litterman allocation artifact (views, confidences, prior/posterior returns & covariance, proposed weights, diagnostics, multiple lineage hashes) for research reproducibility.

### src/autonomous_trading_platform/storage/sor/models/blended_metrics_snapshots.py (51 lines)
- Purpose: ORM model for `blended_metrics_snapshots` — per-strategy blended research/live score snapshot (alpha weight, research/live/blended scores) with lineage back to source metric snapshots.

### src/autonomous_trading_platform/storage/sor/models/broker_account_snapshots.py (38 lines)
- Purpose: ORM model for `broker_account_snapshots` — periodic broker account state (cash, buying power, equity, portfolio value) keyed by broker/environment/account.

### src/autonomous_trading_platform/storage/sor/models/broker_orders.py (74 lines)
- Purpose: ORM model for `broker_orders` — full broker order lifecycle record (status, fill quantities/prices, timestamps for signal→submission→ack→first-fill, raw broker payload) using `Money`/`Quantity` custom types and multiple `Side`/`OrderType`/`TimeInForce`/`OrderStatus` enums.

### src/autonomous_trading_platform/storage/sor/models/capital_allocation_policies.py (37 lines)
- Purpose: ORM model for `capital_allocation_policies` — governance policy caps per approval-status/performance-tier tier (max % capital, max position size, max drawdown).

### src/autonomous_trading_platform/storage/sor/models/cash_snapshots.py (35 lines)
- Purpose: ORM model for `cash_snapshots` — point-in-time cash/buying-power/equity snapshot per run, with settlement-aware `settled_cash`/`unsettled_cash` columns (nullable for legacy rows).

### src/autonomous_trading_platform/storage/sor/models/checksums.py (22 lines)
- Purpose: ORM model `Checksums` for `checksums` — the SoR-side ledger of Parquet file checksums (`dataset_version`, `object_path`, `checksum_algorithm`, `checksum_value`) consumed by `HistoricalBarDatasetReader._verify_file_checksums`.

### src/autonomous_trading_platform/storage/sor/models/corporate_actions.py (55 lines)
- Purpose: ORM model for `corporate_actions` — splits/dividends/ticker-changes with a unique constraint on (symbol, action_type, effective_date, source) to prevent duplicate ingestion.

### src/autonomous_trading_platform/storage/sor/models/correlation_snapshots.py (96 lines)
- Purpose: Two ORM models — `CorrelationSnapshotRow` (`correlation_snapshots`) and `CovarianceSnapshotRow` (`covariance_snapshots`) — rolling pairwise correlation/covariance matrices stored as JSONB with numerical-stability diagnostics (condition number, positive-definiteness).

### src/autonomous_trading_platform/storage/sor/models/dataset_versions.py (46 lines)
- Purpose: ORM model for `dataset_versions` — SoR-side registry of Parquet bar dataset versions (schema version, symbol/date coverage, validation status, checksum, source manifest).

### src/autonomous_trading_platform/storage/sor/models/drawdown_governance_ladder_state.py (82 lines)
- Purpose: ORM model `DrawdownGovernanceLadderStateRow` for `drawdown_governance_ladder_states` — current per-strategy drawdown ladder rung (normal/warning/breach etc.), allocation scalar, cooldown/anti-flapping fields, and operator-acknowledgement gating for breach recovery. One row per strategy, upserted.

### src/autonomous_trading_platform/storage/sor/models/drawdown_governance_ladder_transition.py (59 lines)
- Purpose: ORM model `DrawdownGovernanceLadderTransitionRow` for `drawdown_governance_ladder_transitions` — the append-only counterpart to the ladder-state table, recording every state change with triggering metrics for audit.

### src/autonomous_trading_platform/storage/sor/models/experiments.py (32 lines)
- Purpose: ORM model for `experiments` — research experiment metadata (strategy set, parameter grid, dataset/universe version, start/end time) as JSONB blobs.

### src/autonomous_trading_platform/storage/sor/models/factor_exposure_snapshots.py (118 lines)
- Purpose: Three ORM models — `FactorExposureSnapshotRow` (`factor_exposure_snapshots`, portfolio-level), `StrategyFactorExposureRow` (`strategy_factor_exposures`), `PortfolioFactorExposureRow` (`portfolio_factor_exposures`) — factor exposure monitoring at portfolio/strategy/aggregate granularity with concentration diagnostics and full data lineage.

### src/autonomous_trading_platform/storage/sor/models/factor_neutralization_runs.py (54 lines)
- Purpose: ORM model for `factor_neutralization_runs` — records one factor-neutralization optimization attempt (pre/post exposures, exposure reduction, constraint utilization/violations, fallback mode).

### src/autonomous_trading_platform/storage/sor/models/feature_dataset_versions.py (50 lines)
- Purpose: ORM model for `feature_dataset_versions` — SoR-side registry of Parquet feature dataset versions, analogous to `dataset_versions.py` but for derived features (moving average, volatility, etc.), including `computation_code_version` for reproducibility.

### src/autonomous_trading_platform/storage/sor/models/fill_quality_metrics.py (52 lines)
- Purpose: ORM model for `fill_quality_metrics` — per-fill execution-quality analytics (latency from signal→submission→fill, slippage in bps/notional, commission/spread/total cost, adverse-fill flag).

### src/autonomous_trading_platform/storage/sor/models/fills.py (39 lines)
- Purpose: ORM model for `fills` — individual trade fill records (price, quantity, fees, liquidity side, venue) linked to broker order/intent/run IDs.

### src/autonomous_trading_platform/storage/sor/models/governance_audit_events.py (95 lines)
- Purpose: ORM model `GovernanceAuditEventRow` for `governance_audit_events` — the immutable, append-only governance decision ledger (promotions/demotions/health transitions/drawdown escalations) with full before/after state snapshots, criteria evaluated, and an amendment chain (`superseded_by`) rather than mutation.
- Notable: Explicitly documented as never-updated with amendments modeled as new superseding rows — a deliberate event-sourcing pattern for governance auditability.

### src/autonomous_trading_platform/storage/parquet/services/bar_chunk_writer_service.py (86 lines)
- Purpose: `BarChunkWriterService.write_backfill_chunk` writes a daily backfill chunk of bars for one symbol, merging into any existing month-partition `data.parquet` file by reading it, concatenating, and deduplicating by `bar_id` (last-write-wins), then calling `pq.write_table` directly — a third, independent write path.
- Notable: **This is a THIRD Parquet write path that bypasses both `writer.write_table`'s immutability check and its checksum/metadata-manifest logic.** It writes its own ad hoc `_metadata.json` with only `dataset_version`/`dataset`/`last_updated_at` (no checksum, no row/file counts), and directly mutates (`read → merge → overwrite`) existing files — the opposite of append-once. This directly contradicts a literal reading of "Parquet writer enforces append-once semantics" as a blanket property of the storage layer: it's true only for `writer.write_table`'s callers, not for these two other write paths used by feature/simulation repositories and bar backfill.

### src/autonomous_trading_platform/storage/sor/models/ingestion_checkpoint.py (51 lines)
- Purpose: ORM model for `ingestion_checkpoints` — resumability checkpoints per ingestion run/dataset version/symbol (last successful bar timestamp, retry count, error message), FK'd to `ingestion_runs` and `dataset_versions`.

### src/autonomous_trading_platform/storage/sor/models/ingestion_runs.py (40 lines)
- Purpose: ORM model for `ingestion_runs` — one row per ingestion pipeline execution (run type, source, dataset version, row/file counts, status).
- Notable: `_enum_values` helper special-cases `RunType.GOVERNANCE` to store its `.value` while all other members store `.name` in the DB enum — an inconsistent enum-value serialization rule baked into a one-off helper function.

### src/autonomous_trading_platform/storage/sor/models/kill_switch_state.py (38 lines)
- Purpose: ORM model for `kill_switch_state` — singleton row (`id="current"`) recording whether the global kill switch is enabled, who enabled/cleared it and when.
- Notable: Singleton-row pattern via a fixed default primary key (`KILL_SWITCH_SINGLETON_ID = "current"`), same pattern as `PortfolioDrawdownGovernanceState` below.

### src/autonomous_trading_platform/storage/sor/models/market_bars.py (75 lines)
- Purpose: ORM model for `market_bars` — the SoR mirror of ingested OHLCV bars (parallel to the Parquet `market_bars`/raw-bars dataset), with a unique constraint on (symbol, interval, timestamp, price_basis) preventing duplicate bars.
- Notable: `market_session = synonym("session")` — another reserved-name workaround (`session` collides with SQLAlchemy's ORM `Session` concept in some contexts).

### src/autonomous_trading_platform/storage/sor/models/metrics_summary.py (44 lines)
- Purpose: ORM model for `metrics_summary` — aggregate performance metrics (Sharpe, drawdown, trade counts, volatility) for a `simulation_runs` row, plus nullable lineage columns (metric_lineage_type, environment, calculation_version) added later without backfilling old rows.

### src/autonomous_trading_platform/storage/sor/models/missing_bar_incidents.py (26 lines)
- Purpose: ORM model for `missing_bar_incidents` — records detected gaps in ingested bar data (symbol, timestamp, dataset version, severity, resolved flag).

### src/autonomous_trading_platform/storage/sor/models/operational_alerts.py (51 lines)
- Purpose: ORM model for `operational_alerts` — deduplicated (via unique `fingerprint`) operational alerting with acknowledge/resolve/snooze workflow fields and a JSONB `notes` audit trail.

### src/autonomous_trading_platform/storage/sor/models/operator_settings.py (85 lines)
- Purpose: ORM model `OperatorSettingsRow` for `operator_settings` — the single largest configuration table: risk tolerance, drawdown limits, rebalance cadence, auto-promote/demote toggles, slippage/transaction-cost model choice, per-strategy/portfolio exposure caps, portfolio drawdown governance defaults.
- Notable: Comment flags `min_sharpe_for_promotion`/`min_paper_trading_period_days` as "Deprecated compatibility fields" superseded by `PromotionRules` but the columns remain in the schema — accumulating dead config surface rather than being migrated out.

### src/autonomous_trading_platform/storage/sor/models/optimizer_runs.py (64 lines)
- Purpose: ORM model for `optimizer_runs` — full audit record of a mean-variance optimizer run (constraints applied/binding, convergence, solver status, target vs current weights) explicitly designed for "shadow-mode comparison" per its docstring.

### src/autonomous_trading_platform/storage/sor/models/order_intents.py (56 lines)
- Purpose: ORM model for `order_intents` — pre-broker order intent record with a unique `(run_id, idempotency_key)` constraint to guarantee idempotent order submission.

### src/autonomous_trading_platform/storage/sor/models/portfolio_construction.py (177 lines)
- Purpose: Four ORM models for the portfolio construction pipeline: `PortfolioConstructionRunRow` (per-run aggregate diagnostics), `PortfolioSignalBatchItemRow` (Phase 1 raw signals), `PortfolioNettedSignalRow` (Phase 2 cross-strategy netting), `PortfolioSignalIntentRow` (Phase 3 constraint-gated intents) — explicitly designed per module docstring for "replay debugging, governance review, strategy validation, and shadow-mode comparisons."

### src/autonomous_trading_platform/storage/sor/models/portfolio_drawdown_governance_state.py (76 lines)
- Purpose: ORM model for `portfolio_drawdown_governance_state` — singleton row (`governance_id="current"`) tracking portfolio-level peak-equity high-watermark, breach state, and kill-switch auto-activation, persisted so drawdown state survives scheduler restarts.
- Notable: Docstring explicitly documents the high-watermark-never-resets-downward invariant (except explicit operator reset) — a deliberate safety design to prevent a restart from spuriously computing a fresh, lower peak and hiding a real drawdown.

### src/autonomous_trading_platform/storage/sor/models/position_snapshot_items.py (68 lines)
- Purpose: ORM model for `position_snapshot_items` — per-symbol line items (quantity, cost basis, market value, unrealized P&L) belonging to a `PositionSnapshot`, composite PK `(snapshot_id, symbol)` with `ondelete="CASCADE"`.

### src/autonomous_trading_platform/storage/sor/models/position_snapshots.py (57 lines)
- Purpose: ORM model for `position_snapshots` — parent snapshot header (run, timestamp, source) with a `relationship(cascade="all, delete-orphan")` to `PositionSnapshotItem`, and a unique constraint preventing duplicate snapshots per (run, timestamp, source).

### src/autonomous_trading_platform/storage/sor/models/promotion_rules.py (43 lines)
- Purpose: ORM model for `promotion_rules` — governance thresholds (min Sharpe/CAGR/win-rate, max drawdown, min days/trades tested) gating strategy promotion between lifecycle states, plus separate "maintenance" thresholds for staying promoted.

### src/autonomous_trading_platform/storage/sor/models/raw_market_pool.py (98 lines)
- Purpose: Three ORM models — `RawMarketSymbol` (`raw_market_symbols`, tradability/asset-type metadata per symbol), `RawMarketPoolSnapshot` (`raw_market_pool_snapshots`, point-in-time pool capture) and `RawMarketPoolMembership` (`raw_market_pool_memberships`, symbol membership per snapshot) with a parent/child `relationship(cascade="all, delete-orphan")`.

### src/autonomous_trading_platform/storage/sor/models/rebalance_runs.py (40 lines)
- Purpose: ORM model `UniverseRebalanceRun` for `universe_rebalance_runs` — one row per universe-rotation rebalance evaluation (added/removed/retained symbols, churn %, config/summary JSONB, rejection reason).
- Notable: File name (`rebalance_runs.py`) doesn't match the class/table name (`UniverseRebalanceRun`/`universe_rebalance_runs`) — a naming mismatch versus the sibling `allocation_rebalance_history.py` which covers strategy-allocation rebalancing (these are two distinct rebalance concepts that could be confused by name alone).

### src/autonomous_trading_platform/storage/sor/models/reconciliation_snapshots.py (36 lines)
- Purpose: ORM model for `reconciliation_snapshots` — one row per reconciliation check per run (expected vs actual value, delta, tolerance, severity), explicitly documented as "append-only — no upsert."

### src/autonomous_trading_platform/storage/sor/models/risk_budget_snapshots.py (62 lines)
- Purpose: ORM model `RiskBudgetSnapshotRow` for `risk_budget_snapshots` — persisted risk-budgeting computation results (target vs realized risk contributions, diversification ratio, convergence/fallback diagnostics) so downstream optimizers query rather than recompute.

### src/autonomous_trading_platform/storage/sor/models/risk_snapshots.py (36 lines)
- Purpose: ORM model for `risk_snapshots` — point-in-time gross/net exposure, leverage, drawdown %, limit utilization, and block reasons (`JSONStringListType` — one of few uses of that cross-dialect list type).

### src/autonomous_trading_platform/storage/sor/models/run_manifests.py (88 lines)
- Purpose: ORM model `RunManifestRow` for `run_manifests` — the most comprehensive reproducibility record in the schema: full run configuration (strategy/version/config, dataset/universe version, cost/fill model, random seed, git commit, docker image, dependency lock hash), execution progress (current/last step, status, error), and governance state.
- Notable: Duplicates the same `_enum_values` `RunType.GOVERNANCE`-special-cased helper seen in `ingestion_runs.py` — copy-pasted rather than shared, so a future enum change must be fixed in two places.

### src/autonomous_trading_platform/storage/sor/models/runtime_control_state.py (40 lines)
- Purpose: ORM model for `runtime_control_state` — singleton row (`control_id="global"`) for global trading-enabled/paused/kill-switch flags and trading mode (paper/live).

### src/autonomous_trading_platform/storage/sor/models/runtime_job_run_steps.py (36 lines)
- Purpose: ORM model for `runtime_job_run_steps` — per-step execution record (sequence number, status, duration, error) within a `runtime_job_runs` row, FK'd to its parent.

### src/autonomous_trading_platform/storage/sor/models/runtime_job_runs.py (44 lines)
- Purpose: ORM model for `runtime_job_runs` — generic scheduled-job execution ledger with self-referential `parent_job_run_id` FK (supports nested/sub-job runs), correlation ID, and JSONB input/output summaries.

### src/autonomous_trading_platform/storage/sor/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/sor/models/runtime_soak_reports.py (36 lines)
- Purpose: ORM model `RuntimeSoakReportRow` for `runtime_soak_reports` — persisted runtime-soak verification report (status, environment, checked window, failed checks JSONB, full report JSON).

### src/autonomous_trading_platform/storage/sor/models/shadow_comparison_snapshots.py (40 lines)
- Purpose: ORM model `ShadowComparisonSnapshotRow` for `shadow_comparison_snapshots` — point-in-time sim-vs-live comparison for one category/bar, storing raw values + drift metrics for audit/replay without joins.

### src/autonomous_trading_platform/storage/sor/models/shadow_divergences.py (51 lines)
- Purpose: ORM model `ShadowDivergenceRow` for `shadow_divergences` — one detected sim-vs-live divergence event (metric, magnitude, threshold, exceeds_threshold flag) within a shadow validation run.

### src/autonomous_trading_platform/storage/sor/models/shadow_runs.py (60 lines)
- Purpose: ORM model `ShadowRunRow` for `shadow_runs` — root aggregate for a shadow-mode validation run linking a simulation to a live/paper execution, with divergence counters, promotion-eligibility flag, and lineage refs (covariance/factor snapshot, allocation config hash, optimizer run ids).

### src/autonomous_trading_platform/storage/sor/models/signals.py (48 lines)
- Purpose: ORM model `Signal` for `signals` — per-strategy trading signal (direction, confidence, target position/exposure) with a unique constraint on `(run_id, bar_timestamp, symbol, strategy_id)`.

### src/autonomous_trading_platform/storage/sor/models/simulation_runs.py (56 lines)
- Purpose: ORM model `SimulationRuns` for `simulation_runs` — backtest/simulation run record FK'd to `experiments`, `strategy_configs`, and `dataset_versions`, with symbol list, date range, window role, and execution config JSONB.

### src/autonomous_trading_platform/storage/sor/models/strategy_configs.py (26 lines)
- Purpose: ORM model `StrategyConfigs` for `strategy_configs` — versioned strategy configuration keyed by `strategy_id`, with a unique constraint on `config_hash` for content-addressable dedup.

### src/autonomous_trading_platform/storage/sor/models/strategy_control_states.py (20 lines)
- Purpose: ORM model `StrategyControlState` for `strategy_control_states` — simple per-strategy enabled/disabled operator toggle with reason and audit fields.

### src/autonomous_trading_platform/storage/sor/models/strategy_governance.py (24 lines)
- Purpose: ORM model `StrategyGovernance` for `strategy_governance` — current lifecycle state per `(strategy_id, config_hash)` composite PK, linking to the originating experiment/run.

### src/autonomous_trading_platform/storage/sor/models/strategy_health_state.py (71 lines)
- Purpose: ORM model `StrategyHealthStateRow` for `strategy_health_states` — current per-strategy health snapshot (status, decline streak, quality trend) plus a second generation of lifecycle fields (suspension, anti-flapping cooldown, escalation counters, allocation penalty) added under a "Rec 6.3" comment block.
- Notable: Two generations of fields coexist in one table (original "FINDING-09" fields vs. later "Rec 6.3" lifecycle fields) — the file's own section comments document this evolution rather than hiding it.

### src/autonomous_trading_platform/storage/sor/models/strategy_health_transitions.py (58 lines)
- Purpose: ORM model `StrategyHealthTransitionRow` for `strategy_health_transitions` — append-only audit trail of every health lifecycle transition (from/to status, triggering metrics, actor), explicitly documented as never updated after insert.

### src/autonomous_trading_platform/storage/sor/models/strategy_live_performance_snapshots.py (42 lines)
- Purpose: ORM model `StrategyLivePerformanceSnapshot` for `strategy_live_performance_snapshots` — rolling live-trading performance metrics (realized return/Sharpe/drawdown/win-rate, days live) plus nullable lineage columns matching the pattern seen in `metrics_summary.py`.

### src/autonomous_trading_platform/storage/sor/models/strategy_quality_score_history.py (38 lines)
- Purpose: ORM model `StrategyQualityScoreHistory` for `strategy_quality_score_history` — time series of blended quality scores (live/backtest/blended, alpha weight) per strategy, optionally tied to a rebalance run.

### src/autonomous_trading_platform/storage/sor/models/strategy_runtime_states.py (49 lines)
- Purpose: ORM model `StrategyRuntimeState` for `strategy_runtime_states` — current state-machine state per strategy (`StrategyState` enum imported from `execution/services/strategy_state_machine_service.py`), cooldown/last-signal timestamps.
- Notable: A storage model importing an enum from `execution/services/` — a reverse dependency (storage → execution) that cuts against the documented inward-only layering (`interfaces → application → domain → storage → contracts`); execution is technically outside that chain but this still couples the ORM layer to an execution-layer enum definition.

### src/autonomous_trading_platform/storage/sor/models/symbol_date_coverage.py (25 lines)
- Purpose: ORM model `SymbolDateCoverage` for `symbol_date_coverages` — expected-vs-actual bar count and completeness status per symbol/date/dataset version, used for gap detection.

### src/autonomous_trading_platform/storage/sor/models/ticker_lifecycle_event.py (32 lines)
- Purpose: ORM model `TickerLifecycleEvent` for `ticker_lifecycle_events` — corporate ticker lifecycle events (rename/delisting/merger/successor) via a local `TickerLifecycleEventType` StrEnum, with successor symbol linkage.

### src/autonomous_trading_platform/storage/sor/models/tracked_orders.py (58 lines)
- Purpose: ORM model `TrackedOrder` for `tracked_orders` — lightweight open-order tracking row (previous filled qty/price for delta detection, `is_open` flag) distinct from the fuller `broker_orders` record.

### src/autonomous_trading_platform/storage/sor/models/universe_rotation_records.py (46 lines)
- Purpose: ORM model `UniverseRotationRecord` for `universe_rotation_records` — one row per universe rotation event (added/removed/retained symbols JSONB, churn %, approval workflow fields, rejection/rollback reasons).

### src/autonomous_trading_platform/storage/sor/models/universe_snapshots.py (35 lines)
- Purpose: ORM model `UniverseSnapshot` for `universe_snapshots`.
- Notable: File header explicitly marks this **DEPRECATED**, superseded by `UniverseVersion`/`UniverseMember` (`universe_versions.py`), "retained for historical data only... Do not use in new code." A live deprecated table+model+repository+service chain still exists in the codebase (see `universe_snapshot_repository.py` and `universe_snapshot_service.py` below, both also marked deprecated).

### src/autonomous_trading_platform/storage/sor/models/universe_versions.py (71 lines)
- Purpose: Two ORM models — `UniverseVersion` (`universe_versions`, lifecycle status/effective window/config hash) and `UniverseMember` (`universe_members`, per-symbol rank/score/inclusion-exclusion reason within a version) — the current universe-versioning system that replaced `UniverseSnapshot`.
- Notable: `UniverseVersion.members` relationship uses `lazy="dynamic"` — one of the few dynamic relationships in the schema, appropriate here since `UniverseMember` sets can be large (hundreds of symbols) and callers usually want a filtered/paginated query, not the full collection loaded eagerly.

### src/autonomous_trading_platform/storage/sor/repositories/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/sor/repositories/base.py (14 lines)
- Purpose: `BaseRepository` — trivial base class holding a `Session` reference; parent of ~most (not all) SoR repositories.

### src/autonomous_trading_platform/storage/sor/repositories/core/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/sor/repositories/core/allocation_overrides_repository.py (143 lines)
- Purpose: `AllocationOverridesRepository` — CRUD plus a one-active-override-per-strategy invariant: `create_override` raises `ValueError` if a non-expired active override already exists, and auto-deactivates stale (expired-but-still-flagged-active) rows before inserting.

### src/autonomous_trading_platform/storage/sor/repositories/core/allocation_rebalance_history_repository.py (87 lines)
- Purpose: `AllocationRebalanceHistoryRepository` — one row per rebalance-cycle execution; `get_active_lock` implements a distributed-lock-like check (a "running" row younger than a 4-hour stale window blocks concurrent rebalances).
- Notable: Not a `BaseRepository` subclass — takes its own `Session` directly and is not wired into `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/audit_logs_repository.py (96 lines)
- Purpose: `AuditLogRepository` — paginated event listing with JSONB-path filters (`metadata_["actor"]`, `metadata_["strategy_id"]`), plus `record_operator_action` which composes a human-readable message from actor+reason.
- Notable: `list_by_run_id` uses the legacy `session.query()` API instead of `select()` used everywhere else in this file — inconsistent SQLAlchemy style within the same class.

### src/autonomous_trading_platform/storage/sor/repositories/core/black_litterman_research_repository.py (46 lines)
- Purpose: `BlackLittermanResearchRepository` — insert/lookup for Black-Litterman research run artifacts. Not `BaseRepository`-based, not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/blended_metrics_repository.py (52 lines)
- Purpose: `BlendedMetricsRepository` — latest/history/by-rebalance lookups for blended metrics snapshots. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/broker_account_snapshot_repository.py (42 lines)
- Purpose: `BrokerAccountSnapshotRepository` — get-latest (ordered by `observed_at`/`created_at`/`snapshot_id` desc) and column-wise upsert for broker account snapshots.

### src/autonomous_trading_platform/storage/sor/repositories/core/broker_order_repository.py (112 lines)
- Purpose: `BrokerOrderRepository` — open-order listing, `mark_open_orders_cancelled_by_kill_switch` (bulk-cancels all open orders and stamps a reason), full CRUD.
- Notable: Class docstring is a literal unfilled template: `"Repository for interacting with the <table_name> table... idempotent upserts for <ModelName>."` — copy-paste boilerplate shipped verbatim into production code; the same exact template string recurs in `cash_snapshot_repository.py`, `fill_repository.py`, `order_intent_repository.py`, `position_snapshot_repository.py`, and `signals_repository.py` (6 files total).

### src/autonomous_trading_platform/storage/sor/repositories/core/capital_allocation_policies_repository.py (65 lines)
- Purpose: `CapitalAllocationPoliciesRepository` — `get_active_policy` resolves a tier-specific policy first, falling back to the tier-agnostic (`performance_tier IS NULL`) policy for a given approval status.

### src/autonomous_trading_platform/storage/sor/repositories/core/cash_snapshot_repository.py (133 lines)
- Purpose: `CashSnapshotRepository` — get-latest/list-recent/list-since/list-between, all ordered by `(timestamp desc, source_priority asc, snapshot_id desc)` where `_cash_source_priority()` ranks `LEDGER` above `BROKER_RECONCILED` above other sources — a tie-breaking rule ensuring the internal ledger wins over broker-reconciled data at the same timestamp.
- Notable: Carries the same unfilled `<table_name>`/`<ModelName>` docstring template noted above.

### src/autonomous_trading_platform/storage/sor/repositories/core/checksums_repository.py (76 lines)
- Purpose: `ChecksumsRepository` — CRUD for the `checksums` SoR table plus `build_row`, which derives a deterministic `checksum_id` via `uuid5(NAMESPACE_URL, f"{dataset_version}:{object_type}:{object_path}:{checksum_algorithm}")` so re-computing a checksum for the same object naturally upserts rather than duplicating.

### src/autonomous_trading_platform/storage/sor/repositories/core/corporate_action_repository.py (116 lines)
- Purpose: `CorporateActionRepository` — symbol/date-range queries plus an `UpsertResult[T]` dataclass wrapper distinguishing created-vs-updated on upsert (unlike most sibling repositories, which return the plain row).

### src/autonomous_trading_platform/storage/sor/repositories/core/correlation_snapshot_repository.py (134 lines)
- Purpose: `CorrelationSnapshotRepository` — parallel CRUD/history methods for both `CorrelationSnapshotRow` and `CovarianceSnapshotRow` in one class. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/dataset_versions_repository.py (139 lines)
- Purpose: `DatasetVersionsRepository` — contract↔row mapping (`to_row`/`to_contract`) plus `get_latest_validated`, `list_validated_by_coverage_and_price_basis`, and `list_validated_by_ids_and_price_basis` — all restricted to `validation_status == "validated"`, i.e. lineage-safe dataset lookups for simulations.

### src/autonomous_trading_platform/storage/sor/repositories/core/drawdown_governance_ladder_state_repository.py (70 lines)
- Purpose: `DrawdownGovernanceLadderStateRepository` — upsert increments `evaluation_count` server-side rather than trusting the caller's value, and only overwrites `operator_acknowledged_at/_by` when the incoming row actually sets them (avoids clobbering an existing ack on unrelated updates). Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/drawdown_governance_ladder_transition_repository.py (51 lines)
- Purpose: `DrawdownGovernanceLadderTransitionRepository` — append-only insert/query for ladder transitions, mirroring `strategy_health_transition_repository.py`'s pattern. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/experiments_repository.py (75 lines)
- Purpose: `ExperimentsRepository` — standard CRUD + contract↔row mapping for `experiments`.

### src/autonomous_trading_platform/storage/sor/repositories/core/factor_exposure_snapshot_repository.py (102 lines)
- Purpose: `FactorExposureSnapshotRepository` — insert/history for portfolio-, strategy-, and aggregate-level factor exposure rows in one class. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/factor_neutralization_repository.py (58 lines)
- Purpose: `FactorNeutralizationRepository` — insert/latest/history for factor-neutralization optimizer runs. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/feature_dataset_versions_repository.py (278 lines)
- Purpose: `FeatureDatasetVersionsRepository` — the largest repository file; beyond CRUD it implements `find_matching_dataset` and `find_for_simulation`, which resolve the most-recent validated feature dataset satisfying date-coverage and minimum-symbol-count constraints — feature-lineage resolution logic embedded directly in the repository rather than a service.
- Notable: `find_for_simulation`'s docstring explains it intentionally omits `computation_parameters` from the match so strategies that declare a feature "by name" can resolve any validated variant — a deliberate, documented relaxation of an otherwise strict lineage match.

### src/autonomous_trading_platform/storage/sor/repositories/core/fill_quality_metrics_repository.py (67 lines)
- Purpose: `FillQualityMetricsRepository` — lookup by record/intent id, upsert both ways, and `get_for_calibration` (fills with both `fill_timestamp` and `slippage_bps` populated in a window, for slippage-model calibration).

### src/autonomous_trading_platform/storage/sor/repositories/core/fill_repository.py (65 lines)
- Purpose: `FillRepository` — standard CRUD for `fills`.
- Notable: Carries the unfilled `<table_name>`/`<ModelName>` docstring template.

### src/autonomous_trading_platform/storage/sor/repositories/core/governance_audit_repository.py (218 lines)
- Purpose: `GovernanceAuditRepository` — paginated/filtered listing of the append-only governance audit ledger, `mark_superseded`, and `get_supersession_chain` (walks `superseded_by` links back to the root, with a `seen` set guarding against cycles).
- Notable: `_build_row` is a large static factory method for `GovernanceAuditEventRow` that duplicates the row's own constructor signature field-for-field — could be replaced by calling the constructor directly; unclear who calls this method (not visible in this file alone).

### src/autonomous_trading_platform/storage/sor/repositories/core/governance_repository.py (72 lines)
- Purpose: `GovernanceRepository` — CRUD plus contract↔row mapping for `strategy_governance`, including `list_by_state` (enum-backed) and `get_latest_by_strategy`.

### src/autonomous_trading_platform/storage/sor/repositories/core/ingestion_checkpoints_repository.py (116 lines)
- Purpose: `IngestionCheckpointsRepository` — checkpoint CRUD plus scope-aware lookups: `get_backfill_checkpoint` (date-scoped) vs. `get_cycle_checkpoint` (timestamp-scoped, filtered to `CheckpointScope.INCREMENTAL`) — two distinct checkpointing strategies coexisting in one table.

### src/autonomous_trading_platform/storage/sor/repositories/core/ingestion_runs_repository.py (80 lines)
- Purpose: `IngestionRunsRepository` — CRUD + contract↔row mapping for `ingestion_runs`.

### src/autonomous_trading_platform/storage/sor/repositories/core/kill_switch_state_repository.py (66 lines)
- Purpose: `KillSwitchStateRepository` — singleton-row accessor (`get_current_state` auto-creates the row with `is_enabled=False` on first access) plus `enable`/`disable` methods that clear the opposite pair of actor/timestamp fields.

### src/autonomous_trading_platform/storage/sor/repositories/core/live_performance_snapshot_repository.py (31 lines)
- Purpose: `LivePerformanceSnapshotRepository` — get-latest-by-strategy and insert for live performance snapshots. Not `BaseRepository`-based, not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/market_bar_repository.py (169 lines)
- Purpose: `MarketBarRepository` — contract↔row mapping (`_to_row`/`to_contract`), symbol/timestamp-range queries, and `get_raw_bars_before_date` (used by the Parquet repository's SoR-mirror lookups, RAW price-basis only).
- Notable: `to_contract` always returns `quality_flags=[]` regardless of the stored row's actual quality flags — a real information-loss bug: the contract round-trip silently drops quality-flag data that was written via `_to_row`.

### src/autonomous_trading_platform/storage/sor/repositories/core/metrics_summary_repository.py (80 lines)
- Purpose: `MetricsSummaryRepository` — CRUD + contract↔row mapping for `metrics_summary`.

### src/autonomous_trading_platform/storage/sor/repositories/core/missing_bar_incidents_repository.py (51 lines)
- Purpose: `MissingBarIncidentsRepository` — CRUD plus `list_by_dataset_version` (optional symbol filter) for gap-detection incidents.

### src/autonomous_trading_platform/storage/sor/repositories/core/operator_settings_repository.py (68 lines)
- Purpose: `OperatorSettingsRepository` — singleton-row (`settings_id="default"`) accessor; `get_or_create_default` seeds a full set of default risk/governance/notification values inline.
- Notable: `get_or_create_default` and `update_current` both call `self._session.commit()` directly inside the repository — every other repository in this layer leaves commit/rollback to the caller (or `SorUnitOfWork`). This repository unilaterally ends the caller's transaction, and is not wired into `SorUnitOfWork` at all — a boundary violation if ever called from within an otherwise-open UoW transaction.

### src/autonomous_trading_platform/storage/sor/repositories/core/optimizer_run_repository.py (66 lines)
- Purpose: `OptimizerRunRepository` — insert/latest/history/by-covariance-snapshot lookups for optimizer runs. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/order_intent_repository.py (69 lines)
- Purpose: `OrderIntentRepository` — CRUD for `order_intents`, UUID-coercing `get_by_intent_id`.
- Notable: Carries the unfilled `<table_name>`/`<ModelName>` docstring template.

### src/autonomous_trading_platform/storage/sor/repositories/core/portfolio_drawdown_governance_repository.py (58 lines)
- Purpose: `PortfolioDrawdownGovernanceRepository` — singleton-row accessor/updater for portfolio-level drawdown governance state, auto-creating safe defaults on first access.

### src/autonomous_trading_platform/storage/sor/repositories/core/position_snapshot_repository.py (131 lines)
- Purpose: `PositionSnapshotRepository` — `get_or_create_header` uses a SAVEPOINT (`session.begin_nested()`) with `IntegrityError` fallback-to-SELECT, gracefully handling a race where two fills in the same 5-minute bar concurrently try to create the same snapshot header without poisoning the outer transaction.
- Notable: Carries the unfilled `<table_name>`/`<ModelName>` docstring template. Good concurrency pattern otherwise — one of the more carefully-reasoned repositories in this batch.

### src/autonomous_trading_platform/storage/sor/repositories/core/promotion_rules_repository.py (40 lines)
- Purpose: `PromotionRulesRepository` — lookup by rule id / by from-to transition (active only) / all-active, plus soft-deactivate.

### src/autonomous_trading_platform/storage/sor/repositories/core/raw_market_pool_repository.py (140 lines)
- Purpose: `RawMarketPoolRepository` — snapshot + membership queries (with optional `as_of` time-travel via `get_latest_complete_snapshot_as_of`), asset-type/exchange/tradability filtering, and a large manual field-by-field `upsert_raw_market_symbol`.

### src/autonomous_trading_platform/storage/sor/repositories/core/reconciliation_snapshot_repository.py (41 lines)
- Purpose: `ReconciliationSnapshotRepository` — `append_report` fans a `ReconciliationReport` contract's per-check list out into individual `ReconciliationSnapshot` rows (consistent with the model's documented "append-only, no upsert" design).

### src/autonomous_trading_platform/storage/sor/repositories/core/risk_budget_snapshot_repository.py (61 lines)
- Purpose: `RiskBudgetSnapshotRepository` — insert/latest/history/by-covariance-snapshot for risk-budgeting results. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/risk_snapshot_repository.py (132 lines)
- Purpose: `RiskSnapshotRepository` — `_to_row` derives `block_reasons`/`is_blocked` from `breach_gross_exposure`/`breach_net_exposure`/`breach_leverage` contract attributes (via `getattr` with defaults, since the contract doesn't always carry them), and `_json_safe` recursively stringifies `Decimal` values for JSONB storage.

### src/autonomous_trading_platform/storage/sor/repositories/core/run_manifests_repository.py (141 lines)
- Purpose: `RunManifestRepository` — `add`/`upsert` (upsert manually re-lists every column), `to_contract` mapper, and `list_failed_runs`.
- Notable: `get_by_run_id` and `list_failed_runs` use the legacy `session.query()` API while `add`/`upsert` use `session.add`/`session.flush` idiomatically — no `select()` usage anywhere in this file, unlike most sibling repositories that have migrated to SQLAlchemy 2.0 style.

### src/autonomous_trading_platform/storage/sor/repositories/core/runtime_control_state_repository.py (159 lines)
- Purpose: `RuntimeControlStateRepository` — singleton-row (`control_id="global"`) accessor with one setter method per control dimension (`set_trading_enabled`, `set_trading_paused`, `set_kill_switch`, `activate_kill_switch`, `release_kill_switch`, `set_trading_mode`) — each independently flushes.

### src/autonomous_trading_platform/storage/sor/repositories/core/runtime_job_run_repository.py (107 lines)
- Purpose: `RuntimeJobRunRepository` — CRUD, contract↔row mapping, and lookups by job name / correlation id / parent (for nested sub-job runs).
- Notable: `save()` calls both `self.session.flush()` **and** `self.session.commit()` directly — same repo-commits-its-own-transaction pattern as `OperatorSettingsRepository` and `TickerLifecycleRepository`. Not wired into `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/runtime_job_run_step_repository.py (54 lines)
- Purpose: `RuntimeJobRunStepRepository` — append/list-by-job-run-id for per-step execution records, with contract↔row mapping.

### src/autonomous_trading_platform/storage/sor/repositories/core/runtime_soak_report_repository.py (59 lines)
- Purpose: `RuntimeSoakReportRepository` — `append_report` flattens a `RuntimeSoakVerificationReport` Pydantic contract (via `model_dump(mode="json")`) into the row's `failed_checks`/`runtime_metadata`/`report_json` columns; `get_latest_for_environment`, `list_by_window`.

### src/autonomous_trading_platform/storage/sor/repositories/core/shadow_comparison_snapshot_repository.py (37 lines)
- Purpose: `ShadowComparisonSnapshotRepository` — insert/insert_many/list-for-run (optional category filter) for shadow comparison snapshots. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/shadow_divergence_repository.py (40 lines)
- Purpose: `ShadowDivergenceRepository` — insert/list-for-run/`count_exceedances`.
- Notable: `count_exceedances` calls `list_for_run(..., limit=10000)` and takes `len()` of the Python list rather than issuing a `SELECT count(*)` — loads up to 10k full rows into memory just to count them.

### src/autonomous_trading_platform/storage/sor/repositories/core/shadow_run_repository.py (69 lines)
- Purpose: `ShadowRunRepository` — insert/get/list-for-strategy/list-all/`count_passing_cycles`.
- Notable: `update()` is a no-op that only calls `self._session.flush()` — it doesn't apply any field changes, relying entirely on the caller having mutated the ORM-tracked object in place before calling it; a misleading method name/signature (takes a `row` parameter that's never used). Same `len(list(...))` count-via-full-fetch pattern as the divergence repository.

### src/autonomous_trading_platform/storage/sor/repositories/core/signals_repository.py (81 lines)
- Purpose: `SignalRepository` — contract↔row mapping and CRUD for `signals`.
- Notable: Imports the same ORM class twice under two names (`from ...signals import Signal` then `from ...signals import Signal as SignalRow`) — redundant duplicate import of one class. Also carries the unfilled `<table_name>`/`<ModelName>` docstring template.

### src/autonomous_trading_platform/storage/sor/repositories/core/simulation_runs_repository.py (94 lines)
- Purpose: `SimulationRunsRepository` — CRUD, contract↔row mapping (including `PriceBasis` enum coercion), and lookups by experiment/strategy id.

### src/autonomous_trading_platform/storage/sor/repositories/core/strategy_configs_repository.py (63 lines)
- Purpose: `StrategyConfigsRepository` — lookup by strategy id or config hash, CRUD, contract↔row mapping.

### src/autonomous_trading_platform/storage/sor/repositories/core/strategy_control_state_repository.py (53 lines)
- Purpose: `StrategyControlStateRepository` — `is_enabled` defaults to `True` when no row exists (fail-open: an un-configured strategy is enabled by default), `set_enabled` upserts.

### src/autonomous_trading_platform/storage/sor/repositories/core/strategy_health_state_repository.py (77 lines)
- Purpose: `StrategyHealthStateRepository` — upsert increments `evaluation_count` server-side (like the drawdown-ladder repository), and conditionally updates suspension/lifecycle fields to avoid clobbering existing state on old-shaped rows. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/strategy_health_transition_repository.py (51 lines)
- Purpose: `StrategyHealthTransitionRepository` — append-only insert/query, mirroring `drawdown_governance_ladder_transition_repository.py`. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/strategy_quality_score_repository.py (39 lines)
- Purpose: `StrategyQualityScoreRepository` — insert/recent-for-strategy/by-rebalance-run for quality score history. Not in `SorUnitOfWork`.

### src/autonomous_trading_platform/storage/sor/repositories/core/strategy_runtime_state_repository.py (28 lines)
- Purpose: `StrategyRuntimeStateRepository` — lookup + upsert.
- Notable: `upsert()`'s update branch is a no-op — like `ShadowRunRepository.update()`, when a row already exists it just flushes without copying any fields from the incoming `row` argument onto `existing`; relies entirely on the caller having mutated the tracked instance directly. Same shape of bug in two independent files.

### src/autonomous_trading_platform/storage/sor/repositories/core/symbol_date_coverage_repository.py (73 lines)
- Purpose: `SymbolDateCoverageRepository` — CRUD plus `list_dataset_versions_covering_symbol_date_range` (distinct dataset versions with `completeness_status == "complete"` covering a symbol/date window).

### src/autonomous_trading_platform/storage/sor/repositories/core/ticker_lifecycle_repository.py (87 lines)
- Purpose: `TickerLifecycleRepository` — `upsert` uses `session.merge()` then immediately `commit()`s and `refresh()`es, with UTC-normalization helpers for naive datetimes.
- Notable: This repository **is** wired into `SorUnitOfWork` (`self.ticker_lifecycles = ...`) yet its `upsert()` unilaterally commits the session — calling it from inside a `SorUnitOfWork` block would prematurely commit the UoW's shared transaction, breaking the atomicity the UoW is meant to provide for any other repositories touched in the same `with` block.

### src/autonomous_trading_platform/storage/sor/repositories/core/tracked_order_repository.py (67 lines)
- Purpose: `TrackedOrderRepository` — open-order listing (all / by run / reconcilable-by-status), full-column upsert, `mark_closed`.

### src/autonomous_trading_platform/storage/sor/repositories/core/universe_rebalance_repository.py (48 lines)
- Purpose: `UniverseRebalanceRepository` — CRUD-ish lookups for `universe_rebalance_runs` by run id / candidate version / proposed version, plus recent/latest.

### src/autonomous_trading_platform/storage/sor/repositories/core/universe_rotation_repository.py (73 lines)
- Purpose: `UniverseRotationRepository` — lookups for `universe_rotation_records` by rotation id / version / previous version / time window, plus recent/latest.

### src/autonomous_trading_platform/storage/sor/repositories/core/universe_snapshot_repository.py (79 lines)
- Purpose: `UniverseSnapshotRepository` for the deprecated `universe_snapshots` table (effective-date range queries, open-snapshot lookup, close-open-snapshot).
- Notable: File header explicitly marks this **DEPRECATED**, superseded by `UniverseVersionRepository` — "Retained only to preserve the universe_snapshots historical table reference." Confirms the same deprecation chain flagged in the `universe_snapshots.py` model entry above.

### src/autonomous_trading_platform/storage/sor/repositories/core/universe_version_repository.py (180 lines)
- Purpose: `UniverseVersionRepository` — the current (non-deprecated) universe versioning repository: active-version-as-of, previous-version-before, member listing (all vs. included-only), and a lifecycle state machine (`insert_version` refuses `status="active"` directly — must insert as `candidate` then call `activate_version()`; `activate_version` validates the transition via `is_valid_transition` and requires ≥1 member; `retire_active_version`).
- Notable: Defines and raises two custom exceptions (`ImmutableVersionError`, `InvalidStatusTransitionError`) enforcing that active universe versions cannot be mutated — the only repository in this batch that encodes a real state-machine invariant rather than plain CRUD. `_guard_not_active` is defined but never called within this file (unused / dead helper, or invoked by a subclass/caller not visible here).

### src/autonomous_trading_platform/storage/sor/repositories/portfolio_construction_repository.py (201 lines)
- Purpose: `PortfolioConstructionRepository` — `persist_result` batches four artifact writes (run diagnostics, batch items, netted signals, signal intents) into one flush per pipeline run; explicit read methods per artifact type plus `list_conflicts`.
- Notable: `_persist_batch_items` is a documented no-op stub (`# we don't have raw signals here... pass`) — real raw-signal persistence happens via the separate `persist_raw_signals` method, meaning `persist_result` silently skips batch-item persistence unless callers separately invoke `persist_raw_signals`. A caller who only calls `persist_result()` will get a `PortfolioConstructionRunRow` with no corresponding `PortfolioSignalBatchItemRow`s.

### src/autonomous_trading_platform/storage/sor/repositories/queries/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/sor/repositories/queries/active_strategies_repository.py (325 lines)
- Purpose: `ActiveStrategiesRepository.list_active_strategies` — assembles the Strategy Lab / dashboard "active strategies" view by joining governance state, strategy config, today's trade count (via `OrderIntents` ⋈ `Fill`), control-state enabled flag, active allocation override, and capital allocation policy (tier-aware fallback) into one `ActiveStrategyDashboardRow` per strategy — the most elaborate read-model assembly in the storage layer, doing in Python what might otherwise be several joined queries or a view.
- Notable: `_resolve_allocated_capital` treats an override's `max_position_size_usd` as authoritative over `max_pct_of_capital`, and both take priority over the capital allocation policy — a three-tier precedence rule encoded only in code, not documented in either model's docstring.

### src/autonomous_trading_platform/storage/sor/repositories/queries/operations_repository.py (112 lines)
- Purpose: `OperationsRepository` — `list_jobs` reduces all `runtime_job_runs` rows down to the latest-per-job-name in Python (`setdefault` over a DESC-ordered full fetch) rather than a `DISTINCT ON`/window-function query, plus a small wrapper around `RuntimeControlStateRepository` for the operations dashboard's runtime-state tile.
- Notable: `_list_latest_runs_by_job_name` fetches **every** row in `runtime_job_runs` (no limit) to compute a per-job "latest" — will not scale as job run history grows; the correct SQL pattern (`DISTINCT ON` in Postgres, or a window function) is not used.

### src/autonomous_trading_platform/storage/sor/repositories/queries/portfolio_summary_repository.py (123 lines)
- Purpose: `PortfolioSummaryRepository` — latest/prior/first position & cash snapshot lookups (both ordered by the same `timestamp desc, source_priority asc, snapshot_id desc` tie-break rule as `cash_snapshot_repository.py`, duplicated here rather than shared), `get_total_market_value_for_snapshot`, `compute_total_equity`.
- Notable: `_cash_source_priority()`/`_position_source_priority()` helper functions are duplicated verbatim (same case-expression shape) across this file and `cash_snapshot_repository.py` — the LEDGER-over-BROKER_RECONCILED tie-break rule is copy-pasted rather than shared from one place, so a future change to source priority must be made in both places (and in fact a third near-copy exists in `runtime_soak_verification_repository.py`, see below).

### src/autonomous_trading_platform/storage/sor/repositories/queries/recent_activity_repository.py (176 lines)
- Purpose: `RecentActivityRepository.list_recent_activity` — merges five independently-queried, independently-limited event streams (audit log, fills, risk alerts, strategy state changes, broker order updates) in Python, sorts by timestamp, and truncates to the requested limit — for the dashboard "recent activity" feed.
- Notable: Because each of the five sub-queries is independently limited to `limit` rows *before* the merge-and-truncate step, a burst of one event type (e.g. many fills) can crowd out genuinely more-recent events of another type that fell outside that sub-query's own top-N — the final N is not guaranteed to be the true top-N across all five streams combined.

### src/autonomous_trading_platform/storage/sor/repositories/queries/runtime_soak_verification_repository.py (208 lines)
- Purpose: `RuntimeSoakVerificationRepository` — by far the largest single read-repository, backing the runtime soak-verification framework: job health/staleness, data freshness, concurrent-execution detection, order reconciliation (stale submitted orders, missing broker status, status mismatches), duplicate-fill detection (three different dedup keys: broker fill id, order+execution id, idempotency key), cash/position/equity consistency, observability signal existence, and failure-control checks — all organized under literal `# TASK-5xx` comment headers matching the tasks that introduced them.
- Notable: Contains a third near-duplicate of the `_cash_source_priority()` case-expression (see `portfolio_summary_repository.py` above) — now copy-pasted three times across the codebase. `get_current_trading_freeze_state` is a stub returning `None` unconditionally with a comment "No persisted trading freeze model exists yet" — an acknowledged gap in the soak-verification coverage.

### src/autonomous_trading_platform/storage/sor/services/__init__.py (0 lines)
- Purpose: Empty package marker.

### src/autonomous_trading_platform/storage/sor/services/corporate_action_query_service.py (36 lines)
- Purpose: `CorporateActionQueryService` — thin composition of `HistoricalUniverseFilterService` (survivorship-bias-aware symbol resolution for a date) with `CorporateActionRepository.get_actions_for_symbols_between`.
- Notable: This and `market_bar_query_service.py` are the only two storage-layer services that import from `universe/services/` — a storage→universe dependency, again outward of the documented inward-only layering, needed here specifically to make historical queries survivorship-bias-safe.

### src/autonomous_trading_platform/storage/sor/services/market_bar_query_service.py (36 lines)
- Purpose: `MarketBarQueryService` — same composition pattern as `corporate_action_query_service.py`, for `market_bars`.

### src/autonomous_trading_platform/storage/sor/services/order_execution_service.py (24 lines)
- Purpose: `OrderExecutionService.persist_order_bundle` — the canonical example of `SorUnitOfWork`'s intended usage: upserts an order intent, broker order, and N fills inside one `with SorUnitOfWork(...)` block so they commit atomically together.

### src/autonomous_trading_platform/storage/sor/services/raw_market_pool_query_service.py (92 lines)
- Purpose: `RawMarketPoolQueryService` — structural-typing `Protocol` (`RawMarketPoolReader`) rather than a concrete repository import, so it can be tested/substituted independently; `get_active_tradable_symbols`, `is_symbol_eligible`, `get_symbol_count`.
- Notable: The only service in this directory built against a `Protocol` interface instead of a concrete repository class — a stronger dependency-inversion pattern than every other service/repository pairing in the storage layer, but applied to only this one file.

### src/autonomous_trading_platform/storage/sor/services/unit_of_work.py (147 lines)
- Purpose: `SorUnitOfWork.__init__` instantiates exactly **30** repository attributes onto one shared `Session` (verified by direct count of this file's lines 100-129); `__enter__`/`__exit__` implement the commit-on-success/rollback-on-exception context manager.
- Notable: **Confirms and extends the "~29 repositories" claim verified at the top of this document** — the number is 30. More importantly, this file demonstrates the wiring is selective, not comprehensive: of the 63 repository classes under `sor/repositories/core/` (plus `portfolio_construction_repository.py`), only 30 are UoW members. Entirely absent from `SorUnitOfWork`: `OperatorSettingsRepository`, `KillSwitchStateRepository`, `RuntimeControlStateRepository`, `PromotionRulesRepository`, `CapitalAllocationPoliciesRepository`, `GovernanceRepository`, `GovernanceAuditRepository`, `AllocationOverridesRepository`, `AllocationRebalanceHistoryRepository`, `UniverseRebalanceRepository`, `UniverseRotationRepository`, `PortfolioConstructionRepository`, `PortfolioDrawdownGovernanceRepository`, `DrawdownGovernanceLadderStateRepository`, `DrawdownGovernanceLadderTransitionRepository`, `StrategyHealthStateRepository`, `StrategyHealthTransitionRepository`, `StrategyQualityScoreRepository`, `StrategyRuntimeStateRepository`, `LivePerformanceSnapshotRepository`, `OptimizerRunRepository`, `RiskBudgetSnapshotRepository`, `FactorExposureSnapshotRepository`, `FactorNeutralizationRepository`, `CorrelationSnapshotRepository`, `BlackLittermanResearchRepository`, `BlendedMetricsRepository`, `ShadowRunRepository`, `ShadowDivergenceRepository`, `ShadowComparisonSnapshotRepository`, `RuntimeJobRunRepository`, `RuntimeJobRunStepRepository`, `FillQualityMetricsRepository`. Any code path that needs atomic multi-table writes across, e.g., `strategy_health_states` + `strategy_health_transitions` (a natural pairing — see both repositories' near-identical shapes above) gets no such guarantee from this UoW; each repository is instead constructed ad hoc with its own session reference by its caller.

### src/autonomous_trading_platform/storage/sor/services/universe_snapshot_service.py (28 lines)
- Purpose: `UniverseSnapshotService` — query-only wrapper (`get_snapshot_for_date`, `is_symbol_eligible`) over the deprecated `universe_snapshots` table via `SorUnitOfWork`.
- Notable: File header explicitly marks this **DEPRECATED**, superseded by `UniverseVersionQueryService` — the third file in the `UniverseSnapshot` deprecation chain (model, repository, service all marked dead but still present and importable).

### src/autonomous_trading_platform/storage/sor/services/universe_version_query_service.py (26 lines)
- Purpose: `UniverseVersionQueryService` — the current (non-deprecated) replacement for `UniverseSnapshotService`: `get_symbols_for_date` resolves the active `UniverseVersion` as-of a date and returns its member symbols; `is_symbol_eligible`.

## Standout candidates

- **`storage/sor/repositories/core/universe_version_repository.py`** — the only repository encoding a genuine state machine (candidate → active → retired) with custom exceptions (`ImmutableVersionError`, `InvalidStatusTransitionError`) guarding immutability of active versions and validating transitions via `is_valid_transition`. Most sibling repositories are plain CRUD.
- **`storage/sor/repositories/core/position_snapshot_repository.py`** — `get_or_create_header`'s SAVEPOINT + `IntegrityError` fallback-to-SELECT is the most carefully-reasoned concurrency handling in the storage layer, gracefully resolving a real race (concurrent fills in the same 5-minute bar) without poisoning the outer transaction.
- **`storage/sor/repositories/queries/active_strategies_repository.py`** — the most elaborate read-model assembly (325 lines), joining five different concerns into one dashboard row with a documented-in-code (but not in docstrings) three-tier capital-allocation precedence rule.
- **`storage/sor/repositories/queries/runtime_soak_verification_repository.py`** — the single largest repository (208 lines), systematically covering job health, data freshness, order reconciliation, three independent duplicate-fill detection strategies, and observability-signal existence checks, each traceable to a `# TASK-5xx` origin comment.
- **`storage/sor/repositories/core/feature_dataset_versions_repository.py`** — largest repository file overall (278 lines); `find_for_simulation`/`find_matching_dataset` implement real feature-lineage resolution logic (coverage + symbol-count + optional parameter matching) that would more conventionally live in a service layer.

## Gaps/smells

- **`SorUnitOfWork` wires only 30 of ~64 SoR repositories** (see `unit_of_work.py` entry above for the full list of excluded repositories). Everything related to governance, shadow validation, drawdown-ladder state, strategy health lifecycle, risk budgeting, factor exposure/neutralization, correlation/covariance, optimizer runs, and job-run tracking is instantiated ad hoc outside the UoW, so multi-table writes in those domains (e.g. health-state + health-transition, ladder-state + ladder-transition) have no atomicity guarantee from the UoW pattern that exists specifically to provide it.
- **Repositories that commit their own transactions**: `OperatorSettingsRepository` (`get_or_create_default`, `update_current`), `TickerLifecycleRepository.upsert` (which *is* wired into `SorUnitOfWork`, making it actively dangerous if called inside a `with SorUnitOfWork(...)` block — it would prematurely commit the shared transaction), and `RuntimeJobRunRepository.save`. Every other repository in the layer correctly leaves commit/rollback to the caller.
- **Two `upsert()` methods are no-ops on the update path**: `ShadowRunRepository.update()` and `StrategyRuntimeStateRepository.upsert()` both flush without copying any fields from the passed-in row onto the existing tracked instance — silently relying on the caller having mutated the ORM object in place beforehand. Same bug shape, two independent files; likely to bite a future caller who assumes `upsert(new_row)` behaves like every other repository's field-by-field upsert.
- **Copy-pasted unfilled docstring template** (`"Repository for interacting with the <table_name> table... <ModelName>"`) shipped verbatim into 6 production files: `broker_order_repository.py`, `cash_snapshot_repository.py`, `fill_repository.py`, `order_intent_repository.py`, `position_snapshot_repository.py`, `signals_repository.py`.
- **`_cash_source_priority()` / `_position_source_priority()` tie-break logic duplicated three times** across `cash_snapshot_repository.py`, `portfolio_summary_repository.py`, and `runtime_soak_verification_repository.py` instead of being shared from one place — a source-priority policy change requires editing three files in lockstep.
- **A complete, live deprecated chain**: `UniverseSnapshot` model + `UniverseSnapshotRepository` + `UniverseSnapshotService` are all explicitly marked "DEPRECATED... do not use in new code" in file-header comments, yet remain fully present, importable, and presumably still exercised by whatever legacy callers haven't migrated to `UniverseVersion`/`UniverseVersionRepository`/`UniverseVersionQueryService`.
- **`MarketBarRepository.to_contract` always returns `quality_flags=[]`** — a real (if minor) information-loss bug: any quality flags persisted via `_to_row`/`upsert` are silently dropped on every read back through `to_contract`.
- **Two count-via-full-fetch patterns**: `ShadowDivergenceRepository.count_exceedances` and any caller relying on `ShadowRunRepository.count_passing_cycles` fetch up to thousands of full ORM rows into Python just to call `len()`, instead of a `SELECT count(*)`.
- **`OperationsRepository._list_latest_runs_by_job_name`** fetches every row of `runtime_job_runs` unbounded to compute a per-job-name "latest" in Python — will not scale with job-run history growth; a `DISTINCT ON` (Postgres) or window-function query is the correct tool and isn't used anywhere in this codebase for this pattern.
- **`RecentActivityRepository.list_recent_activity`** independently limits each of 5 event-type sub-queries to `limit` rows *before* merging and re-truncating — the final top-N is not guaranteed to be the true top-N across all 5 streams; a burst in one event type can crowd out genuinely more-recent events of another type.
- **Storage → execution / storage → universe reverse dependencies**: `strategy_runtime_states.py` imports `StrategyState` from `execution/services/strategy_state_machine_service.py`, and `corporate_action_query_service.py`/`market_bar_query_service.py` import from `universe/services/`. Both cut against the documented strict inward-only layering (though `execution` and `universe` aren't formally part of the `interfaces → application → domain → storage → contracts` chain, coupling the ORM/storage layer to sibling-layer code is still a layering smell worth flagging).
- **`PortfolioConstructionRepository._persist_batch_items` is a documented no-op**: `persist_result()` (the "do everything" entry point) silently skips writing `PortfolioSignalBatchItemRow`s unless the caller separately invokes `persist_raw_signals()` — an easy-to-miss two-call contract for what looks like a single atomic persist operation.
- **`SignalRepository` double-imports the same ORM class** under two names (`Signal` and `Signal as SignalRow`) in the same file — harmless but sloppy.
- **`GovernanceAuditRepository._build_row`** is a large static factory duplicating the row constructor's full field list — unclear caller (not visible in-file), and a duplicate-maintenance point if `GovernanceAuditEventRow` gains/loses fields.

## Coverage: read 170 of 170 (100%)

No files skipped. This third pass read the 102 files left uncovered after the prior two passes (all of `storage/sor/repositories/core/` except the 5 read earlier, all of `storage/sor/repositories/queries/`, all of `storage/sor/services/`, `storage/sor/repositories/base.py`/`__init__.py` files, `storage/sor/__init__.py`, `storage/sor/repositories/portfolio_construction_repository.py`, and the remaining 20 `storage/sor/models/*.py` files from `runtime_soak_reports.py` through `universe_versions.py`), reconciled against `find src/autonomous_trading_platform/storage -name '*.py' | sort` (170 files) with zero remaining gaps.
