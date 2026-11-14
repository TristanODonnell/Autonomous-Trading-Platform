# Interview Study Revisit Backlog

**Date:** 2026-11-12
**Source:** the "🔁 Revisit: issues found" subsections of the Interview Study Notes (Parts 1 and 2, in `Claude outputs/`). Each bullet carries its study tag (`[domain.section #item]`) so it can be traced back to the note that explains it.
**Status:** backlog. Nothing here is fixed yet unless its checkbox is ticked.

While studying the codebase for interviews I read every layer closely enough to find real defects, stale docs, and gaps. This file collects them in one place so they survive the study notes, which are not tracked in git. Paths are relative to `src/autonomous_trading_platform/` unless noted.

**How to use**

- Tick the box when the fix has merged and been verified (test, dashboard, or Box A check named in the bullet).
- Items marked **(confirmed)** were reproduced during the study (snippet, sandbox, or saved data). The rest were found by reading and should be reproduced before fixing.
- Follow the same rehearsal rule as `pre-launch-deployment-plan.md`: anything touching `safety/`, `execution/`, or the rebalance path gets a test first and is run on paper before it lands on Box A.
- **Scheduled elsewhere (2026-11-12):** the research-side items are folded into `research-generation-plan.md` Step 0 with their tags: all of section 7 (R1, R3, R7, R8, R9, the cache-hygiene follow-up), [6.5 #1, #2, #5, #8] as D2, [9.8 #5] under R4, and [1.8 #1, #4, #7] under the research-observability follow-up. Tick those in both files. Everything else here (observability buckets and Loki filters, CI, reliability, data layer, safety) stays on this list and follows the pre-launch plan's bug classes: idempotency and safety gaps are P1 for the deployment track.

**Domains:** 1 Observability · 2 Testing and CI/CD · 3 Reliability · 5 Architecture and data layer · 6 Parquet layer · 7 Determinism and research funnel · 8/9 Safety and governance

---

## Suggested order

Quick, low-risk, high-value first. Each line points at the tagged bullets below.

1. **Observability correctness** — histogram buckets [1.8 #7], Loki filters [1.8 #8, #9], the dashboard that never loads [1.8 #1]. Half a day; every latency panel is wrong until this lands.
2. **Data-layer footguns** — `upsert()` skipping JSONB [5.8 #1], missing model imports [5.8 #2], `get_session()` leak [5.8 #3]. Each one can silently corrupt a migration or a row.
3. **Idempotency and the rebalance lock** — [3.8 #1, #4, #5, #7]. These are the duplicate-order risks on the live path.
4. **Safety gaps** — empty allowlist [9.8 #1], kill switch skipped without account ID [9.8 #4], drawdown limit unwired on backtest [9.8 #5].
5. **CI hardening** — [2.4] items. Mechanical, no product risk.
6. **Parquet integrity** — [6.5] items, compaction data loss first.
7. **Research determinism** — [7.10] and [7.5] items. Biggest design work; MC and stage resume need tests before changes.

---

## 1. Observability

- [ ] **[1.8 #7]** Every histogram uses the OTel default buckets, which are sized in ms while durations are recorded in seconds, so p95 reads about 4.75s regardless of real latency. Add a View in `observability/telemetry.py` with second-scale boundaries (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10).
- [ ] **[1.8 #8]** The Loki correlation filter on 6 dashboards matches nothing. Change to `{service_name=~".+"} | correlation_id="$correlation_id"` and drop `| json`.
- [ ] **[1.8 #9]** `loki_link()` in `observability/correlation_links.py` uses `correlation_id` as a stream selector. Same fix as above.
- [ ] **[1.8 #1]** `research_pipeline.json` sits in `grafana/provisioning/dashboards/` and uses `${DS_PROMETHEUS}`, so it never loads. Move it to `grafana/dashboards/` and replace the import variable.
- [ ] **[1.8 #2]** `make_dashboards.py` hardcodes `C:\Users\Tristan\...`. Use a path relative to the repo.
- [ ] **[1.8 #3]** Fix copy-paste metric descriptions (`ratp_position_scaling_absent_total`, `ratp_average_portfolio_correlation`, `ratp_diversification_ratio`, `ratp_mvo_turnover`) and fill in the empty ones (`correlation_cluster_count`, `portfolio_volatility_estimate`, `strategy_risk_contribution`, `risk_budget_utilization`).
- [ ] **[1.8 #4, 7.10 #9]** State values are recorded as histograms (`ratp_portfolio_governance_paused`, `ratp_drawdown_governance_state`, `ratp_portfolio_peak_equity`, research stage survivor counts). Change them to observable gauges or counters.
- [ ] **[8.8 #3]** Check whether the 18 alerts route anywhere (Alertmanager or a Grafana contact point). If not, add routing.

## 2. Testing and CI/CD

- [ ] **[2.1]** Replace the 6 runtime "scenario didn't happen" skips (settlement ×4, dividend, rebalance no-op) with fixtures that guarantee the scenario, and assert instead of skipping.
- [ ] **[2.2]** Add a real Postgres test for `pg_try_advisory_xact_lock` with two connections, where the second must skip.
- [ ] **[2.3]** `test_unmapped_contracts_or_models_are_documented` only checks the list is non-empty. Loop over `Base.metadata.tables` and assert each table is either paired or listed.
- [ ] **[2.3]** The schema-drift fixture sets `os.environ["DATABASE_URL"]` directly. Use `monkeypatch`.
- [ ] **[2.3]** Add `alembic check` to CI, plus a downgrade then upgrade round-trip test.
- [ ] **[2.4]** Add a CI job running `pre-commit run --all-files` (ruff, mypy).
- [ ] **[2.4]** Align versions: CI tests on Python 3.12 but prod runs 3.11, and CI installs from pyproject ranges while prod uses the pinned `requirements.txt`.
- [ ] **[2.4]** Add a Docker build step to CI.
- [ ] **[2.4]** Add a pip cache, a coverage report (`pytest-cov` is already installed), and `pytest-xdist`.
- [ ] **[2.4]** Pin GitHub Actions to commit SHAs, especially `appleboy/ssh-action`.

## 3. Reliability

- [ ] **[3.8 #1]** The 15-minute idempotency window uses `StubOrderActivityReader`, which always returns `False`. Write a real reader that queries `order_intents` by key and time window, and wire it in at `trading_cycle_common.py:502`. The same stub feeds the throttle.
- [ ] **[3.8 #2]** Check the Alpaca client-order-ID lookup in `execution/clients/alpaca_broker_client.py` against the docs (`GET /v2/orders:by_client_order_id?client_order_id=...`), then test it on a paper account.
- [ ] **[3.8 #3]** Order IDs include `run_id`, so they only dedupe within one run. Check when the bar checkpoint commits relative to submission. Consider seeding IDs with strategy, bar, symbol and side instead.
- [ ] **[3.8 #5]** The rebalance checks the retry and interval guards before taking the advisory lock. Take the lock first, then run every check inside it.
- [ ] **[3.8 #4]** The rebalance row guard is invisible to other sessions under READ COMMITTED. Either commit a lease row in its own transaction or remove the guard and update the comment.
- [ ] **[3.8 #6]** Add `skipped_kill_switch` to `_TERMINAL_STATUSES`, and have production callers pass `rebalance_run_id`.
- [ ] **[3.8 #7]** `SorUnitOfWork.__exit__` commits or rolls back even when it didn't open the transaction. Only do so when `_started_transaction` is true, or use `begin_nested()` for inner scopes.
- [ ] **[3.8 #8]** Treat 5xx responses like transport errors: look the order up before retrying.
- [ ] **[3.8 #9]** Add a test or comment ensuring nothing commits between taking the rebalance lock and the final commit.
- [ ] **[3.8 #10]** Add jitter to the order retry backoff.

## 5. Architecture and data layer

- [ ] **[5.8 #1] (confirmed)** `upsert()` silently skips the `metadata` JSONB column. Iterate `sa.inspect(Model).column_attrs` and use `attr.key`. The same loop is in 23 repository files. Only `fills` and `order_intents` are affected, but fix the pattern everywhere.
- [ ] **[5.8 #2]** `storage/sor/models/__init__.py` is missing 7 model imports (`drawdown_governance_ladder_state`, `drawdown_governance_ladder_transition`, `feature_dataset_versions`, `fill_quality_metrics`, `shadow_comparison_snapshots`, `shadow_divergences`, `shadow_runs`), so autogenerate would try to drop those tables. Add the imports plus a test that every model file is imported.
- [ ] **[5.8 #3]** `get_session()` returns instead of yielding, so the session never closes. Change to `s = SessionLocal(); try: yield s; finally: s.close()`, and stop rebuilding the sessionmaker on every call.
- [ ] **[5.8 #4]** 13 write endpoints have no role check (the metadata writes and the operations/alerts actions). Add `require_*` dependencies plus a test that every non-GET route declares one.
- [ ] **[5.8 #5]** Add import-linter contracts to CI for the layer rule. 51 services import ORM models directly, and `contracts/validators/dataset_version.py` imports `storage.parquet`.
- [ ] **[5.6, 5.8 #7]** Generate frontend TS types from `/openapi.json` with `openapi-typescript` instead of hand-copying them.
- [ ] **[5.8 #8]** Change `market_bars.volume` to `BigInteger`. Consider `Numeric` for `adjustment_factor`.

## 6. Parquet layer

- [ ] **[6.5 #2]** Compaction in `platform_replay/ingestion_hooks.py` deletes fragments it failed to read. Delete only the fragments that were read successfully, and log or raise on read errors.
- [ ] **[6.5 #1]** The reader ignores fragments once `data.parquet` exists. Read each file separately, normalize with `_with_partition_columns`, then concat.
- [ ] **[6.5 #4]** `bar_chunk_writer_service.py` rewrites `data.parquet` in place. Write to a temp file, then `os.replace`.
- [ ] **[6.5 #5] (confirmed)** The table checksum depends on chunk layout. In `compute_checksum.py`, `sort_by` the key columns and `combine_chunks()` before hashing.
- [ ] **[6.5 #3]** Live appends overwrite `_metadata.json` with only the last cycle's checksum and row count. Accumulate them, or write them at end of day.
- [ ] **[6.5 #6]** Call `_verify_file_checksums` somewhere on Box A, and clean up `checksums` rows that point at deleted fragments after compaction.
- [ ] **[6.5 #8]** Partition dates use UTC dates. Use the exchange date (ET).
- [ ] **[6.5 #9]** Update `docs/backend/storage-lineage/storage.md`, which says the dataset version model, checksums and lineage don't exist.
- [ ] **[6.4]** Small files: buffer the day's bars and write once at end of day, or compact the daily version as an EOD step.

## 7. Determinism and research funnel

- [ ] **[7.10 #1] (confirmed)** Monte Carlo runs are identical copies (seen in saved Parquet rows). Give MC something to vary: seeded partials, rejections and touch probability for MC only, or `shuffle_timestamp` or a block bootstrap, or per-run cost perturbation. Add a real-engine MC smoke test asserting `sharpe_dist.std > 0`.
- [ ] **[7.10 #3]** Stage resume is wrong in both directions. Store the pass/fail verdict or the `cached_run_id` with the checkpoint, rehydrate and re-filter on resume, and add tests where a strategy that failed before the crash still fails and a partial resume keeps earlier passes.
- [ ] **[7.10 #2]** Wire `ResearchCheckpointService` and `SimulationResultCache` into `from_dict()` and `_build_replay_experiment_definition`.
- [ ] **[7.10 #4]** Add the cache key's `key_id` to the checkpoint identity (it's missing seed, symbols, date range and cost model).
- [ ] **[7.10 #5]** Add the git SHA or an engine version string to the cache key.
- [ ] **[7.10 #6]** Use `derive_seed(... trial_id=run_index)` in the MC stage instead of `base_seed + run_index`.
- [ ] **[7.10 #8]** Rename `settlement_days` to `settlement_bars`, or convert days to bars with `BARS_PER_DAY`.
- [ ] **[7.5 #1]** Fills draw from one RNG stream in intent order, so adding a symbol changes other symbols' fills. Derive a keyed RNG per decision from (run seed, `intent_id`, bar timestamp, decision kind).
- [ ] **[7.5 #2]** Raise if any stochastic probability is set and no seeded RNG was passed. Also fix `reset_for_run(rng=None)`, which keeps the old RNG.
- [ ] **[7.10 #7]** Add fitness selection to `evolutionary_generator.py` (keep the top k by Sharpe and breed from them), or rename it to mutation search.
- [ ] **[7.10 #10]** Fix the stale "42-day lookback" comment in the replay hook. The lookback is 90 days.
- [ ] **[7.4]** Add a cross-process replay test that compares artifact checksums.

## 8/9. Safety and governance

Read `docs/architecture/safety-doctrine.md` before touching any of these.

- [ ] **[9.8 #1]** An empty account allowlist allows every account. Require a non-empty list when the environment is LIVE.
- [ ] **[9.8 #2]** Add `drawdown_ladder_mode` to Settings so the mode is explicit (it currently falls back to observe).
- [ ] **[9.8 #5]** Confirm nothing reads the 12% `max_drawdown_limit` on the backtest trading path. Wire it in, then rerun the two-year backtest with `ACTIVATE_KILL_SWITCH` or enforce.
- [ ] **[9.8 #3]** The runtime arm is in memory per process. Check how arming reaches the scheduler, and persist it if it doesn't.
- [ ] **[9.8 #4]** On the live path with no account ID, `RuntimeTradingGuardService` skips the kill switch check. Add the check.
- [ ] **[8.8 #1]** Add a test for the kill switch auto-trip on a portfolio drawdown breach.

---

## Fix log

Record fixes here as they land: date, tag, commit, how it was verified.

| Date | Tag | Commit | Verified by |
|------|-----|--------|-------------|
| | | | |
