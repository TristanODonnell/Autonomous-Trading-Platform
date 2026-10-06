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
- [x] Nightly Postgres backup → S3 (`infra/ops/backup_postgres.sh`, systemd timer at 23:30 ET via `infra/ops/install_timers.sh`; runbook `docs/operations/runbooks/postgres-backup-restore.md`). Dump and restore tested locally (row counts match on all 90 tables); the S3 upload has only run against a stand-in. **Box setup still to do:** AWS CLI, bucket + lifecycle rule, instance role, `BACKUP_S3_BUCKET` in `infra/.env`, run the installer
- [x] Auto-deploy (`.github/workflows/deploy.yml`) skips 9:00–16:30 ET on weekdays (and checks the market calendar): `scripts/deploy_window.py`, frozen from 30 min before the open to 30 min after the close, so half days and holidays follow the exchange calendar. A skipped deploy is **not** retried automatically: re-run the workflow after the close
- [x] Emergency override: manual `workflow_dispatch` with a required `reason`, logged (job summary + `~/ratp-deploy.log` on the box, which records every deploy)
- [ ] Scheduler stops gracefully on deploy: finishes the in-flight step and doesn't start a new one (matters for the post-close end-of-day chain, which falls outside the freeze). **Half done:** an in-flight intraday tick or end-of-day run finishes on SIGTERM (6 min grace, tested) and no new one starts. Still open, do with 1.3's per-step wrapper: stopping *between* end-of-day steps, and resuming the chain after a restart (today a restart after 18:00 ET re-runs the whole chain, because "done for today" is only held in memory)

**1.3 Scheduling (soak-loop runner, no Airflow; see §3.1)**
- [ ] Market calendar refresh (holidays, half days)
- [x] Extend `PaperTradingGoldenPathOrchestrator.run_eod_maintenance` past features: final ingestion, broker reconciliation, then the governance stack and rebalance **in the backtester's hook order** (§3 end-of-day row), passing `dataset_version_id` along the chain. Done 2026-10-05; the backtester's actual order differs from the §3 row and was followed, see `pre-launch-1.3-scheduling.md` §1–2
- [x] Per-step retry wrapper; every step recorded in `job_runs` (`EodChainRunner`: per-step rows under one parent per trading date, retry cap, stop between steps, resume after restart)
- [ ] Parquet publish to S3 at the end of the end-of-day chain
- [ ] Weekly review and monthly universe steps in the runner (roadmap Step 6, without Airflow)
- [ ] Host timers: nightly Postgres backup (1.2), weekly retention job

**1.4 Observability (before the worker exists)**
- [ ] Resource tags: `deployment.environment` (paper/live/backtest, from `APP_ENV`), `service.name` per role, `host.name`
- [ ] All alert rules (`infra/observability/prometheus/alerts/ratp-alerts.yaml`) exclude `deployment_environment="backtest"`
- [ ] `hostmetrics` + container stats on A; watch memory and upsize to t4g.medium if needed
- [ ] Dashboards: Infra (host dropdown) · Trading ops · End-of-day pipeline · Research pipeline (exists)
- [ ] Heartbeat panel: "no trading cycle in 15 min during market hours" and "end-of-day chain not finished by ~19:00 ET" (stands in for Airflow's missed-run visibility)

**1.5 Soak**
- [ ] 1–2 weeks of unattended paper running before relying on it

### Phase 2: worker bootstrap (can overlap with A's soak)

**2.1 Box**
- [ ] `docker-compose.worker.yml`, own Postgres, migrations
- [ ] `.env.worker`: `APP_ENV=backtest`, `NO_LIVE_TRADING=true`, market-data keys only
- [ ] Separate deploy target for B
- [ ] OTel collector on B → A, with `hostmetrics` + container stats

**2.2 Data**
- [ ] S3 dataset pull, pinned to the dataset versions A published (needs 1.3's publish, **or** a one-time seed of S3 from the dev machine's Parquet to start sooner)

**2.3 On-demand runs only (nothing touches A yet)**
- [ ] Runner wrapper: reset → delete stale checkpoint → run → artifact to S3 → run summary
- [ ] Span/log attributes: `run.mode`, `backtest.run_id`, `fixture`, `sim_timestamp`; keep `run_id` off metric labels
- [ ] Sample backtest traces heavily (or don't export them)
- [ ] Dashboard: Worker/research runs

### Phase 3: connect worker → A (guardrails before automation)

**3.1 Guardrails first**
- [ ] Every automated decision is audited (`governance_audit_events`, `portfolio_review_decisions`): what, why, which worker run
- [ ] A per-automation off switch (research publish, review swaps, rebalance, promotion/demotion) that works without a deploy
- [ ] Rate caps stay on (max swaps per review, tenure, swap interval, turnover cost)
- [ ] A refuses worker results built on data older than N days
- [ ] Kill switch overrides all automation
- [ ] Dashboard panel: "automated decisions in the last 7 days" plus "worker results rejected"

**3.2 Hand-off**
- [ ] **API hand-off contract**: results carry `dataset_version`, code version (git SHA), run ID and fixture; A rejects stale or mismatched results; calls are idempotent by run ID

**3.3 Switch automation on in order of risk, one at a time with a few cycles between**
- [ ] (a) Weekly bench re-sims + scorecards from B, plus the weekly review DAG on A
- [ ] (b) Monthly research / strategy generation → bench
- [ ] (c) Allocation/governance parameter changes, last (riskiest)

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
