# Pre-launch plan: paper/live loop + worker box

_Decided 2026-10-01. Planning only, nothing implemented. Assumes 5c (research↔trading parity) is done first._

Related: `docs/audits/execution_topology_deployment_audit.md` (component inventory, P0 gaps),
`docs/roadmaps/portfolio-rotation-plan.md` (Step 6 — Airflow schedules, deferred; see §3.1).

## 1. Decisions

| Topic | Decision |
|---|---|
| Topology | 2 EC2 boxes: **A = paper/live**, **B = worker** |
| Paper vs live | One box (A). "Live" means switching on real-money trading, not a separate deployment |
| Worker role | The platform's **research engine**: strategy generation, bench re-sims, inputs to allocation and governance. Not a regression/ops test box |
| Worker DB | **Its own Postgres.** Never pointed at A's DB (`scripts/reset_backtest_state.py` wipes shared SOR tables such as `audit_logs`, `governance_audit_events`, `strategy_sleeve_ledger`) |
| Worker → A hand-off | **Through A's REST API** |
| Automation | **Fully automated**: results auto-apply, no approval step. Find problems while deployed, so guardrails and audit matter |
| Market data | **One source of truth**: A ingests and publishes versioned Parquet to S3; B pulls |
| Data feed | Free Alpaca (IEX). Paying for nothing |
| Corp actions | **Post-close, about 6pm ET**, first step of the end-of-day chain |
| Scheduler on A | **No Airflow at launch.** One long-running scheduler process, extended from `cli/commands/runtime_soak_loop.py`. Airflow is deferred (§3.1) |
| Worker uptime | Always on (idle between jobs); look at cost later |
| Observability | OTel collector on each box → central LGTM **stays on A**; upsize A to t4g.medium if RAM is tight |
| Alerts | Dashboard only at launch; push channel later |
| Deploys | **Frozen during market hours** (~9:00–16:30 ET weekdays), with an emergency override |

## 2. Topology

**Box A: paper/live (t4g.small → medium if needed)**
- FastAPI app service (new in `docker-compose.yml`)
- Scheduler service (same app image, runs the soak-loop runner, `restart: unless-stopped`)
- Platform Postgres. **No Airflow** (no webserver, scheduler or Airflow Postgres)
- LGTM (Grafana) + OTel collector
- Host timers (cron/systemd) for nightly backup and weekly retention
- `.env`: `APP_ENV=paper` (later `live`), broker keys, `JWT_SECRET`
- Publishes Parquet datasets to S3

**Box B: worker**
- Worker container (same app image), **own Postgres**, OTel collector → A
- `.env`: `APP_ENV=backtest`, `NO_LIVE_TRADING=true`, **market-data keys only, no broker trading keys**
- Pulls Parquet from S3; posts results to A's API
- Job timer: cron/systemd, with one job at a time per DB

**Config model:** `Settings` (`config/settings.py`) reads environment variables, not YAML. Each box
gets a compose file plus an env file, with secrets kept in SSM/Secrets Manager. Fixture YAMLs stay as
scenario specs. Add a "campaign" YAML on B listing which fixtures and research jobs run when.
Operator settings live in A's DB (`operator_settings`), not in files.

### Backtest cadence knobs (clarification)
- `platform_replay.cadence_minutes` sets how often the trading cycle ticks within a simulated day:
  `5` = 78 ticks/day (matches production `*/5`), `390` = one tick/day (cheap mode).
- `scheduled_jobs.<job>.cadence: daily` = batch jobs run once per simulated day (ingestion pulls the
  whole day of 5-min bars at once). `trading_cycle: daily` in that block is misleading: the cycle
  still ticks at `cadence_minutes` inside the day.
- Cost: ~5 min of compute per simulated day at 5-min cadence; ~30 s at daily cadence.

## 3. Schedules

All schedules are in `America/New_York` and gated by the market calendar (`RealMarketCalendar`).
Order the end-of-day work as a dependency chain, not as separate clock times. (Today's
`scheduler_registry.py` crons are UTC, so rebalance at `0 21 * * 1-5` lands exactly on the 4pm ET
close in winter, and rebalance/promotion run *before* the ladder, health lifecycle and corp actions.)

**Box A: scheduler process (soak-loop runner) + host timers**

| Cadence | Job(s) |
|---|---|
| Every 5 min, market hours, weekdays | Ingestion → trading cycle (evaluation, netting, pre-trade risk, submission, intraday reconciliation) |
| End of day, ~16:15 → ~18:30 ET | Final ingestion → broker reconciliation → **corp actions** → adjusted bars → **features** → risk/sleeve snapshots → on-deck shadow marking → live metrics → health monitor/lifecycle → drawdown ladder + portfolio drawdown → correlation/risk budgeting → promotion/demotion → rebalance (for tomorrow) → **publish Parquet to S3** |
| Weekly (after B's re-sims land) | Bench review → portfolio review (auto) → re-weight |
| Monthly | Universe: raw pool refresh → selection/rotation |
| Nightly (host timer) | Postgres backup → S3 |
| Weekly (host timer) | Retention: old Parquet versions, artifacts, logs |

The intraday, end-of-day, weekly and monthly rows run in the scheduler process. Backup and retention
are host timers so they don't depend on the app process being healthy.

### 3.1 Why Airflow is deferred

**What exists today.** Airflow has 4 thin DAGs (`scheduler/airflow/dags/`: ingestion, trading, corp
actions, backfill), each calling a `run_*_cycle` function. The end-of-day chain, the S3 publish and the
weekly/monthly jobs don't exist as DAGs, so they are new work either way. Meanwhile
`runtime_soak_loop.py` + `PaperTradingGoldenPathOrchestrator` already have the real clock and market
calendar, the 5-minute intraday tick (ingestion → features → trading), end-of-day detection with
corp actions → adjusted bars → features, a no-overlap lock and orphan-job rescue at startup.

**Reasons to defer:**
- The missing end-of-day steps are plain Python calls in dependency order either way; wrapping them in a
  DAG later is a thin layer.
- Closer to the backtester: platform replay calls the cycles in-process in hook order. One runner
  calling the same sequence keeps production ordering aligned with replay (5c parity), instead of
  independent per-job schedules.
- Memory: Airflow webserver + scheduler + its own Postgres, next to the platform Postgres, API and
  LGTM, would likely force A off t4g.small (estimate, not measured).
- Attack surface: the dev compose runs Airflow on `admin/admin` with `EXPOSE_CONFIG=true`; deploying it
  means hardening it first.

**What replaces Airflow's features:**

| Airflow feature | Replacement |
|---|---|
| Run history UI | `job_runs` table + `runtime list-job-runs` / `inspect-job-run`; Grafana end-of-day pipeline panel |
| Retries | Small per-step retry wrapper (the trading cycle already classifies retryable vs not) |
| Manual trigger | `ManualTriggerService` / `runtime trigger-job` |
| Crash recovery | `restart: unless-stopped` + orphan-job rescue at startup |
| Missed-run detection | Heartbeat panel: "no trading cycle in 15 min during market hours" (1.4) |
| Backup / retention | Host cron/systemd timers |

**Main risk:** the scheduler process is a single point of failure, so the restart policy and the
heartbeat panel are launch requirements, not nice-to-haves.

**Revisit Airflow when any of these holds:**
- A's weekly review needs to wait on B's results arriving (though triggering it from B's API hand-off
  may fit better than a schedule)
- Re-running past end-of-day dates becomes routine (backfill)
- The number of schedules makes the runner hard to follow

**Box B: worker timer**

| Cadence | Job(s) | Output → A |
|---|---|---|
| Nightly (after A's S3 publish) | Pull datasets | none |
| Weekly (before A's weekly review) | Bench re-sims + scorecards | Scorecards/re-sim results |
| Monthly | Research pipeline / strategy generation (full profile) | New candidates → bench |
| Monthly | Regime-suite portfolio/governance sims | Allocation/governance parameter recommendations, auto-applied |
| On demand | Any fixture | Artifact to S3 only |

## 4. Launch checklist (in order)

**Box A comes first.** The worker depends on A for its data (A's S3 Parquet publish), its result
hand-off (A's API) and its observability (A's Grafana). Phases 1 and 2 can overlap once A's S3
publish works.

**Three ordering rules matter most:**
1. Backups and the deploy freeze go in **before A runs unattended**.
2. Alert filtering (`!= backtest`) goes in **before the worker sends any telemetry**.
3. Guardrails go in **before anything auto-applies**.

### Phase 1: Box A runs unattended on paper

**1.1 P0: blocks deploy**
- [x] `JWT_SECRET` in env (API crashes without it); add to `.env.example`
- [x] `DATABASE_URL` uses `postgres:5432` inside Compose, not `localhost:5433` (set in `docker-compose.yml`, so `.env` keeps the host-side URL; same for the OTel endpoints)
- [x] `alembic upgrade head` runs on deploy, before the app starts (one-shot `migrate` service; `app` and `scheduler` wait for it to succeed)
- [x] API `app` service added to `docker-compose.yml`
- [x] `scheduler` service (soak-loop runner, `restart: unless-stopped`) added; Box A's compose has no Airflow services (moved to the dev-only overlay `docker-compose.airflow.yml`; the deploy uses `--remove-orphans` to stop the old Airflow containers)
- [x] Found while building the image: `alembic`, `pyjwt` and `pytz` were imported but never declared, so the image could not migrate, serve the API or run `atp`. Added to `pyproject.toml` / `requirements.txt`

_Done 2026-10-04. Verified locally against a scratch database: migrations from empty to head, API
healthy and JWT-gated, scheduler starts, sleeps until the next open and exits cleanly on SIGTERM.
Not yet run on the EC2 box. Before the first deploy the box's `.env` needs `JWT_SECRET`, `APP_ENV=paper`
and the broker keys, and Docker Compose must be 2.24 or newer (`env_file` uses `required: false`)._

**1.2 Before running unattended: backups + deploy freeze**
- [x] Nightly Postgres backup → S3 (`infra/ops/backup_postgres.sh`, systemd timer at 23:30 ET via `infra/ops/install_timers.sh`; runbook `docs/operations/runbooks/postgres-backup-restore.md`). Dump and restore tested locally (row counts match on all 90 tables); Box setup done 2026-10-11: bucket `ratp-tristan-prod` (us-east-2, 30-day expiry on `postgres/` only), policy `ratp-box-a-s3` + instance role `ratp-box-a`, IMDS hop limit 2, AWS CLI, `BACKUP_S3_BUCKET` in `infra/.env`, timers installed; first real upload verified (234 MB, 90 tables)
- [x] Auto-deploy (`.github/workflows/deploy.yml`) skips 9:00–16:30 ET on weekdays (and checks the market calendar): `scripts/deploy_window.py`, frozen from 30 min before the open to 30 min after the close, so half days and holidays follow the exchange calendar. A skipped deploy is **not** retried automatically: re-run the workflow after the close
- [x] Emergency override: manual `workflow_dispatch` with a required `reason`, logged (job summary + `~/ratp-deploy.log` on the box, which records every deploy)
- [x] Scheduler stops gracefully on deploy: finishes the in-flight step and doesn't start a new one (matters for the post-close end-of-day chain, which falls outside the freeze). An in-flight intraday tick or end-of-day step finishes on SIGTERM (6 min grace, tested) and no new one starts; `EodChainRunner` (1.3) stops *between* end-of-day steps and resumes the chain after the last completed step on restart, with completion held in `job_runs` rather than in memory (2026-10-05)

**1.3 Scheduling (soak-loop runner, no Airflow; see §3.1)**
- [x] Market calendar refresh (holidays, half days): `exchange_calendars` already supplies them; a daily cross-check of the next two weeks against Alpaca's calendar is the chain's first step and reports any mismatch (2026-10-05)
- [x] Extend `PaperTradingGoldenPathOrchestrator.run_eod_maintenance` past features: final ingestion, broker reconciliation, then the governance stack and rebalance **in the backtester's hook order** (§3 end-of-day row), passing `dataset_version_id` along the chain. Done 2026-10-05; the backtester's actual order differs from the §3 row and was followed, see `pre-launch-1.3-scheduling.md` §1–2
- [x] Per-step retry wrapper; every step recorded in `job_runs` (`EodChainRunner`: per-step rows under one parent per trading date, retry cap, stop between steps, resume after restart)
- [x] Parquet publish to S3 at the end of the end-of-day chain (code done 2026-10-05; `DATASET_S3_BUCKET`/`DATASET_S3_REGION` set on the box 2026-10-11 and the role verified from inside the `app` container; **first real publish expected at the close of Mon 2026-10-12**)
- [x] Weekly review and monthly universe steps in the runner (roadmap Step 6, without Airflow). Done 2026-10-05: weekly review on the week's first session (a no-op until `portfolio_review_mode` is switched on in Phase 3), universe rotation at the close of the month's last session with the churn guard on
- [x] Host timers: nightly Postgres backup (1.2), weekly retention job (`infra/ops/retention.sh`, Sunday 03:00 ET: dumps > 14 d, `artifacts/` > 30 d, Docker prune; Parquet untouched; container logs capped in compose)

**1.4 Observability (before the worker exists)**
- [x] Resource tags: `deployment.environment` (paper/live/backtest, from `APP_ENV`), `service.name` per role (`OTEL_SERVICE_NAME` per compose service: ratp-api / ratp-scheduler / ratp-migrate; the API now starts through `create_production_app`, which sets telemetry up), `host.name` (`HOST_NAME` from the box's `$HOSTNAME`). The collector's Prometheus exporter converts them to labels (`resource_to_telemetry_conversion`), verified end to end on the dev stack 2026-10-05
- [x] All alert rules (`infra/observability/prometheus/alerts/ratp-alerts.yaml`) exclude `deployment_environment="backtest"` (promtool: 18 rules OK)
- [x] `hostmetrics` + container stats on A (collector receivers `hostmetrics` via `/hostfs` and `docker_stats` via the Docker socket; collector runs as root for the socket); **watch memory on the Infra dashboard and upsize to t4g.medium if needed** (still to observe on the box)
- [x] Dashboards: Infra (host dropdown) · Trading ops · End-of-day pipeline · Research pipeline (exists). The Grafana Postgres datasource uses the default `ratp_password`; if the box's `infra/.env` differs, update `infra/observability/grafana/provisioning/datasources/datasources.yaml` or the SQL panels stay empty
- [x] Heartbeat panel: "no trading cycle in 15 min during market hours" and "end-of-day chain not finished by ~19:00 ET" (stands in for Airflow's missed-run visibility). The scheduler exports `ratp_scheduler_heartbeat_timestamp_seconds`, `ratp_market_open`, `ratp_trading_cycle_last_success_timestamp_seconds`, `ratp_eod_chain_overdue`, `ratp_eod_chain_completed_timestamp_seconds`; alerts `RATPSchedulerDown`, `RATPTradingCycleStale`, `RATPEodChainOverdue` replace the two `absent_over_time` rules that fired every night and weekend; stat panels on Trading Ops

**1.5 Soak**
- [ ] 1–2 weeks of unattended paper running before relying on it

*Soak log.* First attempt Mon–Wed 2026-10-12..14 on t4g.medium: no OOM, every EOD chain
finished, Parquet published to S3 all three days and Box B pulled it nightly. Trading itself
only ran ~09:30–11:00 ET each day, so those days do not count. Causes and fixes (branch
`fix/soak-findings`):
- **Tick drift.** The runner slept a flat 300 s after each tick, ticks drifted ~3 s per
  cycle, and once more than 60 s past the 5-minute boundary the trading cycle's readiness
  check (`ingestion_deadline = boundary + 60 s`) put every remaining tick in safe mode
  (`trading_cycle_degraded: ingestion_not_ready: ingestion_deadline_missed`; 53/60/58 ticks
  per day). Ticks are now anchored at each boundary + 10 s (`_seconds_until_next_tick`).
- **Missing-bars hard stop.** On the free IEX feed thin names print no trade in some minutes;
  Monday 09:55 ET had 5/22 symbols missing (22.7%), over the 20% threshold, and the whole
  tick failed four times (no features, no trading). The 20% level is now only the SLA
  record; the tick fails above `INGESTION_MISSING_BAR_FAIL_RATIO` (default 0.5).
  Incidents per day were 43 / 9 / 28, so this is routine, not a feed outage.
- **Worker deploy never ran.** `Deploy worker (Box B)` reported success while skipping:
  the `WORKER_HOST` secret is not visible to the workflow (B had no runner logins and sat
  12 commits behind main). Fix the repository secret, then `workflow_dispatch`.
- **Feature versions not registered on B.** Publish manifests only carried the
  `dataset_versions` row; returns/regime versions live in `feature_dataset_versions`, so B
  logged `no_registry_row` for each. Manifests now also carry `feature_dataset_version`
  and the pull registers it.
- Still to do from the soak: container logs print only event names (the formatter drops
  `extra` fields; they are in Loki), and Box A's root volume (79% of 29 GB) needs the
  planned grow to 50 GB. **The soak clock restarts at the first full session after these
  fixes are deployed.**

### Phase 2: worker bootstrap (can overlap with A's soak)

**2.1 Box** (code done 2026-10-11 on `worker-bootstrap`; runbook `docs/operations/runbooks/worker-box.md`; box not provisioned yet)
- [x] `docker-compose.worker.yml`, own Postgres, migrations (standalone compose; jobs run as `docker compose run --rm worker atp ...` through `infra/ops/worker_job.sh`, which holds a one-job-at-a-time host lock)
- [x] `.env.worker`: `APP_ENV=backtest`, `NO_LIVE_TRADING=true`, market-data keys only (`.env.worker.example`; keys left empty, the worker reads S3 datasets)
- [x] Separate deploy target for B (`.github/workflows/deploy-worker.yml`: CI green on main, no market-hours freeze, takes the job lock around migrate, skips itself until the `WORKER_HOST` secret exists)
- [x] OTel collector on B → A, with `hostmetrics` + container stats (`infra/observability/otel-collector-worker.yaml`, forwards OTLP to A's collector over the VPC with a retry queue; traces sampled 10%; **A's security group needs 4317 from B**)
- [ ] Provision the instance, role `ratp-worker`, hop limit 2, secrets, first `up -d` (user; AWS steps in the runbook)

**2.2 Data**
- [x] S3 dataset pull, pinned to the dataset versions A published (`DatasetPullService` + `atp datasets pull [--as-of DATE | --version ID]`, nightly timer 20:30 ET via `infra/ops/install_worker_timers.sh`; verifies size + SHA-256, registers rows in the worker's own `dataset_versions`; manifests now carry `source_manifest`/`metadata_json` so the worker can select versions the way A does; `atp datasets publish` allows a one-time seed from the dev machine). **Not yet run against the real bucket**: first chance after A's first publish on Mon 2026-10-12

**2.3 On-demand runs only (nothing touches A yet)**
- [x] Runner wrapper: reset → delete stale checkpoint → run → artifact to S3 → run summary (`atp worker run`, `WorkerRunService` + `WorkerStateResetService`; summary.json per run locally and in S3 under `artifacts/worker/<name>/<stamp>/`; pulled dataset registrations survive the reset; refuses outside `APP_ENV=backtest|dev|test`; `scripts/reset_backtest_state.py` now calls the same service). Done 2026-10-12. **Note:** fixture replays fetch bars through the real Alpaca ingestion path by design (5c parity), so B needs a market-data key pair from a separate paper account for replays; the pulled Parquet is for the research jobs. Recorded-dataset replay is 3.4
- [ ] Span/log attributes: `run.mode`, `backtest.run_id`, `fixture`, `sim_timestamp`; keep `run_id` off metric labels
- [ ] Sample backtest traces heavily (or don't export them)
- [ ] Dashboard: Worker/research runs

### Phase 3: connect worker → A (guardrails before automation)

**3.1 Guardrails first** (code done 2026-10-12 on `guardrails`; migration `cc77dd88ee99`)
- [x] Every automated decision is audited (`governance_audit_events`, `portfolio_review_decisions`): what, why, which worker run (`portfolio_reviews.worker_run_id` + `PortfolioReviewResult.worker_run_id`; promotion/demotion audit rows already carry `source_run_id`; every worker hand-off lands in `worker_result_submissions` with its decision and reason)
- [x] A per-automation off switch (research publish, review swaps, rebalance, promotion/demotion) that works without a deploy (`worker_results_enabled` added; `portfolio_review_mode`, `auto_rebalance_enabled`, `auto_promote_enabled`, `auto_demote_on_breach` and `min_rebalance_interval_hours` are all in `OperatorSettingsUpdateRequest`, so `atp settings set` / the settings API flip them; `atp controls automation` shows the effective state)
- [x] Rate caps stay on (max swaps per review, tenure, swap interval, turnover cost): unchanged, still enforced by `PortfolioReviewService` whatever the switches say
- [x] A refuses worker results built on data older than N days (`WorkerResultGateService`: switch, kill switch, `dataset_as_of` age ≤ `worker_result_max_age_days`, git SHA match unless `worker_result_require_matching_git_sha=false`, empty payload, idempotent by run id)
- [x] Kill switch overrides all automation (`AutomationGateService`; auto-promotion, auto-demotion and rebalance record `kill_switch_enabled` skips, an `auto` review runs as advisory, worker results are rejected)
- [x] Dashboard panel: "automated decisions in the last 7 days" plus "worker results rejected" (governance-runtime dashboard: "Automated decisions (7d)", "Worker results (7d)", "Automation switches"; metrics `ratp_automation_blocked_total`, `ratp_worker_results_total`)

**3.2 Hand-off** (code done 2026-10-12 on `guardrails`)
- [x] **API hand-off contract**: results carry `dataset_version`, code version (git SHA), run ID and fixture; A rejects stale or mismatched results; calls are idempotent by run ID (`POST /api/v1/worker/results` + `GET` list/one, `worker` JWT role, `WorkerResultSubmission`/`WorkerResultDecision` contracts, typed `BenchResimsPayload`; worker side `WorkerResultClient` + `atp worker publish-result`; A's weekly review consumes the latest accepted still-fresh `bench_resims` and marks it `applied` with the review id). **Open for 3.3(a):** the worker job that produces bench re-sims needs A's bench membership + strategy configs as input

**3.3 Switch automation on in order of risk, one at a time with a few cycles between**
- [ ] (a) Weekly bench re-sims + scorecards from B, plus the weekly review DAG on A
- [ ] (b) Monthly research / strategy generation → bench
- [ ] (c) Allocation/governance parameter changes, last (riskiest)

**3.4 Data fetching restructure: recorded-dataset replay (before any scheduled replay)**

Today a fixture replay fetches its bars from Alpaca through the real ingestion path. That is
the right regression and parity tool (5c) and stays. It is the wrong input for *scheduled*
replays: a fresh fetch months later can differ (corrections, backfills, IEX thinness), so
two runs of the same fixture are not guaranteed to match; every run re-downloads months of
5-minute bars for the whole universe; and it needs a trading-capable key pair on B.

- [ ] A recorded-bars source for the ingestion step: the replay's ingestion job reads the
      Parquet dataset versions B pulled from A (the exact bars A traded on) instead of calling
      Alpaca, and everything downstream (corp actions, features, trading cycle) runs unchanged
- [ ] Fixture flag to choose the source (`alpaca` = today's behaviour, `recorded` = pulled
      datasets pinned with `--as-of`), recorded in the bundle and the run summary
- [ ] Parity check: the same fixture run both ways over a recent window agrees within the 5c
      tolerance (re-sim vs platform corr ≥ 0.99); keep one `alpaca`-sourced regression in CI
- [ ] Only then: scheduled fixture runs on B use `recorded`; `.env.worker` can drop the Alpaca keys

### Phase 4: scheduled campaigns
- [ ] Freeze `suite_v1` windows (section 5)
- [ ] Turn on the worker's campaign timer (section 3, Box B table)

### Phase 5: real money
- [ ] Only after A has a clean paper record with all Phase 3 automation on
- [ ] Add a push channel for kill switch + the heartbeat conditions from 1.4

## 5. Historical sim windows (suite_v1)
- Pick windows by **regime classifier coverage** (TASK-2.2) so every trend/volatility bucket is covered
- Candidates:
  - Q4 2018 selloff
  - Feb–Jun 2020 COVID
  - 2021 low-volatility bull
  - 2022 bear market
  - 2023 narrow AI rally
  - Jul–Sep 2024 volatility spike
  - Mar–May 2025 tariff shock
- **Holdout:** the most recent 6–12 months, never tuned on
- Tiers:
  - Regime windows: 2–6 months at 5-min cadence (~5 min of compute per simulated day)
  - Long walk-forward: 5+ years at **daily** cadence (at 5-min cadence it's ~105 h)
- 2–3 random seeds per window; freeze as `suite_v1`; changes become `v2`
- Data: check how far back free Alpaca history goes. IEX 5-min volume is thin and distorts slippage.
  Verify whether the free plan allows **historical SIP bars older than 15 min**; if so, use SIP for
  backtests at no cost

## 6. Still open
- [ ] Verify free-plan SIP historical access (affects backtest realism)
- [ ] Exact API contract for worker → A publishing (design doc once 5c lands)
- [ ] Settlement/T+1: does the live ledger need a daily pass, or is it simulation only?
- [x] Confirm risk/sleeve snapshots run inside the trading cycle or need their own schedule: they run inside every trading cycle, with order reconciliation and portfolio drawdown governance; no separate schedule (see `pre-launch-1.3-scheduling.md` §1)
