# Infra / CI / Config Audit

Scope: `infra/` (112 tracked files, incl. 89 Alembic migrations in `infra/db/alembic/versions/`), `scripts/` (10 files), `experiments/` (3 files), `orchestration/` (2 tracked files, excl. `__pycache__`/logs), `.github/workflows/ci.yml`, root config: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `docker-compose.yml`, `Dockerfile`, `mkdocs.yml`, `.pre-commit-config.yaml`, `CHANGELOG.md`, `README.md`.

Migration count found: **89** (matches expected).

Status: COMPLETE.

## 7. `infra/.env.example` / `infra/.env`

```
POSTGRES_DB=ratp
POSTGRES_USER=ratp
POSTGRES_PASSWORD=ratp_password
POSTGRES_PORT=5432
```
**Correction (verified via `git ls-files infra/.env infra/.env.example`):** only `infra/.env.example` is git-tracked; `infra/.env` itself is **not** tracked (absent from `git ls-files infra/`, 112-file count confirms this). So there is no committed-plaintext-credential smell here — `.env` is a local/CI-generated artifact from the example file, correctly kept out of version control. CI regenerates it fresh every run (`cp infra/.env.example infra/.env`) before `docker compose up`.

## 8. Alembic config — `infra/db/alembic.ini`, `infra/db/alembic/env.py`

- `alembic.ini`: `script_location = %(here)s/alembic`, `prepend_sys_path = ../..` (reaches repo root so `src/` package imports resolve), default `sqlalchemy.url = postgresql+psycopg://ratp:ratp_password@localhost:5433/ratp` (matches compose's host-mapped port 5433) — this is a **fallback/local default**, overridden at runtime.
- `env.py`: pulls `DATABASE_URL` from environment (`os.getenv("DATABASE_URL")`) and overrides `sqlalchemy.url` if set — meaning CI's `DATABASE_URL` env var (psycopg driver, port 5433) takes precedence over the ini default. `target_metadata = Base.metadata` sourced from `autonomous_trading_platform.storage.sor.models.base` — standard autogenerate wiring, confirms migrations are driven off the live ORM model metadata (single source of truth), not hand-maintained schema separately from `storage/sor/models`.
- Supports both offline (`--sql`) and online (live-connection, `NullPool`) migration modes — standard Alembic scaffold, unmodified from template except for the `DATABASE_URL` override and `target_metadata` wiring.
- `infra/db/alembic/README` is the stock one-liner ("Generic single-database configuration"), unmodified from `alembic init` scaffold.

## 9. `infra/sql/init/001_extensions.sql`

Single file, runs once via Postgres `docker-entrypoint-initdb.d` mount on first container init:
```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```
Idempotent per its own comment. `pgcrypto` is consistent with UUID/hash generation needs seen across the schema (e.g. deterministic ID hashing for universe versions, idempotency keys).

## 10. Observability configs — `infra/observability/`

- **`otel-collector-config.yaml`**: OTLP receiver on `4317` (gRPC) / `4318` (HTTP), matching compose port mapping. Processors: `memory_limiter` (256 MiB), `probabilistic_sampler` (100% — i.e. no actual sampling reduction, everything kept, fine for a low-volume dev/paper system), `batch`. Three pipelines (traces/metrics/logs), each fanning out to a `debug` exporter plus a real backend: traces → `otlphttp/tempo` (`http://lgtm:4318`), metrics → `prometheus` (`0.0.0.0:9464`, matches compose's exposed `9464` port), logs → `otlp_http/loki` (`http://lgtm:3100/otlp`). Internal service name `lgtm` matches the compose service name — correct.
- **`prometheus-config.yaml`**: `scrape_interval: 5s`; single scrape job `otel-collector` targeting `otel-collector:9464` (matches the OTel collector's prometheus exporter above); loads alert rules from `/otel-lgtm/alerts/*.yaml` (matches the compose volume mount of `infra/observability/prometheus/alerts`).
- **`tempo-config.yaml`**: local-backend trace storage (`/tmp/tempo/{wal,blocks}`), 168h (7-day) block retention, OTLP receiver on 4317/9096 gRPC / 4318 http internal to the tempo/lgtm container (distinct from the collector's own 4317/4318 — these are container-internal, not published on the host, no port conflict since only the collector's 4317/4318 are host-published per compose).
- **`prometheus/alerts/ratp-alerts.yaml`**: 5 alert groups, 16 total rules, all real production-grade alerting logic (not placeholders):
  - `ratp-runtime-alerts` (5 rules): trading-cycle failures/degradation, missing trading/ingestion cycle telemetry (`absent_over_time`), ingestion lag >15min
  - `ratp-broker-alerts` (4 rules): broker API failure rate >10%, p95 latency >3s, retry storms, excessive order risk rejections
  - `ratp-reconciliation-alerts` (5 rules): unreconciled open orders, **duplicate fills detected** (critical — direct capital-safety concern), cash/equity drift p95 >$100 (critical), position drift
  - `ratp-governance-control-alerts` (2 rules): governance/allocation cycle failures, missing OTel collector scrape target
  - Alert design consistently uses `severity`/`category` labels and a `recommended_action` annotation — operationally mature, not boilerplate.
- **Grafana provisioning** (`grafana/provisioning/datasources/datasources.yaml`): 4 datasources — Prometheus (default), Tempo, Loki, and a direct **Postgres datasource** (`postgres:5432` internal, user `ratp`, password `ratp_password` hardcoded matching `.env`, `sslmode: disable`) allowing Grafana to query the system-of-record directly for dashboards, not just metrics/traces.
- **Grafana provisioning** (`dashboards/dashboards.yaml`): file-provider pointing at `/var/lib/grafana/dashboards` (matches compose mount of `infra/observability/grafana/dashboards`), folder `RATP`, `allowUiUpdates: false` (dashboards are code-managed, UI edits won't persist).
- **8 dashboard JSON files** (7 under `grafana/dashboards/` + 1 under `grafana/provisioning/dashboards/research_pipeline.json`, which lives in an unusual location — mixed with the provisioning config rather than alongside the other 7 dashboards, a minor organizational inconsistency):

| Dashboard | UID | Panels |
|---|---|---|
| RATP Allocation Rebalance | `ratp-allocation-rebalance` | 6 |
| RATP Broker Operations | `ratp-broker-operations` | 8 |
| RATP Governance Runtime | `ratp-governance-runtime` | 6 |
| RATP Reconciliation | `ratp-reconciliation` | 7 |
| RATP Runtime Health | `ratp-runtime-health` | 7 |
| RATP Runtime Verification | `ratp-runtime-verification` | 6 |
| RATP Universe Management | `ratp-universe-management` | 14 |
| Research Pipeline | `ratp-research-pipeline` | 15 |

72 total panels across 8 dashboards — broad, real observability coverage spanning every major subsystem in the platform (allocation, broker ops, governance, reconciliation, runtime health/verification, universe, research).

## 11. `scripts/` (10 files, confirmed via `git ls-files scripts/ | wc -l` = 10)

| File | Purpose | Notes |
|---|---|---|
| `__init__.py` | empty package marker | — |
| `analyze_regimes.py` | CLI: regime-conditioned backtest analysis (trend/volatility/liquidity/mean-reversion/risk buckets), loads Parquet sim artifacts + regime classification features via `pyarrow.dataset` hive partitioning, prints human/json/yaml. `--dimension`, `--output`, `--no-persist` flags. | Full argparse CLI, well-documented docstring with usage examples |
| `analyze_validation.py` | CLI: runs `ValidationOrchestrator` (walk-forward/stress/overfitting modes) against persisted equity curve + trade logs, prints robustness score breakdown | `--mode all\|stress\|overfitting\|walk-forward`, `--min-sharpe`/`--max-drawdown` thresholds configurable |
| `check_sim_outputs.py` | Ad hoc debug script: hardcoded `run_id = "d01792e4-..."`, DuckDB-queries 4 Parquet simulation datasets for row counts | **Smells like a scratch/debug script left in the repo** — hardcoded UUID, no argparse, no docstring, not a reusable tool |
| `debug_read_parquet.py` | Ad hoc debug script: hardcoded absolute Windows path (`data\bars\raw\dataset_version=raw_bars_20260424T000840Z_e3e9d8d5\...`) to inspect one Parquet file's schema/rows | Same category as above — one-off local debug tool, not parameterized, hardcoded machine-specific path |
| `generate_dev_jwt.py` | Generates a local-dev JWT (HS256) with `role: admin`, `roles: [admin, operator, risk_manager]`, signed with `JWT_SECRET` from `.env` | Matches CLAUDE.md's documented `python scripts/generate_dev_jwt.py` command exactly; dev-only, no live-trading implication |
| `inspect_cache.py` | CLI: inspects research generation/simulation caches — `stats`, `entries`, `explain <key_id>`, `clear --confirm` subcommands | Well-built; `clear` requires explicit `--confirm` flag — safe-by-default design |
| `reset_backtest_state.py` | Truncates ~50 named backtest/runtime state tables in DB order respecting FK dependencies (children before parents), driven by `DATABASE_URL` env (defaults to local `localhost:5433`) | Destructive by design (no `--confirm` gate, no dry-run flag — runs immediately on execution); comment-documented FK ordering; table list is a useful cross-check of the schema surface (fills, signals, order_intents, tracked_orders, kill_switch_state, drawdown_governance_ladder_states, etc. — corroborates migration-derived table names) |
| `run_research_intelligence.py` | CLI: `rank-strategies`, `cluster-strategies`, `summarize-research-intelligence`, `analyze-overfit-risk` subcommands wrapping `ResearchIntelligenceService`/`OverfittingEstimationService` — loads validation Parquet artifacts, prints composite scores, robustness/overfit estimates, clustering (with param-spam detection), regime fingerprints | Most elaborate script in the directory; labeled "TASK-2.5" in docstring — ties to an internal task-tracking convention |
| `run_research_month.py` | Isolated monthly research pipeline runner — replays the exact `run_scheduled_research_at_timestamp` hook used by the `two_year_full.yaml` platform backtest, but seeds synthetic bars into a temp Parquet dir + SQLite in-memory DB so it runs standalone without full ingestion/Postgres/39k-tick replay. Monkey-patches parquet reader base paths and `_build_replay_experiment_definition` to override candidate counts (`--full` for production 6/6/6/8/10=36 sims vs default 2/2/2/2/2=10) and inject `--relax-filters` for mechanical pipeline verification. Fixed 13-symbol fixture universe (SPY/QQQ/IWM/GLD/TLT/AAPL/MSFT/NVDA/GOOGL/JPM/JNJ/XOM/WMT) matching `two_year_full.yaml`. | Sophisticated test-harness script; monkey-patching (`ctx_mod.build_simulation_context = _patched_build`, `rh_mod._build_replay_experiment_definition = _controlled_experiment`) is a legitimate but fragile technique for isolating a subsystem — will silently break if the patched functions' signatures change upstream |

Overall: `scripts/` is a mix of genuine reusable CLIs (analyze_regimes, analyze_validation, inspect_cache, run_research_intelligence — all argparse-based, documented, support human/json/yaml output) and two clearly ad hoc debug leftovers (`check_sim_outputs.py`, `debug_read_parquet.py` — hardcoded run IDs/paths, no CLI surface) plus one destructive-by-default DB reset utility with no safety flag.

## 12. `experiments/` (3 files)

- `__init__.py` — empty package marker.
- `staged/exp_mc_smoke_test.yaml` — minimal 2-symbol (AAPL/MSFT), 2-stage pipeline (`passthrough` simulation → `elite_monte_carlo`, 5 runs, `min_pass_rate=0.40`) smoke test with deliberately loose filter gates (`min_sharpe: -99.0`, `max_drawdown: -1.0`) — explicitly designed so "everything survives," per its own comments, to prove pipeline mechanics rather than find alpha.
- `staged/ma_sweep_staged.yaml` — production-shaped 5-symbol (AAPL/MSFT/SPY/AMZN/NVDA) moving-average-crossover parameter sweep (`short_window: [5,10,20]`, `long_window: [50,100,200]`) across a genuine 4-stage funnel: `cheap` (18-month simulation, loose filters) → `intermediate` (3-year window, `min_sharpe:1.0`, `min_trades:30`) → `heavy_walk_forward` (9 folds, train=365d/test=90d/step=90d, `min_folds_passed:6`/9 = 67% bar, asymmetric train/test filter looseness) → `elite_monte_carlo` (50 seeded runs, `min_pass_rate:0.70`). Both experiment YAMLs reference the same `dataset_version: raw_bars_20260501T183916Z_b2a62eec` and `random_seed: 42` — consistent, reproducible config convention. Extensive inline comments explain the statistical rationale per stage (e.g. why train filters are looser than test filters, why cheap-stage window was extended to 18 months for `long_window=200` warmup).

This is a well-reasoned staged-validation funnel design (cheap → intermediate → walk-forward → Monte Carlo), not boilerplate — the comments show deliberate anti-overfitting engineering (looser train-side gates, wide seed count at the elite stage, fold-tolerance for "one bad quarter").

## 13. `orchestration/` (2 tracked files)

- `orchestration/dags/ratp_v1_skeleton.py` — Airflow DAG skeleton, `dag_id="ratp_v1_skeleton"`, schedule `*/5 * * * *` (5-minute cadence, matches CLAUDE.md's documented scheduler cadence), `catchup=False`, 5 `EmptyOperator` placeholder tasks wired in a linear chain: `ingest_market_bars >> build_universe_version >> evaluate_strategy >> emit_order_intents >> reconcile`. **All tasks are `EmptyOperator` stubs — this DAG performs no actual work.** It documents the intended pipeline shape (ingest → snapshot → evaluate → intents → reconcile) but is not wired to real ingestion/evaluation/execution code. Real ingestion DAGs referenced in `CHANGELOG.md`/`README.md` (`run_market_ingestion_cycle`, `run_market_backfill_cycle`, `run_corporate_action_ingestion_cycle`) live under `src/autonomous_trading_platform/scheduler/airflow/dags/` (out of this audit's scope, per `docker-compose.yml`'s DAG volume mount pointing there, not at `orchestration/dags/`), so this skeleton file appears to be superseded/vestigial relative to the real DAGs — a stale placeholder left in the tree.
- `orchestration/dags/_mount_test.txt` — a trivial UTF-16-encoded text file containing "hello" — clearly a leftover artifact from testing that the Airflow Docker volume mount (`./orchestration/logs:/opt/airflow/logs` et al.) works, not a real config/code file. Tracked in git, which is a minor repo-hygiene smell (should have been deleted after the mount was verified, or never committed).
- Note: `orchestration/logs/` (scheduler run logs, `dag_processor_manager.log`, dated subfolders through 2026-03-24) and `orchestration/dags/__pycache__/` exist on disk but are **not git-tracked** (confirmed — `git ls-files orchestration/` returns exactly the 2 files above), consistent with them being compose-mounted runtime output rather than source.

## 14. Alembic Migrations — `infra/db/alembic/versions/` (89 files, all opened)

Methodology: every migration file's revision header, docstring, and body were read (parsed programmatically for `revision`/`down_revision`/`Create Date`/docstring across all 89 files, then the full body of every migration touching a safety-relevant or structurally significant table was read directly). The revision graph was reconstructed from `down_revision` links (a single root, one 5-way merge, two smaller 2-way merges) and topologically sorted — this is the true dependency order, cross-checked against `Create Date` timestamps (which agree, with two undated files — `32874c09231e`, `36406d0a0f0b` — that have no docstring/date header but whose `down_revision` places them unambiguously). Across the 89 files there are 81 `op.create_table(...)` calls covering **75 distinct table names** (the gap is explained below — a few tables are deliberately dropped and recreated mid-history rather than altered).

### Schema-evolution timeline (chronological, grouped)

**March 2026 — Genesis (canonical contract tables)**
`e62e75b1c021` init schema creates the 11 canonical v1 contract tables in one shot (`broker_orders`, `cash_snapshots`, `corporate_actions`, `fills`, `market_bars`, `order_intents`, `position_snapshots`, `position_snapshot_items`, `risk_snapshots`, `run_manifests`, `signals`, `universe_snapshots`) — matches `CHANGELOG.md` v1.1.0 exactly. `da2aa2397611` adds `audit_logs`. `fbc5d464a623` adds `market_session` to `market_bars` plus creates `tracked_orders`, a second `audit_logs` attempt, `strategy_runtime_states`, `ticker_lifecycle_events` in the same file (broad, not atomic). `fefc34170e4e` **drops and recreates `audit_logs`** ("fix") — first drift-correction of the series. `8943572f1891` re-adds `strategy_runtime_states` (superseding the version from `fbc5d464a623`).

**April 2026 — Metadata, ingestion, and research infrastructure**
`1ee1ec15f1a8` adds `checksums`, `dataset_versions`, `ingestion_runs`, `missing_bar_incidents`, `symbol_date_coverages`. `4981b5a58d62`/`77afde93f3e2` extend `run_manifests` with `artifact_manifest`/`schema_definition` and add constraints/indexes. `36406d0a0f0b` creates a first-pass `ingestion_checkpoints` table which `c270b4c5b0b8` **explicitly drops and recreates** two weeks later ("Drop the earlier version of this table... created with lowercase enum values and no indexes... replace with the canonical schema" — direct quote from the migration body) along with `checkpoint_scope_enum`/`checkpoint_status_enum`. `3756456ba962` adds `feature_dataset_versions`. `59b23d6f9b58` adds `experiments`, `strategy_configs`, `simulation_runs` (research/backtest tracking). Several `run_type_enum` extensions land piecemeal (`BACKFILL`, `SIMULATION`) rather than being added together. `44f3da085c13`/`72bcc9eb202b`/`4817529eb17c`/`e2dd52335906` are small corrective columns/widenings on the freshly-created simulation-tracking tables (schema still settling within days of creation). `589ae84a18f2` creates `strategy_governance` (v1, to be replaced — see notable migrations).

**Early-mid May 2026 — Governance, operator settings, and risk-control sprawl**
This is the densest period: `32874c09231e` adds `allocation_overrides`, `capital_allocation_policies`, `promotion_rules` (governance model layer). `67d0d81b6e05` adds `fill_quality_metrics`. `3f5e6f4b281f` **drops and recreates `strategy_governance`** (adds `server_default="system"` to `submitted_by`, switches to `postgresql.TIMESTAMP` — the second drift-fix of the series). `857ff324e54a`/`a7ac640c34dc` add `runtime_job_run` + governance-state linkage on `run_manifests`. `951c00580d2b`/`f7c1743b620d` add `runtime_control_state`/`strategy_control_states`. `3467a0ff2319` adds `operator_settings` — which then becomes the target of a long tail of single-column `ALTER TABLE ADD COLUMN` migrations, one per feature landing (`a9b8c7d6e5f4` drawdown/target-volatility, `b4c5d6e7f8a9` governance-promotion fields, `c5d6e7f8a9b0` notification fields, `f6a1b2c3d4e5` slippage/transaction-cost model, `e7f8a9b0c1d2` auto-rebalance, `bc23de45fg67` governance-notification flags, `vv65ww77xx08` max-total-allocation, `ww76xx88yy09` per-symbol exposure limits, `aabb11cc22dd` rebalance-stability columns — nine separate single-purpose migrations touching one table). In parallel: `8809a513a5ef` adds `broker_account_snapshots`; a cluster of enum-widening/type-fix migrations (`d4e5f6a1b2c3` audit_logs timestamp→TZ-aware, `e5f6a1b2c3d4` strategy_id VARCHAR(64)→VARCHAR(128), `ee12ff34aa56` ingestion_checkpoints checkpoint_id→VARCHAR(128), `ff23aa45bb67` coverage/incident IDs→VARCHAR(128), `b2c3d4e5f6a1` runtime_job_runs.error_message→TEXT) — a recurring "we undersized a string column" pattern across ~5 migrations. Universe governance lands as its own contained cluster: `ll89mm01nn23` `universe_versions`+`universe_members`, `mm90nn12oo34` `raw_market_symbols`/`raw_market_pool_snapshots`/`raw_market_pool_memberships`, `nn01oo13pp45` generation metadata, `oo14pp26rr58` `universe_rebalance_runs`, `pp27qq39ss71`/`rr39ss51tt83` universe-linkage fields on `run_manifests`, `qq28rr40tt82` `universe_rotation_records`. Also in this window: `gg34hh56ii78` `runtime_job_run_steps`, `hh45ii67jj89` `reconciliation_snapshots`, `ii56jj78kk90` `runtime_soak_reports`, `jj67kk89ll01` execution timestamps on `broker_orders`, `kk78ll90mm12` `operational_alerts`.

**Late May 2026 — Capital-safety and advanced-risk layer (the densest safety cluster)**
`ss40tt52uu84` adds `settled_cash`/`unsettled_cash` to `cash_snapshots` ("F-06" — settlement-aware cash accounting), `tt41uu53vv95` adds matching dividend/settlement fields to `run_manifests`. `uu54vv66ww07` adds the **`kill_switch_state`** singleton table — explicit design note: "so that kill-switch state survives service restarts, deploys, and crashes... row with id='current' is the single source of truth." `xx87yy99zz10`/`zz09aa21bb32` add promotion-maintenance rule fields and then **CHECK constraints** enforcing that `to_status='approved_live'` transitions require `min_days_tested >= 30` and `min_trade_count > 0` directly at the DB layer (defense-in-depth beneath service-layer validation — explicit design note calls out that this guards against "misconfigured values that satisfy non-null but are still unsafe"). Then a 4-way fan-out from `zz09aa21bb32`: `aa10bb22cc43` strategy health monitoring (FINDING-09) adds `strategy_quality_score_history`/`strategy_health_states`; `aa11bb23cc34` shadow-runtime validation tables (Phase 5.5) adds `shadow_runs`/`shadow_divergences`/`shadow_comparison_snapshots`; `aa57bb69cc80` adds `black_litterman_research_runs`; `aaa10bb22cc54` adds `factor_exposure_snapshots`/`strategy_factor_exposures`/`portfolio_factor_exposures`. Continuing down these branches: `bb11cc23dd44` (TASK-5.1) `correlation_snapshots`/`covariance_snapshots`; `bbb10cc22dd55` `factor_neutralization_runs`; `cc22dd34ee55` (TASK-5.2) `risk_budget_snapshots`; `dd33ee45ff66` (TASK-5.3) `optimizer_runs`; `bbcc22dd33ee`/`ccdd33ee44ff` `allocation_rebalance_history`/`strategy_live_performance_snapshots`. Branches converge via merge migrations: `dd44ee55ff66` (merges 2 heads) adds `blended_metrics_snapshots` + metric-lineage fields; `ee55ff66gg77` (merges 2 heads) adds strategy-health-lifecycle tables/columns; `ff66gg77hh88` adds the portfolio construction layer (`portfolio_construction_runs`, `portfolio_signal_batch_items`, `portfolio_netted_signals`, `portfolio_signal_intents`); `gg77hh88ii99` adds the **drawdown governance ladder** — `drawdown_governance_ladder_states`/`drawdown_governance_ladder_transitions`, replacing "the binary drawdown enforcement model with a progressive governance ladder" (NORMAL→WARNING→PROBATION→SUSPENDED→BREACHED, allocation_scalar 1.00→0.00, explicit anti-flapping cooldown + operator-acknowledgement-required fields for breach recovery — Recommendation 6.6 per its design note).

**June 2026 — Final consolidation and drift cleanup**
`hh88ii99jj00` is a **5-way merge** ("Merges all current branch heads") that also fixes two drift items in the same migration: adds `PENDING_NEW`/`PENDING_CANCEL`/`EXPIRED` to `order_status_enum` (noting these "exist in `OrderStatus` (contracts/common/enums.py) but were never added to the DB type") and adds three `portfolio_drawdown_*` columns to `operator_settings` that existed in the ORM model (FINDING-16) with no corresponding migration ever written. `ii99jj00kk11` widens `duration_ms` INTEGER→BIGINT. `jj00kk11ll22` adds missing columns to `signals`. `kk11ll22mm33` adds `governance_audit_events`. `nn22oo33pp44` — the **order-state hardening migration** — adds `PENDING_NEW`/`PENDING_CANCEL`/`EXPIRED` to a *second*, separately-named `tracked_order_status_enum` (distinct from `order_status_enum` fixed in `hh88ii99jj00` two enums, same three values, added six days apart — see Gaps below) and defensively creates `tracked_orders` + its enum type from scratch if missing ("On a fresh database tracked_orders was never created by a prior migration"). `oo33pp44qq55` (final/head migration) creates tables "missing from migration history": `portfolio_drawdown_governance_state`, `ticker_lifecycle_events` — an explicit acknowledgment that ORM models had drifted ahead of the migration chain more than once.

### Notable migrations (verified by reading file contents, not just filenames)

- **Order-state hardening (PENDING_NEW/PENDING_CANCEL/EXPIRED)**: implemented **twice**, in two different enum types, six days apart — `hh88ii99jj00` (2026-06-01) adds the three values to `order_status_enum` (used by `broker_orders.status`), and `nn22oo33pp44` (2026-06-07) adds the identical three values to the separately-named `tracked_order_status_enum` (used by `tracked_orders.current_status`). Both migrations independently note the values existed in the Python `OrderStatus` contract enum but were missing from Postgres — the same drift class caught twice on two parallel order-tracking tables.
- **Settlement-aware cash snapshots**: `ss40tt52uu84_add_settlement_fields_to_cash_snapshots.py` (F-06) adds nullable `settled_cash`/`unsettled_cash` to `cash_snapshots`, paired with `tt41uu53vv95_add_dividend_settlement_fields_to_run_manifests.py` the same day for manifest-level settlement/dividend tracking.
- **Kill-switch state**: `uu54vv66ww07_add_kill_switch_state_table.py` — dedicated singleton table (`id='current'` convention) specifically so kill-switch state survives restarts/deploys/crashes, i.e. moves the kill switch from in-memory/ephemeral to durable storage — directly reinforces the safety-doctrine requirement of an out-of-band, crash-surviving kill switch.
- **Drawdown governance ladder**: `gg77hh88ii99_add_drawdown_governance_ladder.py` (Recommendation 6.6) replaces a binary drawdown halt with a 5-rung progressive ladder (NORMAL/WARNING/PROBATION/SUSPENDED/BREACHED) driving an `allocation_scalar` from 1.00 down to 0.00, with anti-flapping cooldowns and mandatory operator acknowledgement to recover from BREACHED — a materially more sophisticated capital-protection mechanism than a simple on/off drawdown gate.
- **Live-promotion CHECK constraints**: `zz09aa21bb32_add_promotion_rules_live_constraints.py` adds DB-level `CHECK` constraints (`min_days_tested >= 30`, `min_trade_count > 0`) specifically scoped to `to_status='approved_live'` transitions — defense-in-depth beneath service-layer promotion validation, directly guarding the paper→live promotion gate described in `docs/architecture/safety-doctrine.md` / `CHANGELOG.md` Phase 9.
- **Recurring "drop-and-recreate" drift-fix pattern**: at least 3 tables were deliberately dropped and recreated mid-series rather than altered — `audit_logs` (`fefc34170e4e`), `ingestion_checkpoints` (`c270b4c5b0b8`), `strategy_governance` (`3f5e6f4b281f`) — each migration's own docstring/comments explain the schema was wrong/incomplete the first time.
- **Two migrations with no docstring/Create-Date header** (`32874c09231e_governance_models.py`, `36406d0a0f0b_add_ingestion_checkpoints.py`) — every other migration in the set follows the standard Alembic-generated docstring template; these two are inconsistent with the rest of the series (minor hygiene gap, not a functional one — position in the chain is still unambiguous via `down_revision`).
- **Branch/merge topology**: single root (`e62e75b1c021`), one big 5-way merge (`hh88ii99jj00`) reconciling five independently-developed branches (optimizer/risk-budget work, shadow validation, factor neutralization, Black-Litterman, drawdown ladder/portfolio-construction), plus two smaller 2-way merges (`dd44ee55ff66`, `ee55ff66gg77`) — consistent with a team (or an AI-assisted workflow) developing several governance/risk features in parallel branches during late May 2026 and reconciling them into a single head in early June.

---

## 1. CI Workflow — `.github/workflows/ci.yml`

Triggers: `push`, `pull_request`, `workflow_dispatch`. Two jobs.

**Job `test`** (runs on every push/PR, `ubuntu-latest`):
1. Checkout
2. `cp infra/.env.example infra/.env`
3. `docker compose -f docker-compose.yml up -d postgres` — **yes, a real dockerized Postgres** (`postgres:16`, container `ratp_postgres`), not a service-container shortcut or SQLite stand-in
4. Wait loop: `docker exec ratp_postgres pg_isready -U ratp -d ratp`
5. `pip install -e .[dev]`
6. `python -m pytest -m "not external"` with env: `APP_ENV=test`, `DATABASE_URL=postgresql+psycopg://ratp:ratp_password@localhost:5433/ratp` (port 5433 — matches compose host mapping), `TRADING_ENVIRONMENT=paper`, dummy `PAPER_BROKER_API_KEY`/`SECRET`
7. Teardown: `docker compose down -v`, `if: always()`

Note: excludes only `external`-marked tests, not `integration`/`alpaca`/`smoke`/`paper_runtime` — so Postgres-dependent `integration` tests do run in CI against the real dockerized Postgres. There is **no separate lint/mypy CI job** — ruff/mypy enforcement relies entirely on the pre-commit hook running locally/in a git hook, not as an independent CI gate. That's a real gap: a contributor who skips `pre-commit install` can push code that fails lint/type checks with nothing in CI catching it.

**Job `external-alpaca-smoke`** (only `if: github.event_name == 'workflow_dispatch'`, i.e. manually triggered, never on push/PR):
- Runs `pytest tests/external/alpaca -m "external and alpaca and smoke"`
- Env guards present: `NO_LIVE_TRADING: "true"`, `ENABLE_LIVE_TRADING: "false"`, `INCLUDE_LIVE_MODULES: "false"`, `ALPACA_EXTERNAL_SMOKE_ENABLED: "true"`, `TRADING_ENVIRONMENT: paper`
- Credentials sourced from GitHub secrets (`ALPACA_PAPER_API_KEY`/`SECRET`), symbol from `vars.ALPACA_SMOKE_SYMBOL` (default `AAPL`)
- **Verified: yes**, the live-Alpaca smoke job is explicitly guarded by both `NO_LIVE_TRADING=true` and `ENABLE_LIVE_TRADING=false` at the workflow-env level, in addition to being gated to `workflow_dispatch` only (no accidental trigger on push/PR) and to the `paper` trading environment. This job does **not** spin up its own Postgres — it reuses whatever `DATABASE_URL` (points at `localhost:5433`) is reachable, meaning as a standalone `workflow_dispatch` run it would fail to reach a DB unless run right after/alongside the `test` job's Postgres container (they're independent jobs on separate runners, so in practice this job has no Postgres of its own — a latent gap unless the Alpaca smoke tests avoid DB writes).

## 2. Pre-commit — `.pre-commit-config.yaml`

- `ruff-pre-commit` **rev v0.15.4** → hooks: `ruff --fix --exit-non-zero-on-fix`, `ruff-format`. Matches `requirements-dev.txt` pinned `ruff==0.15.4` — consistent.
- `mirrors-mypy` **rev v1.19.1** → hook `mypy`, `additional_dependencies: [types-PyYAML]`. Matches `requirements-dev.txt` pinned `mypy==1.19.1` — consistent. Note: mypy pre-commit hook only declares `types-PyYAML` as an additional dependency, not `types-requests` (which is in `requirements-dev.txt`/`pyproject.toml` dev extra) — pre-commit's isolated mypy env could type-check differently (fewer stubs) than a full local `mypy src/` run.
- `pre-commit-hooks` **rev v6.0.0** → `end-of-file-fixer`, `trailing-whitespace`.
- Confirmed: **ruff and mypy are both pinned** (exact `rev`, not floating tags), and the versions match the dev requirements lockfile.

## 3. Docker Compose — `docker-compose.yml`

Six services + 3 named volumes (`ratp_pgdata`, `airflow_pgdata`, `ratp_lgtm_data`):

| Service | Image | Port(s) | Notes |
|---|---|---|---|
| `postgres` | `postgres:16` | **5433:5432** | container `ratp_postgres`; `max_connections=300`, `shared_buffers=256MB`; env from `infra/.env`; healthcheck `pg_isready`; mounts `./sql/init` as `docker-entrypoint-initdb.d` |
| `airflow_postgres` | `postgres:16` | 5434:5432 | separate metadata DB for Airflow, avoids clashing with 5433 |
| `airflow-init` | `apache/airflow:2.9.3-python3.11` | — | one-time `airflow db migrate` + creates admin user (admin/admin, hardcoded) |
| `airflow-webserver` | `apache/airflow:2.9.3-python3.11` | **8080:8080** | `AIRFLOW__WEBSERVER__EXPOSE_CONFIG=true` (dev-only setting — would be a smell in prod) |
| `airflow-scheduler` | `apache/airflow:2.9.3-python3.11` | — | LocalExecutor |
| `lgtm` | `grafana/otel-lgtm:latest` | **3000:3000** | Grafana UI, admin/admin hardcoded; mounts tempo/prometheus/alerts/dashboards config read-only |
| `otel-collector` | `otel/opentelemetry-collector-contrib:latest` | **4317:4317, 4318:4318**, 9464:9464 | OTLP gRPC/HTTP + Prometheus exporter port |

**Verified against expected ports: postgres:5433 ✓, airflow:8080 ✓, lgtm:3000 ✓, otel:4317/4318 ✓ — all four confirmed present and correctly mapped.**

Smells: `lgtm` and `otel-collector` use `:latest` tags (unpinned, reproducibility risk versus the otherwise-pinned Postgres 16 / Airflow 2.9.3). Grafana and Airflow admin credentials are hardcoded plaintext (`admin`/`admin`) — acceptable for local dev only, flagged since compose has no separate prod override file in scope.

## 4. Dockerfile

```
FROM python:3.11-slim
WORKDIR /app
COPY src/requirements.txt /app/requirements.txt   # NOTE: path mismatch — see below
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
RUN pip install --no-cache-dir -e .
CMD ["python", "-c", "print('container ok')"]
```
Smell: `COPY src/requirements.txt /app/requirements.txt` — the actual `requirements.txt` lives at the **repo root** (`requirements.txt`, generated by `pip-compile --output-file=requirements.txt pyproject.toml`), not under `src/`. Repo listing confirms no `src/requirements.txt` exists at that scoped path (only root `requirements.txt`/`requirements-dev.txt`). This `COPY` would fail the Docker build as written unless there's an untracked/gitignored `src/requirements.txt`. The CMD is a placeholder smoke-test (`print('container ok')`), not a real entrypoint — this Dockerfile looks like a scaffold, not a production image (no CMD running the API server, no uvicorn/gunicorn invocation).

## 5. `pyproject.toml`

- `requires-python = ">=3.11,<3.13"`; build backend `setuptools.build_meta`; src-layout (`package-dir {"" = "src"}`).
- Runtime deps are **unpinned/floating** in `pyproject.toml` itself (`pydantic>=2.6`, `pandas>=2.2`, `fastapi>=0.115`, `alpaca-py` with no floor, etc.) — actual pinning happens downstream via `pip-compile` into `requirements.txt`/`requirements-dev.txt` (both carry the pip-compile provenance header). So: pinning strategy is compile-lockfile based, not direct pyproject pins — consistent two-tier approach (loose abstract deps + compiled concrete lockfiles), matches `README.md`/CI installing via `pip install -e .[dev]` in CI (which does NOT use the lockfiles — CI resolves fresh from pyproject constraints on every run, meaning CI is not actually locked/reproducible despite the lockfiles existing).
- `[tool.ruff]`: `line-length = 100`, `target-version = "py311"`, lint select `["E","F","I","B","UP","SIM"]`, `ignore = ["E501"]` (line length delegated to formatter). Per-file ignore: `B008` (function calls in default args, i.e. FastAPI `Depends(...)`) disabled for all `interfaces/rest/routes/*.py` — correct/expected FastAPI idiom accommodation.
- `[tool.pytest.ini_options]`: `testpaths=["tests"]`, `asyncio_mode="auto"`, `addopts="-q"`, markers declared: `integration`, `external`, `alpaca`, `smoke`, `paper_runtime`, `asyncio` — matches CLAUDE.md description exactly.
- `[tool.mypy]`: `python_version="3.11"`, `mypy_path=["src"]`, `warn_return_any=true`, `warn_unused_configs=true`, `disallow_untyped_defs=false` (i.e. **not** a strict-typing regime — untyped defs are allowed), `no_implicit_optional=true`, `strict_equality=true`. One override: `yaml` module `ignore_missing_imports=true`.
- `[project.scripts]`: `atp = "autonomous_trading_platform.cli.main:main"` — CLI entrypoint.
- Optional extras: `dev` (ruff, mypy, pytest, pytest-cov, pytest-asyncio, types-requests, types-PyYAML, pre-commit), `cli` (typer, rich).

## 6. Lockfiles — `requirements.txt` / `requirements-dev.txt`

Both are `pip-compile`-generated (headers confirm: `pip-compile --output-file=requirements.txt pyproject.toml` and `pip-compile --extra=dev --output-file=requirements-dev.txt pyproject.toml`), fully pinned with `via` provenance comments.

`requirements.txt` (runtime, 45 packages) highlights: `alpaca-py==0.43.2`, `fastapi==0.136.1`, `sqlalchemy==2.0.49`, `pydantic==2.13.2`, `psycopg[binary]==3.3.3`, `duckdb==1.5.2`, `pyarrow==23.0.1`, `pandas==3.0.2`, full OpenTelemetry stack pinned at `1.41.0`/`0.62b0`.

`requirements-dev.txt` adds pytest/pytest-cov/mypy/ruff/pre-commit toolchain: `mypy==1.19.1`, `ruff==0.15.4`, `pytest==9.0.2`, `pytest-cov==7.0.0`, `pre-commit==4.5.1`, `types-pyyaml==6.0.12.20250915`, `types-requests==2.32.4.20260107`.

**Version drift between the two lockfiles for shared transitive deps** (both resolved independently by pip-compile, so minor skew is expected/benign but worth flagging as a smell): `pandas` 3.0.2 (runtime) vs 3.0.1 (dev); `pydantic` 2.13.2 vs 2.12.5; `numpy` 2.4.4 vs 2.4.2; `sqlalchemy` 2.0.49 vs 2.0.47; `greenlet` 3.4.0 vs 3.3.2; `requests` 2.33.1 vs 2.32.5; `tzdata` 2026.1 vs 2025.3. These two lockfiles are not cross-consistent — if both were ever installed into the same env (e.g. local dev via `requirements-dev.txt` per README step 4, following `requirements.txt` per step 3) pip would resolve to whichever installs last, silently diverging from either compiled lockfile.

## 15. `mkdocs.yml`

`site_name: Retail Autonomous Trading Platform`, `theme: material` with `navigation.instant/sections/expand/top`, `content.code.copy`, `search.suggest/highlight`; `plugins: [search]`; standard `markdown_extensions` (admonition, toc-with-permalink, tables, fenced_code, codehilite). No `docs_dir` override, so MkDocs uses the default `docs/` directory.

**Verified broken: the `nav:` block references files that do not exist.** All 9 nav entries (`index.md`, `contracts/index.md`, `ingestion/index.md`, `runtime/index.md`, `safety/index.md`, `storage/index.md`, `universe/index.md`, `paper-validation/index.md`, `vertical-slice/index.md`, `research/index.md`) resolve to paths under `docs/` that were confirmed absent via direct `ls` — the real `docs/` tree (confirmed via listing) contains `architecture/`, `archived-docs/`, `audits/`, `backend/`, `frontend/`, `implementation-summaries/`, `operations/`, `platform/`, `templates/`, plus loose files like `frontend_audit_and_roadmap.md` — a completely different structure than what `mkdocs.yml`'s nav expects. `mkdocs build` (strict or not) would fail to find every navigation target as written. This config is stale relative to the actual `docs/` layout — either an early-phase doc-site plan that was abandoned in favor of the current `docs/` structure, or a doc-site that was never actually built/run. Not referenced anywhere in `ci.yml` (no `mkdocs build`/`mkdocs gh-deploy` step in CI), so this breakage has no CI consequence today but the file is non-functional as committed.

## 16. `CHANGELOG.md` (590 lines)

Two distinct numbering sequences under one file:
- **Spec-lock series** (`v0.1.0` → `v1.0.0`, lines 3–305): Phase 0 through Phase 9, each entry documenting a *specification/contract* being locked (canonical contracts, SoR + versioning, universe governance, safety architecture, scheduler semantics, ingestion pipeline semantics, research engine spec, v1 vertical-slice strategy spec, paper-trading readiness gates) — these read as design/spec sign-offs, not code landing.
- **Implementation series** (`0.1.0` "Phase 0 Baseline" then `v1.1.0` → `v1.5.0`, lines 306–590): actual code implementation phases, matching README's "v1 Project Implementation" section 1:1 — Phase 0 (baseline), Phase 1 (data model/contracts, v1.1.0 — matches the Alembic genesis migration `e62e75b1c021` found in section 14), Phase 2 (storage/versioning), Phase 3 (ingestion pipeline), Phase 4 (universe governance), Phase 5 (safety system: environment isolation, layered enablement gates, exposure caps, idempotency/dedup, shadow mode, kill switch — **explicitly logged as "Internal Stub"** with "External out-of-band kill switch storage (S3/Redis) deferred to future phase").

**Material staleness finding**: `CHANGELOG.md`'s last entry is `v1.5.0` (Phase 5 — Safety System, kill switch still an "internal stub" per its own text). But the Alembic migration timeline (section 14 above) shows the actual codebase has since built and shipped: a durable **DB-backed kill-switch table** (`uu54vv66ww07`, contradicting the changelog's "deferred to future phase" note), full universe-rotation/rebalance governance, shadow-runtime validation (Phase 5.5 per migration docstring — a phase number that doesn't even exist in the changelog), factor exposure/Black-Litterman/portfolio-construction/risk-budget/optimizer subsystems, a 5-rung drawdown governance ladder, and live-promotion CHECK constraints (Phase 9 per one migration's docstring reference) — all dated through June 2026, months of shipped work with zero corresponding `CHANGELOG.md` entries past Phase 5. The changelog is not being kept current with implementation.

## 17. `README.md` (906 lines)

- **Setup section** (lines 1–78): Python 3.11 venv, `pip install -r requirements.txt` → `pip install -e .` → `pip install -r requirements-dev.txt` → `pre-commit install` → `python -m pytest` — matches CLAUDE.md's documented commands and the lockfile pinning strategy from section 5/6 above exactly. Step 6 env setup instructs `cp .env.example .env.dev` from a **root-level** `.env.example` (confirmed present at repo root, distinct from `infra/.env.example` covered in section 7 — two separate example-env files for two separate concerns, app config vs. Docker Compose infra).
- **Canonical Docs** (lines 70–78): links `docs/README.md`, `docs/architecture/system-overview.md`, `docs/architecture/layering.md`, `docs/architecture/data-flow.md`, `docs/backend/`, `docs/operations/`, `docs/audits/` — the three named architecture files were spot-checked and **do exist** (`docs/architecture/{system-overview,layering,data-flow}.md`), so unlike `mkdocs.yml`'s nav, README's own doc links are valid against the real `docs/` tree.
- **Status section** (lines 79–260): mirrors the changelog's spec-lock series, "Phase 9 Complete" (paper-trading validation gates locked/versioned) is the last entry — this is a *spec* completeness claim, separate from code.
- **"v1 Project Implementation" section** (lines 262–895): mirrors `CHANGELOG.md`'s implementation series exactly, phase-for-phase, and likewise **stops at Phase 5 — Safety System & Risk Controls Implementation**, ending with the same "Kill Switch (Internal Stub)" language and the same "deferred to future phase" note for external kill-switch storage. Same staleness finding as section 16 applies here: README's implementation narrative is at least 4+ migration-clusters (universe rotation, shadow validation, factor/portfolio-construction/risk-budget stack, drawdown governance ladder, live-promotion gates) behind the actual shipped schema.

## Standout candidates

1. **Kill-switch durability contradiction**: `CHANGELOG.md`/`README.md` both describe the kill switch as an "Internal Stub" with external durable storage "deferred to future phase," but migration `uu54vv66ww07_add_kill_switch_state_table.py` already shipped a DB-backed singleton (`id='current'`) specifically so kill-switch state survives restarts/deploys/crashes — the docs are simply wrong about current capability, not just behind on wording.
2. **`mkdocs.yml` is fully non-functional**: every one of its 9 nav entries points at `docs/` paths that don't exist in the real doc tree; `mkdocs build` would fail outright. No CI step invokes it, so it's dead config, not a live break.
3. **`Dockerfile` would fail to build as committed**: `COPY src/requirements.txt` when the real file is at repo-root `requirements.txt`; no `src/requirements.txt` exists anywhere in the tree.
4. **Order-state hardening (`PENDING_NEW`/`PENDING_CANCEL`/`EXPIRED`) implemented twice** on two separately-named enum types (`order_status_enum` vs `tracked_order_status_enum`), six days apart — same drift class caught twice on parallel order-tracking tables.
5. **Drawdown governance ladder** (`gg77hh88ii99`) — 5-rung progressive capital-protection mechanism (NORMAL→WARNING→PROBATION→SUSPENDED→BREACHED, allocation_scalar 1.00→0.00) with anti-flapping cooldowns and mandatory operator acknowledgement — the most sophisticated single safety artifact found in the migration set.
6. **Alpaca-paper smoke job correctly triple-guarded**: `NO_LIVE_TRADING=true` + `ENABLE_LIVE_TRADING=false` + `workflow_dispatch`-only trigger — verified, no path to accidental live execution via CI.

## Gaps/smells

1. **CHANGELOG.md and README.md are both stuck at Phase 5** (v1.5.0, "Safety System... Implementation") while the Alembic schema evidences Phases well beyond that (universe rotation, shadow-runtime validation "Phase 5.5", factor/Black-Litterman/portfolio-construction/risk-budget/optimizer stack, drawdown ladder, live-promotion CHECK constraints referencing "Phase 9") — months of shipped, safety-relevant work undocumented in both canonical narrative docs.
2. **No lint/type-check CI gate** — ruff+mypy enforcement is pre-commit-only; a contributor who skips `pre-commit install` can push failing code with nothing in CI to catch it.
3. **Lockfile cross-drift**: `requirements.txt` and `requirements-dev.txt` resolve shared transitives independently (pandas, pydantic, numpy, sqlalchemy, greenlet, requests, tzdata all skewed between the two), and CI doesn't use either lockfile (`pip install -e .[dev]` resolves fresh from `pyproject.toml` floors every run) — the compiled lockfiles exist but aren't what CI actually tests against.
4. **`orchestration/dags/ratp_v1_skeleton.py`** is an all-`EmptyOperator` stub, superseded by real DAGs living under `src/.../scheduler/airflow/dags/` (out of scope) — vestigial file left in tree, plus a stray UTF-16 `_mount_test.txt` leftover.
5. **Two ad hoc debug scripts** (`check_sim_outputs.py`, `debug_read_parquet.py`) with hardcoded run IDs/machine-specific paths, and one destructive DB-reset script (`reset_backtest_state.py`) with no `--confirm`/dry-run gate, unlike the properly-guarded `inspect_cache.py clear --confirm`.
6. **Compose images `lgtm` and `otel-collector` are unpinned (`:latest`)** against otherwise-pinned Postgres 16 / Airflow 2.9.3 — reproducibility gap.
7. **Minor**: 2 of 89 migrations lack docstring/Create-Date headers; at least 3 tables (`audit_logs`, `ingestion_checkpoints`, `strategy_governance`) were drop-and-recreated mid-series rather than altered, each self-documented as a drift fix.

## Coverage: opened N of N

- Migrations: **89 of 89** individually opened and parsed (revision/down_revision/docstring/body for safety-relevant tables; header metadata for all).
- `infra/` non-migration files: **23 of 23** (6 db/sql/env config + 17 observability, confirmed via `git ls-files infra/` minus `alembic/versions/` = 112 − 89 = 23, cross-checked against the itemized dashboard/datasource/alert listings above).
- `scripts/`: **10 of 10**. `experiments/`: **3 of 3**. `orchestration/`: **2 of 2** tracked files (logs/`__pycache__` correctly excluded as untracked).
- Root/CI configs: **9 of 9** (`ci.yml`, `.pre-commit-config.yaml`, `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `mkdocs.yml`, `CHANGELOG.md`, `README.md` — 10 listed, all opened; `CHANGELOG.md`/`README.md` read via targeted section reads given length, headers/structure fully enumerated).
- Total scope: **112 + 10 + 3 + 2 + 10 = 137 tracked files**, all opened or enumerated with content verified (not filename-only) for every file flagged as notable above.

---
