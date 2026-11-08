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
- **Late bars were dropped, silently.** A 5-minute bar validated more than 30 s after its
  window ended was flagged LATE and then thrown away (not stored, not counted received);
  once every symbol of a tick was late the cycle had nothing to finalize, so no missing-bar
  record, no error, `job_completed`. With the drift that was every tick from ~10:30 ET:
  1055/1146/1197 BAR_LATE audit rows per day, and the stored day was 167 / 192 bars
  (9:30–11:00 ET) instead of ~1700, so strategies ran on stale data and the S3 datasets
  are 90% empty for those days. Late bars are now kept with the LATE flag (freshness for
  trading is the readiness deadline's job), the window is `INGESTION_LATE_BAR_SECONDS`
  (default 120), and each tick with late bars logs one `ingest_bars.late_bars` warning.
- **Worker deploy never ran.** `Deploy worker (Box B)` reported success while skipping:
  the `WORKER_HOST` secret is not visible to the workflow (B had no runner logins and sat
  12 commits behind main). Fix the repository secret, then `workflow_dispatch`.
- **Feature versions not registered on B.** Publish manifests only carried the
  `dataset_versions` row; returns/regime versions live in `feature_dataset_versions`, so B
  logged `no_registry_row` for each. Manifests now also carry `feature_dataset_version`
  and the pull registers it.
- Box A's root volume was grown 29 → 50 GB online the same night (growpart + resize2fs).
  All four fixes merged (PRs #150, #151) and deployed to A and B 2026-10-15 ~04:10 UTC.
  **The soak clock restarts with the Thu 2026-10-15 session.**
- Why there were **0 signals, 0 order intents, 0 fills** in the three days (A's DB, checked
  2026-10-15): `portfolio_memberships` is empty, so the cycle runs the legacy single-strategy
  path and picks one approved row by name (`macd_crossover_v1`, a moving-average crossover
  with default parameters = 31-bar warm-up). With ~8 stored bars per symbol per day it never
  warmed up before 11:00 ET. Not a strategy or governance fault. The nightly rebalance is a
  no-op and auto-promotion logs `missing governance evidence` because the three approved
  rows came from the dev dump without a research run behind them, and the nine candidates
  have no bench evidence yet (that is 3.3(a)'s job).

**1.6 Next session checklist (after the first clean day)**

*Verify Thu 2026-10-15 was a clean soak day (Box A):*
- [ ] Scheduler log: ticks at HH:M5:10 all session; no `ingestion_deadline_missed`, no
      `Too many missing bars`, few or no `ingest_bars.late_bars`
      (`docker compose logs scheduler --since 24h | grep -c trading_cycle_degraded` → 0)
- [ ] Stored bars for the day ≈ 1,700 (22 symbols × 78), not ~170: count rows in
      `data/bars/raw/dataset_version=raw_bars_<date>T133000Z_*/`
- [ ] `audit_logs`: `BAR_LATE` near 0, `BAR_MISSING` small, no `RUN_FAILED`
- [ ] `strategy_runtime_states.last_evaluated_bar_timestamp` = the last tick before close
- [ ] `signals` has rows from ~12:05 ET on (31-bar warm-up met); `order_intents` / `fills`
      non-empty if the crossover fired
- [ ] EOD chain finished; S3 `datasets/published/<date>.json` lists the day's versions
- [ ] Box B's 20:30 ET pull (`journalctl -u ratp-worker-pull.service`) registered the
      returns/regime versions with no `no_registry_row` warnings
- [ ] Grafana Trading Ops / Infra: memory flat on t4g.medium, no heartbeat gaps

*Log formatter (small, do before anything else):*
- [ ] `observability/logging.py`: append the record's `extra` fields to the console line
      as `key=value` (JSON for dicts/lists), so `docker compose logs` shows strategy id,
      warm-up bars, signal/order counts, late/missing symbols without opening Loki
- [ ] Test: a log call with `extra={...}` renders the fields; a call without renders as today
- [ ] Deploy and confirm one scheduler tick's lines on Box A read end to end

*Seed an initial active set (resolved 2026-11-02):*
- [x] Members decided and seeded 2026-10-27 through `ActivePortfolioService`: `mean_reversion_v1`
      and `momentum_v1` ACTIVE, `macd_crossover_v1` BENCH (see 3.3(a))
- [x] `portfolio_mode_enabled` was **not** switched on: the memberships are inert and the
      whole soak has run the legacy single-strategy path (`macd_crossover_v1` until 10/22,
      `mean_reversion_v1` from 10/23, picked by `_resolve_active_strategy`'s ordering).
      Switching it on is now its own step, **3.3(0)**, after the soak completes
- [x] `portfolio_review_mode=off` and `worker_results_enabled=false` until 3.3(a)
- The verify list (one runtime per member, aggregator inputs, sleeve ledgers, rebalance no
  longer a no-op) moved to 3.3(0)

*Then start 3.3(a)* (see the Phase 3 checklist below for the sub-steps).

**1.7 Findings 7–8 and the go-live-on-paper decision (Thu 2026-10-15 → Sat 2026-10-18)**

Thu 2026-10-15 was the first day the order path ran: after the Alpaca data-host fix (PR #152)
10 BUY signals became 10 `order_intents` with `ORDER_GENERATED` audit rows, but 0 orders reached
Alpaca and 0 fills landed. Two causes:

- **Finding 7: shadow mode was on.** `SHADOW_MODE_ENABLED` is unset in A's `.env` and defaults to
  `true`, so the submission job stops after the risk check and never calls the broker. This is the
  intended pre-paper gate, but nothing in this plan or `.env.example` said to turn it off.
  `.env.example` now documents it (PR #153). **Decision (Sat 2026-10-18): trade paper for real from
  Mon 2026-10-20.** Operator step on Box A (check the prompt reads `ip-172-31-42-34`), before 09:30 ET:
  - [ ] add `SHADOW_MODE_ENABLED=false` to `~/Autonomous-Trading-Platform/.env`
  - [ ] `docker compose up -d scheduler` (recreates the container so it picks up the new env;
        the EC2 instance is not touched)
  - [ ] confirm: `docker compose exec scheduler env | grep SHADOW_MODE` prints `false`
- **Finding 8: a throttle rejection killed the whole tick.** `MAX_ORDERS_PER_BAR=2` on A; the
  third BUY intent in the 15:00 ET bar raised `OrdersPerBarLimitExceededError`, which the
  submission job re-raised, so the tick ended `cycle_failed` and the two allowed intents never
  went out either. Fixed in PR #153 (merged + deployed to A Sat 2026-10-18): a throttled intent is
  now audited `ORDER_REJECTED_BY_THROTTLE`, its tracked order goes REJECTED, and the tick carries on.
- **Limits kept as they are on A** (decided 2026-10-18): 2 orders/bar, 20/hour, $100k gross
  exposure, $25k per symbol, $100k daily notional. Intents size at ~$25k each, so the gross cap
  binds after about four open positions; revisit once a week of fills exists.

*Monday 2026-10-20 checklist (first real orders):*
- [ ] First BUY signal → `order_intents` row → order visible in the Alpaca paper account →
      `fills` row → position/cash ledgers move (this path has never run live)
- [ ] `ORDER_REJECTED_BY_THROTTLE` audit rows are expected when >2 BUYs share a bar; no
      `cycle_failed` ticks from it
- [ ] EOD broker reconciliation agrees with Alpaca's positions
- [ ] Fri 2026-10-16's session was never reviewed (shadow mode still on, so no orders expected);
      the soak clock starts with the first full session that produces fills

**1.7 Calendar of first-time triggers (verify each the morning after)**

Box A has run cleanly since Thu 2026-10-22 (kill switch released; soak clock counts from
there, target ten clean sessions = Wed 2026-11-04). From here on the risk moves from "does a
tick work" to "does each scheduled thing fire the first time its date comes round". Every
end-of-day step is recorded in `runtime_job_run_steps` (`completed` / `skipped` / `failed`,
with `error_message`), so each line below is one query the next morning:

```
docker compose exec -T postgres psql -U ratp -d ratp -Atc "select step_name, status, started_at, duration_ms, error_message from runtime_job_run_steps where started_at > now() - interval '1 day' order by started_at"
```

Calendar pre-checked 2026-10-26 with `RealMarketCalendar` (exchange_calendars XNYS, zoneinfo
America/New_York): 10/30 is the month's last session, 10/31 closed, 11/2 is the week's first
session after the clock change, 11/26 closed, 11/27 and 12/24 close at 13:00 ET and report
`is_early_close`, 12/31 is December's last session. The clock docstring still says early closes
are not handled; `RealMarketCalendar.market_close` does take them from the exchange calendar.

| When | Trigger | Rule in code | First time on A | Verify |
|---|---|---|---|---|
| every Monday | `weekly_portfolio_review` | `is_first_trading_day_of_week` | 10/20 | [x] fired 10/26, `completed` in 11 ms (no-op while `portfolio_review_mode=off`); 10/22–23 `skipped` as expected |
| Fri 10/30 close | `monthly_universe_rotation` | `is_last_trading_day_of_month`, churn guard on, reason `scheduler_monthly` | never run on A (active universe is still the dev dump `proposed_2024-12-02`, 22 names) | [x] ran, step `failed`: empty proposal (Finding 16, 3.3a); chain continued, universe unchanged. Re-verified 2026-11-03 01:50 UTC with `atp universe monthly-rotation --dry-run` after PR #163: pool 500 captured, 18 → 19 (+HYG, LQD, XLF; −CRM, DIA), churn 27.8%, no warnings, no rejections. Next real run Mon 11/30 close |
| Sun 11/1 | daylight saving ends | all times via `America/New_York` zoneinfo | first clock change during the soak | [x] 11/2: tick #1 at 09:30:00 ET, then every boundary + 10 s through 15:55:10 (79 ticks, same as every prior session; the 79th at 15:59:59 is degraded `ingestion_deadline_missed` every day, not new); EOD chain 18:00–18:02 ET = 23:00 UTC, one UTC hour later than Friday as expected |
| Mon 11/2 | weekly review on the first session after a rotation | | | [x] `completed` in 12 ms on 11/2 (no-op, `portfolio_review_mode=off`); universe unchanged because the 10/30 rotation never activated |
| Thu 11/26 | Thanksgiving, closed | `is_trading_day` false | first holiday | [ ] runner logs "next market open" and runs no ticks, no EOD chain, no `runtime_job_runs` row for 11/26; B's nightly pull finds nothing new and exits clean |
| Fri 11/27 | early close 13:00 ET | `market_close` from the exchange calendar, `is_early_close` | first half-day | [ ] last tick ≤ 12:55 ET, EOD chain starts ~13:15 ET, `final_ingestion` has the half-day's bars only, no ticks between 13:00 and 16:00 |
| Thu 12/24 | early close 13:00 ET | same | second half-day | [ ] same as 11/27 |
| Thu 12/31 close | `monthly_universe_rotation` | | second rotation | [ ] same as 10/30; compare churn with the first |
| weekly, after 3.3(a) deploy | B's `ratp-worker-bench-resim.timer` → `POST /worker/results` | timer (Sat, after the nightly pull) | not built | [ ] A lists the run `accepted` (`GET /worker/results`); Monday's review `worker_run_id` points at it |
| nightly | backup → S3, B's dataset pull 20:30 ET | host timers | 10/12 | [x] running since 10/12 (`journalctl -u ratp-worker-pull.service`) |
| Sun 03:00 ET | retention on A | host timer | 10/18 | [x] running |

Anything that fires `failed` here resets the soak clock only if it needed a code fix; a
`skipped` on the wrong day or a step that never appears means the cadence rule is wrong and
is a finding.

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
- [x] **(0) Turn on portfolio mode: trade the seeded active set (first, after the soak)** —
  switched on Mon 2026-11-02 at 05:45 UTC (00:45 ET Tue); first portfolio-path session is Tue 11/3
  - *Why first:* the review in (a) decides swaps for an active set that is not trading, and
    the bench, sleeves and allocation only become visible once the portfolio path runs. The
    user wants this live early as the realistic read on whether the system is moving in the
    right direction, with the bench and rotation layered on as they come up.
  - *Risk:* lowest in Phase 3. One operator setting, no deploy, reversible the same way; the
    kill switch still overrides everything. The code path is Step 1 of the rotation plan
    (done 2026-09-28) and ran in every rotation backtest, but has never traded on A.
  - *When:* decided Mon 11/2 night to flip immediately rather than wait for Thu 11/5: the
    last two soak sessions only re-test plumbing that has run clean for eight, the portfolio
    path does not touch ingestion or the EOD chain, and the flag is reversible on the next
    tick. Do not start any (a) switch the same week.
  - *Operator step on A* (prompt reads `ip-172-31-42-34`). **Finding 17:** `atp settings set`
    refuses `portfolio_mode_enabled` (and `bench_management_enabled`): the CLI only accepts
    the fields of `OperatorSettingsUpdateRequest`, neither flag is in it, and the box has no
    `OPERATOR_SETTINGS_UPDATED` audit rows at all, so the 10/27 bench flag was also set
    outside the CLI. Done with SQL from the laptop (a file piped over ssh, because long
    quoted one-liners get a newline inserted when pasted into PowerShell):
    ```
    type ~lip.sql | ssh -i ~/.ssh/trading-platform-laptop ubuntu@3.133.10.131 'cd ~/Autonomous-Trading-Platform && docker compose exec -T postgres psql -U ratp -d ratp'
    -- flip.sql:
    update operator_settings set portfolio_mode_enabled = true, updated_by = 'tristan', updated_at = now();
    select portfolio_mode_enabled, bench_management_enabled, updated_at from operator_settings;
    ```
    No restart needed: `resolve_strategy_runtimes` reads the row every tick. Roll back with
    the same statement and `false`; positions already opened stay owned by their sleeve and
    the legacy path winds them down. Fixed the same night: both flags are now in
    `OperatorSettingsUpdateRequest`, the settings DTO and the REST response, so
    `atp settings set --key portfolio_mode_enabled --value true` and `PUT /api/v1/settings`
    flip them with an `OPERATOR_SETTINGS_UPDATED` row (tests on the CLI parser and the route).
  - **Finding 18 (found 2026-11-02 while reviewing allocation): the portfolio drawdown
    ladder's peak is dev-dump data.** `portfolio_drawdown_governance_state.peak_equity_usd`
    was $117,955 with a breach recorded 2026-07-19, both from the July dump, not this
    account (reset to $100k on 10/09; real high since then $100,531). Against the stale
    peak the account read 18% down, past the 15% threshold; the action is `warn_only` with
    `auto_resume`, so nothing fired, but every nightly `DRAWDOWN_GOVERNANCE_LADDER_RUN`
    was measuring the wrong thing. Reset by SQL (`~/ladder_reset.sql` on the laptop, same
    pipe-over-ssh as the flip): peak = 100531.26, breach fields cleared; the ladder raises
    the peak itself from the nightly equity. Open question for later: whether the ladder
    should seed its peak from the first broker sync after an account reset rather than
    carrying whatever the dump held.
  - **Dump-era rows (scoped 2026-11-02, not done):** A's SoR still carries the dev dump's
    2023-2024 backtest and the July 2026 fixture: 29.7k `fills`, 35.9k `signals`, 29.7k
    `order_intents`, 84.8k `run_manifests`, 29.7k `cash_snapshots`, 15.7k
    `position_snapshots`, 3.96M `audit_logs`, all dated before 2026-10-01. The live
    performance metrics read a trailing window (at least 90 days times a lookback
    multiplier), which keeps the 2023-2024 rows out but can still pick up the July 2026
    fixture (equity in the millions on 07/15-07/19); treat any live-metric or "all time"
    dashboard number as suspect until the purge is done.
    Purge is a scripted, FK-ordered delete by `run_id` with the nightly backup verified
    first and a restore test after; do it on a weekend, not mid-soak, and not the night
    before the first multi-strategy session.
  - *What changes:* `momentum_v1` joins `mean_reversion_v1` (re-sim 2026-10-27: ~10 turns a
    day, 249 trades over the window), so expect many more intents and more
    `ORDER_REJECTED_BY_THROTTLE` / gross-cap rejections against the current limits (2 per
    bar, 20 per hour, $100k gross, $25k per symbol). Intents are sized from membership
    budgets (`budget_pct`), not one strategy's full capital. Revisit the limits after two
    sessions with fills, not before.
  - *Verify the first session (Tue 11/3):*
    - [ ] Scheduler log: `trading_cycle.portfolio_runtimes_resolved` once per tick and two
          `evaluate_strategy_job | job_started` per tick (today: zero and one)
    - [ ] `portfolio_signal_aggregator.summary` with two inputs (needs the 1.6 log-formatter
          item or Loki: `{service_name="ratp-scheduler"} |= "aggregator.summary"`)
    - [ ] `signals` has rows for both strategies; `strategy_runtime_states` gains a
          `momentum_v1` row
    - [ ] `strategy_sleeve_ledger` / `strategy_sleeve_positions` populate (both empty today)
          and the EOD `broker_reconciliation` still agrees with Alpaca
    - [ ] No `cycle_failed` ticks; throttle rejections audited, not fatal (Finding 8 shape)
    - [ ] Next nightly `allocation_rebalance` stops logging
          `STRATEGY_ALLOCATION_REBALANCE_NOOP` once quality scores exist (weekly interval
          guard still applies); `strategy_quality_score_history` gains rows
    - [ ] Grafana Trading Ops: per-strategy panels show two sleeves
  - [ ] After two clean sessions with both strategies filling, note here whether the order
        limits were changed, then start (a)
  - **First session, Tue 2026-11-03 (reviewed after the close).** Mechanics: the portfolio
    path ran all 79 ticks (`portfolio_runtimes_resolved` 79, three `evaluate_strategy_job`
    starts per tick, aggregator summary 78, no `cycle_failed`), `momentum_v1` traded for the
    first time (128 fills, $74k notional; `mean_reversion_v1` 37 fills, $28k), sleeve tables
    populated, EOD chain clean, Box B pull clean. Broker equity $97,238 at the close
    (+$843 on the day). Four findings, in order of what they cost:
    - [x] **Finding 19: the throttle starves exits.** 985 intents, 829 rejected by the
          2-per-bar cap. The orphan `__unattributed__` sleeve (the inherited DIA / QQQ / TLT
          book, ~$75k) generated 78 sell intents per name and filled none: the submission
          job takes intents in evaluation order (active members first, orphan sleeves
          appended last) and the two slots go to the first two every bar; only AMD and AVGO
          got out, on the one bar with a free slot (09:41 ET). Momentum alone was throttled
          575 times. So the book is still 75% the old single-strategy positions and both
          sleeves are squeezed into ~$25k of gross-cap room. Fix: order exits before entries
          at submission (sells first, orphan / winding-down sleeves first among them), and
          decide a multi-strategy `MAX_ORDERS_PER_BAR` (2 was set for one strategy; with
          three books and a 5-bar momentum rule it blocks 84% of orders).
          **Code fixed 2026-11-03** (`_order_for_submission` in `portfolio_evaluation.py`):
          the aggregator now hands the submission job sells first, and among sells those of
          non-ACTIVE (orphan / winding-down) sleeves first, evaluation order otherwise, so
          whatever the per-bar cap is, exits take the slots before entries. The cap itself
          is an env change on A, see the post-deploy steps below.
    - [x] **Finding 20: sleeve books drift from the broker within one session.** Tonight's
          reconciliation: AMZN sleeves 8 / broker 0, AAPL 4 / 3, TSLA 4 / 4, META 4 / 4;
          67 `sleeve_reconciliation.mismatch` warnings, 5 `sleeve_ledger.fill_not_applied`
          errors ("sleeve 'momentum_v1' holds 0 AAPL; cannot sell 1"), 28 ticks with sells
          clamped to account holdings. Mechanism: a bar's fills are only applied to the
          sleeves by the reconciliation step of the *next* tick, which runs after that
          tick's evaluation; evaluation books internal crosses immediately. So a momentum
          sell that filled at the broker late in bar T is still in the sleeve at T+1, the
          same shares get crossed to mean reversion, and when the broker fill is finally
          applied the sleeve is already empty and the fill is dropped. The broker ends up
          holding less than the sleeves say, forever. Fix: apply pending fills before
          evaluation (reconcile first in the tick), or make the crossing service net
          against open orders; and a nightly sleeve-vs-broker resync so drift cannot
          compound.
          **Code fixed 2026-11-03**, both halves: (1) in portfolio mode the trading cycle
          runs a `pre_evaluation_reconciliation` step (same job and failure handling as the
          post-submission one) before `trading_evaluation`, so bar T's fills are in the
          sleeves before bar T+1 is evaluated and crossed; (2) the crossing service is not
          given intents for a symbol that still has an open broker order (logged as
          `portfolio_evaluation.cross_skipped_open_orders`); they go to the broker
          uncrossed. Still open: the nightly sleeve-vs-broker resync (drift that already
          happened is cleared by the ledger resync below, which rebuilds the sleeves
          from the broker book).
    - [x] **Finding 21: the position / cash ledger is $24k off the broker, and the drawdown
          ladder reads it.** Ledger equity $121,007 vs broker $97,215 (cash $29,786 vs
          $5,853); positions AMZN 7 / 0, TSLA 1 / 4, AAPL 2 / 3, META 3 / 4. This predates
          today (ledger $116k at Monday's close) and is the drift the ledger-resync runbook
          exists for. The ladder evaluates `CashSnapshotRepository.get_latest()` (ledger),
          so its peak rose to $121,022 today instead of being reset (Finding 18's reset was
          not run; it would have been overwritten anyway). The `atp execution
          validate-broker-consistency` check reports `drifted`. Fix: resync per the runbook
          after the close, then point the ladder at broker equity.
          **Resynced 2026-11-04 01:19 UTC** per the runbook: cash matched to the cent, peak
          reset to the resynced $96,513. The validator still said `drifted` on equity /
          buying power only: after-hours marks had moved $9 between the sync and the check
          (tolerance is $0.01), so that is noise, now noted in the runbook. Still open:
          the ladder reads ledger equity, and the validator has no mark-to-market tolerance.
    - [ ] **Finding 22 (cadence, no fix needed yet):** `macd_crossover_v1` moved BENCH →
          ON_DECK at the first tick (`selected_on_deck`): the active-set refresh fills
          on-deck seats from eligible candidates, so the "bench until re-sim evidence" seed
          lasted one tick. It is shadow-only (shadow sleeve: AMZN 4, CRM 1, META 1, MSFT 1,
          QQQ 2, TLT 23), so harmless. `active_portfolio.below_minimum` warns every tick
          because `min_active_strategies` is 3 with two members; set it to 2 or accept.
    - Also seen: DIA / TLT dividends pending on the inherited book withheld the orphan
      adoption for the first ticks (`adoption_withheld` ×5, `corporate_action.skipped`), then
      adopted; symbol cap ($25k) rejected momentum's DIA buys because the orphan sleeve
      already holds $25k DIA.
    - **After the fix deploys (manual, on A, after the close):**
      1. [x] Ledger resync per `docs/operations/runbooks/ledger-resync.md` (Finding 21):
         kill switch on, `sync-broker-state` + `sync-position-snapshot` with one fresh
         run id, `validate-broker-consistency --paper-only` must print `ok`, then the
         runbook's step-4 peak reset (this replaces Finding 18's hand-written reset: the
         resynced equity is the right peak), kill switch off with `--confirm-release`.
      2. [x] `MAX_ORDERS_PER_BAR=6`, `MAX_ORDERS_PER_HOUR=60` in A's `.env`, then
         `docker compose up -d scheduler`. Three books (two members + the orphan sleeve
         winding down) need more than two slots a bar; 6 lets the orphan exits and both
         members trade in the same bar, and 60/hour still caps a runaway loop at ~1
         order a minute. Revisit once the orphan book is flat.
      3. [ ] Optional: `min_active_strategies` 3 -> 2 in `operator_settings` (Finding 22)
         to stop the per-tick `below_minimum` warning.
      4. [x] Next session checked below (the trading cycle does not write step rows;
         the evidence is two `RECONCILIATION_COMPLETED` audit rows per tick).
  - **Second session, Wed 2026-11-04 (reviewed after the close).** Both fixes held: 79
    ticks, no `cycle_failed`, 156 reconciliation audit rows (two per tick), 5 internal
    crosses all applied, throttle rejections 49 (829 on Tuesday). The orphan book
    unwound in the morning: TLT 306 and QQQ 45 sold inside the first six minutes, DIA 48
    by 12:36 ET. Broker equity $97,901 at the close, +$856 on Alpaca's daily change
    (a good part of it the inherited book getting out). Sleeve mismatch warnings fired
    78 times, but every one was AMZN 8 / AAPL 4 carried over from Tuesday (Finding 24)
    plus the TLT short below; nothing new drifted in-session. Two findings:
    - [x] **Finding 23: a partial fill across a tick boundary left the account short.**
          The orphan sleeve's 306-share TLT sell filled 304 at 14:34:52 and 2 at 14:35:50.
          The 14:35:10 tick ran in between: pre-evaluation reconciliation applied the 304,
          the sleeve showed 2 left, the account showed 2 held (committed to the open
          order, which the sell clamp does not see), so a second 2-share sell went out.
          Both filled; TLT is -2 at the broker. Knock-on: `momentum_v1` wanted ~20 TLT all
          day and Alpaca refused 29 times (a buy that flips a short to long is rejected),
          and the second fill was dropped as `fill_not_applied`. Finding 20's open-order
          check only kept such symbols out of *crossing*. **Code fixed 2026-11-04:**
          intents on a symbol with an open broker order are held back from submission
          entirely for that bar (`portfolio_evaluation.intents_held_open_orders`); the
          order resolves, the fill lands via pre-evaluation reconciliation, and the next
          bar re-evaluates from the true sleeve. Manual: cover the 2 TLT on the dashboard.
    - [x] **Finding 24: the ledger resync does not touch the sleeves.** `sync-broker-state`
          / `sync-position-snapshot` rebuild the account ledger only; the sleeve tables
          kept Tuesday's AMZN 8 / AAPL 4 over-claims and warned all day. **Code fixed
          2026-11-04:** `StrategySleeveLedgerService.resync` writes sleeves down to the
          account (unattributed sleeve first, then members, at the sleeve's own cost, no
          P&L), adopts unowned shares, skips symbols with an open order, and reports a
          broker short as unresolved. Exposed as `atp execution sync-sleeves [--dry-run]`
          (added to the ledger-resync runbook) and as the nightly EOD step
          `sleeve_resync` after `broker_reconciliation` (portfolio mode only). This is
          the "nightly sleeve-vs-broker resync" from Finding 20.
    - Also seen: the six sells from the 15:55 ET tick filled 1-5 s after that tick's
      reconciliation and sat `SUBMITTED` in our table overnight (the 15:59:59 degraded
      tick and the EOD chain do not apply fills); Thursday's first tick applies them.
      `below_minimum` warned 92 times: `min_active_strategies` is still 3.
    - **After this fix deploys (manual, on A, after the close):**
      1. [ ] Buy 2 TLT on the Alpaca dashboard to close the short (nothing in the system
         will: the orphan sleeve is empty and momentum's buys are refused while short).
      2. [ ] `docker compose exec app atp execution sync-sleeves --paper-only --dry-run`,
         then without `--dry-run`. Expect AMZN 8 and AAPL 4 written down; symbols with an
         open order are listed as skipped and are fine to leave (Thursday's first tick
         applies their fills).
      3. [ ] Optional: `min_active_strategies` 3 -> 2 (Finding 22).
      4. [x] Next session, check: no `intents_held_open_orders` storm (a handful per day
         is expected), no broker rejections, the EOD `sleeve_resync` step output in
         `runtime_job_runs`, and the sleeve mismatch warnings gone. (Checked Thu, below:
         one hold-back, 33 TLT rejections from the uncovered short, resync output present,
         mismatch warnings still the stale AAPL / AMZN / TLT until the EOD step cleared
         the first two.)
  - **Third session, Thu 2026-11-05 (reviewed after the close; PR #167 deployed 00:30
    UTC, steps 1-3 above not yet run).** Mechanics clean: 79 ticks, no `cycle_failed`,
    156 reconciliation rows (two per tick), `intents_held_open_orders` once all day, no
    `fill_not_applied`, no `cross_skipped_open_orders`. 403 intents (344 Wed), throttle
    rejections 91 (49 Wed; 65 of them momentum buys), 33 broker rejections, all momentum
    TLT buys refused while the account is short 2 (09:31 to 13:46 ET). Broker equity
    $97,393 at the close, -$487 on Alpaca's daily change: the first down day in portfolio
    mode, with heavy 5-minute round-tripping (AAPL bought and sold 17 shares each for
    about $2 net). Step 2 above turned out to be moot: the new EOD `sleeve_resync` step
    ran at 18:00 ET and wrote AAPL down 1 + 3 and AMZN 6 + 2, skipped CRM / IWM / META /
    MSFT (15:55 orders still open) and reported `unresolved: TLT -2`. The sleeve mismatch
    warning fired on all 78 ticks with the stale AAPL -4 / AMZN -8 / TLT -2; Friday should
    show only TLT until the short is covered. `below_minimum` warned 79 times. One finding:
    - [x] **Finding 25: the ledger resync double-counts fills that are still open in our
          table.** EOD `broker_reconciliation` and `validate-broker-consistency` both say
          `drifted`: the account ledger holds AAPL 3 the broker does not, and its cash is
          about $1,000 low, while equity is within $20 of the broker. Trace: Tuesday's
          15:55 tick bought 3 AAPL, the fill landed at 15:56 after that tick's
          reconciliation, so the tracked order sat `SUBMITTED` overnight. The Finding 21
          resync at 20:19 ET copied the broker book, which already held those 3 shares.
          Wednesday's 09:30 tick then applied the open tracked order on top: ledger AAPL 3
          -> 6, cash -$995 (`position_snapshot_items` 20:19 BROKER_RECONCILED AAPL 3, 09:30
          LEDGER AAPL 6). Two days of flat round-trips carried the extra 3 forward. The
          sleeve resync (Finding 24) already skips open-order symbols; the account-ledger
          resync did not know about them. The missing TLT in the ledger (broker -2) is the
          Finding 23 second sell the ledger could not book below zero; covering the short
          makes both sides zero. **Code fixed 2026-11-05** (`BrokerRuntimeSyncService`):
          `sync-broker-state` / `sync-position-snapshot` compute the fills the broker has
          executed on our open tracked orders that no tick has applied yet (same
          incremental-fill extraction as the reconciliation job) and write the broker book
          *net of* them (cash and shares held back, a flat symbol with a pending sell
          carried at the fill price), so the next tick's application lands the ledger on
          the broker; `validate-broker-consistency` compares cash against the same
          expectation and all three print the `pending_fills` they held back. Also done,
          the open half of Finding 21: both drawdown guards (`run_trading_cycle` and the EOD
          `allocation_rebalance`) now measure live broker equity (`fetch_broker_equity`)
          and fall back to the ledger's cash snapshot only when the broker does not
          answer, so ledger drift is a bookkeeping problem, not a safety one.
    - **After this fix deploys (manual, on A, after the close):**
      1. [x] Ledger resync per `docs/operations/runbooks/ledger-resync.md` (kill switch
         on, `sync-broker-state` + `sync-position-snapshot` with one run id,
         `validate-broker-consistency --paper-only` must print `ok` and list the four
         15:55 orders under `pending_fills`, step-4 peak reset, kill switch off). Safe
         with the four open orders because of the fix. **Done 2026-11-06 06:51 UTC**
         (PR #168 deployed 06:48): the four 15:55 fills (CRM sell 2, IWM 4, META 1, MSFT
         1) were held back, the ledger now has CRM 6 / MSFT 1 / no IWM or META / no
         AAPL, `cash.cash` matched the broker to the cent; the validator still said
         `drifted` on equity / buying power / portfolio value only, which is the
         after-hours marks moving in the 10 s between sync and check (runbook note).
         Peak reset to $97,389.53. Two things to know: `enable-kill-switch` reports
         `canceled_order_count: 4` for the four already-filled tracked orders (Alpaca
         ignores the cancels; the rows stay open for Friday's first tick) and left the
         dashboard TLT buy untouched. And the snapshot copied the broker's TLT -2 into
         the ledger, so once the cover fills Friday morning the ledger will carry TLT -2
         (about $164) against a flat broker until the next resync; harmless (the
         pre-trade caps read broker positions), fold it into the weekend resync.
      2. [x] Buy 2 TLT on the Alpaca dashboard (carried over; the only item left from
         Wednesday's list that still matters). Placed 2026-11-06 06:48 UTC as a day
         market order, queued for Friday's 09:30 ET open.
      3. [ ] Optional: `min_active_strategies` 3 -> 2 (Finding 22).
      4. [x] Friday, check: `validate-broker-consistency` reads `ok` after the first tick
         applies the four fills; no TLT broker rejections once covered; sleeve mismatch
         warnings gone; `trading_cycle` logs show no `equity_source: ledger` fallbacks.
         **Fri 2026-11-06:** 79/79 ticks, 0 failed runs, 0 broker rejections, 369 orders
         generated, 48 throttled; the TLT cover filled at the open and momentum traded TLT
         44/44; close equity $97,557.90 (-$322.91 on the day, +0.9% on the week from
         Monday's low). 45 `SLA_BREACH` rows (Thu 11), the thin IEX names COST / DIA /
         BKNG / TLT / MA, feed not code (SIP is the fix; see section 6). EOD chain 14/14
         completed; `broker_reconciliation` reported `drifted` on the six 15:55 sells that
         filled at 15:56 ET after the last tick (AMZN 1, AVGO 4, COST 1, CRM 8, HD 5,
         MSFT 2), `sleeve_resync` skipped exactly those six; the ladder raised the peak
         to $97,556.96 from broker equity (Finding 21 live).
      5. [x] **Full ledger resync, Fri 2026-11-07 01:02-01:03 UTC** (after the close, per
         the runbook, all steps): kill switch on (`canceled_order_count: 6`, the six
         already-filled 15:55 orders, harmless as on Thursday); `sync-broker-state` and
         `sync-position-snapshot` held back the six sells; `validate-broker-consistency`
         printed `ok` with no mismatches at all; `sync-sleeves` dry run and real run:
         nothing adopted or written down, the six symbols skipped for Monday's first tick;
         peak reset to $97,442.60; kill switch off. The ledger's TLT -2 is gone (broker
         flat, no pending TLT fill). Ledger now: cash $76,339.55 (broker $83,317.21 less
         the $6,977.66 held back), 13 symbols, sleeves unchanged.
  - **Where 3.3(0) stands after four sessions:** three clean sessions under the fixed code
    (Wed, Thu, Fri), both strategies filling, ledger reconciled to the cent. Order limits
    unchanged (`MAX_ORDERS_PER_BAR=6`, `MAX_ORDERS_PER_HOUR=60`); the orphan book is flat,
    so revisit with Thu's 91 / Fri's 48 throttle rejections in hand. Start (a) next week.
- [ ] (a) Weekly bench re-sims + scorecards from B, plus the weekly review DAG on A
  - [x] Read endpoint on A: `GET /api/v1/worker/bench-inputs` (`worker` role) returns the
        ACTIVE / ON_DECK / BENCH strategies with their stored parameters, the shared window
        (`resolve_bench_window`, `bench_resim_window_days`), dataset version, symbols and A's
        git SHA (`BenchResimInputsService`, `BenchResimInputs` contract)
  - [x] Worker job: `atp worker bench-resim [--as-of] [--run-id] [--no-pull] [--no-publish]`
        fetches the inputs, pulls the datasets, re-simulates every strategy
        (`BenchResimulationService.resimulate_specs`, same sizing/seed as A's own review),
        writes the `bench_resims` payload and posts it (exit 0 accepted, 2 rejected)
  - [x] Seed `portfolio_memberships` on A (done 2026-10-27 through `ActivePortfolioService`:
        `mean_reversion_v1` + `momentum_v1` ACTIVE, `macd_crossover_v1` BENCH by operator,
        `bench_management_enabled=true`; trading untouched while `portfolio_mode_enabled=false`).
        Repeatable since `atp portfolio membership list | refresh --updated-by X --enforce |
        set --strategy-id S --status bench --reason R --updated-by X --enforce` (dry-run previews)
  - [x] Dry run on B 2026-10-27: `worker bench-resim --as-of 2026-10-26 --no-publish` → 19 s,
        3/3 strategies, git SHAs match A. Window 07-29..10-26 but A only holds 5-min bars
        since mid-October, so the numbers are thin until ~mid-January (momentum_v1: 249
        trades, turnover 10×/day, score 2.38; the other two ≈ 0.8–0.9 on 15–18 trades).
        Findings: `daily_returns` empty for every outcome and no SPY market returns → the
        review's correlation lens has nothing until fixed (see 3.3a findings below)
  - [x] Weekly timer on B (`ratp-worker-bench-resim.timer`, Sat 06:00 ET, after Friday's pull;
        `sudo infra/ops/install_worker_timers.sh` to install). Payload default path moved under
        the mounted `artifacts/worker/bench_resims/` (the first dry run wrote into the job
        container's /tmp and was lost with it)
  - [ ] Flip `worker_results_enabled`, then `portfolio_review_mode=advisory`, then `auto`
  - **3.3(a) findings (2026-10-27, from the first dry run on B)**
    - [ ] **Finding 14: live raw-bars versions are one session each.** Every EOD publish cuts
          a `raw_bars_<date>T133000Z_*` version whose coverage is that single day (25 versions on
          A, one per session). `resolve_bench_window` takes "the latest validated raw_bars"
          as the re-sim dataset, so the 63-day window re-simulated 2026-10-26 only: one day
          of bars, one daily close, hence empty `daily_returns` and no SPY market returns
          (`bench_review.no_market_returns`). Box A's own bench review (`bench_hooks`) has the
          same gap; in backtests it was hidden because the replay used its cumulative dataset.
          Fix on A: a rolling multi-session raw-bars version written at EOD after
          `resolve_raw_bars_dataset` (union of the last `bench_resim_window_days` sessions'
          per-day versions, coverage start..end spanning them, validated and published like
          the daily ones), and `resolve_bench_window` prefers the latest rolling version. B
          pulls it like any other version, so the worker job needs no change. Until this is
          in, weekly re-sims are plumbing tests only; do not install the timer on B or flip
          `worker_results_enabled`.
          **Fix (branch `fix/rolling-raw-bars`):** `RollingRawBarsService` reads the newest
          `bench_resim_window_days` per-session versions back through the bounded reader,
          merges them (last `bar_id` wins, as the daily chunk writer does), writes one new
          immutable version with per-file checksum rows, and registers it `validated` with
          `metadata_json.dataset_lifecycle = "rolling_bench_window"` and
          `source_versions`. New EOD step `rolling_raw_bars` (after the monthly rotation,
          before `operations_health`; no-op when the newest rolling version already has the
          same sources). `resolve_bench_window` prefers the newest rolling version and
          clamps the window start to its coverage, so until A holds ~15 calendar days of
          sessions the window is reported as `insufficient_data_for_window` rather than
          re-simulated on one day. Follow-up: a rolling version is written every session
          (~63 sessions of 5-minute bars each); `retention.sh` leaves Parquet alone, so
          add pruning of rolling versions older than ~14 days before disk becomes a topic
    - [ ] After Finding 14: re-run the dry run, confirm `daily_returns` has one entry per
          session and `market_returns` is populated, then install the timer
          (`sudo infra/ops/install_worker_timers.sh` on B)
    - [x] **Backfill the bench window instead of waiting for it (2026-11-04).** A has held
          five-minute bars only since 2026-10-09 (19 sessions), so a 63-session re-sim
          scores on a fraction of its window until mid-January. Alpaca serves historical
          five-minute bars and `atp ingestion run-backfill` already writes them as a
          validated `raw_bars` version tagged `dataset_type = historical_backfill`; the
          rolling-window build just never used those. **Code (branch
          `feat/bench-backfill-window`):** `RollingRawBarsService.build` now merges every
          validated backfill version overlapping the window in as a base source before
          the per-session versions (live sessions win on an overlapping day; the version
          records `backfill_versions`; idempotency includes them). Nothing changes for the
          re-sim, publish, pull or B. Scores from the same job over the same data, just a
          full window. Not a substitute for the research sims' numbers: those come from a
          different dataset and cost model and must not be mixed into bench scores.
          *Operator step on A, after a close* (22 names = the rolling version's manifest;
          `--max-days` is 31, so three chunks; each takes a minute):
          ```bash
          SYMS=AAPL,ADBE,AMD,AMZN,AVGO,BKNG,COST,CRM,DIA,GOOGL,HD,IWM,MA,META,MSFT,NVDA,QQQ,SPY,TLT,TSLA,UNH,V
          docker compose exec app atp ingestion run-backfill --symbols "$SYMS" --start 2026-08-01 --end 2026-08-31 --actor "$USER"
          docker compose exec app atp ingestion run-backfill --symbols "$SYMS" --start 2026-09-01 --end 2026-09-30 --actor "$USER"
          docker compose exec app atp ingestion run-backfill --symbols "$SYMS" --start 2026-10-01 --end 2026-10-08 --actor "$USER"
          ```
          The next EOD `rolling_raw_bars` step picks them up (new sources, so a new rolling
          version), `publish_datasets` ships it, B's 20:30 ET pull registers it, and the
          re-sim window is 63 real sessions from then on. Verify: the rolling version's
          `metadata_json.backfill_versions` lists the three, and the dry run on B reports
          ~63 `daily_returns`. Note: a stray 3-symbol `active_daily_incremental` version
          dated 2024-06-03 (dev dump) already sets the rolling coverage start to 2024, so
          `resolve_bench_window` reports a full window today that is mostly empty; delete
          that version row (and directory) when running the backfill, or the coverage
          dates stay misleading.
          **Done 2026-11-06 08:11-08:14 UTC (PR #169 deployed 08:08):** three
          `market_backfill_cycle` runs `completed` (70 s, 69 s, 18 s), versions
          `raw_bars_20261106T081118Z_094d195a` (08-01..08-31),
          `..T081235Z_17b465d4` (09-01..09-30), `..T081350Z_69256cdb` (10-01..10-08),
          each `validated`, `dataset_type = historical_backfill`, 22 symbols / 22 Parquet
          files, 8.6 MB in all (disk 61% used). The stray 2024-06-03 version
          (`raw_bars_20260724T204756Z_12e641bc`) has no directory on disk, only its row,
          referenced by one `ingestion_checkpoints` and two `ingestion_runs` rows (all
          dev-dump, 2024-06-03) and nothing else. Deleted by the operator on A on
          2026-11-07 00:45 UTC (one transaction: the checkpoint row, the two run rows, the
          version row). It was **not** cosmetic, see Finding 26.
    - [x] **Finding 26: the rolling build silently dropped the whole backfill (2026-11-06).**
          Friday's EOD `rolling_raw_bars` listed the three backfill versions under
          `backfill_versions` yet wrote 23,658 rows, all Oct 9 to Nov 6, no August or
          September. The stray 2024-06-03 version carries the daily lifecycle
          (`active_daily_incremental`), so `daily_versions` returned it as the oldest live
          session and `_merge` capped every backfill at `first_live - 1 day` = 2024-06-02,
          i.e. nothing. The earlier note above that the row was "not blocking" was wrong.
          **Done 2026-11-07 00:52 UTC:** after the delete, a one-off `RollingRawBarsService.build`
          (same call as the EOD step, `as_of` 2026-11-06, 63 sessions) created
          `raw_bars_20261107T005243Z_8097b17d`: 83,988 rows (Aug 26,526 / Sep 27,049 /
          Oct 23,448 / Nov 6,965), coverage 2026-08-01..2026-11-06, 22 symbols, 88 files,
          the three backfills under `backfill_versions`; `atp datasets publish
          --trading-date 2026-11-06` uploaded it (89 files, 11.1 MB) into the 2026-11-06
          index before B's 20:30 ET pull. **Code fix still to do:** `daily_versions` must
          ignore versions whose `date_coverage_end` is older than the window start (the way
          `backfill_versions` already does), so one stale daily row can never zero the
          backfill again; add a test with a 2024 daily row in the way. **Verify Sat
          morning:** B's Fri 20:30 ET pull registered `raw_bars_20261107T005243Z_8097b17d`;
          the 06:00 ET bench re-sim (or the dry run, step above) reports ~63 `daily_returns`.
    - [x] **Finding 15: the `rolling_raw_bars` step failed every EOD after deploy (10/27-10/30).**
          `checksums.dataset_version` is a foreign key to `dataset_versions` on Postgres;
          the service committed the checksum rows before it saved the version row. The
          SQLite test suite does not enforce the constraint, so four tests passed. Three
          retries a night left 12 unregistered version directories on A, which
          `publish_datasets` shipped to S3 (it uploads every local version directory) and B
          pulled, unregistered. Fixed in PR #161 (2026-10-30, deployed to A and B): version
          row first, then checksum rows; any failure after the Parquet write removes the
          directory; the foreign key is emulated in the tests. Orphans deleted from A, S3
          and B the same night. First real run Mon 2026-11-02 EOD was clean: `rolling_raw_bars` `completed` in 19.7 s, version row plus 44 checksum rows, published to S3 and registered by B's 20:30 ET pull.
    - [x] **Finding 16: the first month-end universe rotation on A (Fri 10/30) proposed an
          empty universe.** The candidate scorer reads bars from the box's own store, and A
          only ingests five-minute bars for the names it trades: 478 of the ~500 pool names
          were rejected `no_market_data`, and the 22 traded names `insufficient_history`
          because the live hook hands the scorer that session's one-day dataset version
          (Finding 14's shape again). The pre-activation checks refused the empty proposal,
          so the dev-dump universe stayed active; the chain was not blocked. Backtests never
          hit this because the replay dataset holds bars for every pool name.
          **Fix (branch `fix/universe-rotation-market-data`):** the scorer takes a
          `CandidateBarSource`; `AlpacaDailyBarSource` fetches daily bars for the whole pool
          per run (the same data the pool-refresh screener already ranks on; three batched
          requests; a failed batch is retried once, then the step fails rather than scoring
          partial data). The scheduler's step passes `candidate_market_data="alpaca_daily"`;
          replays keep their pinned Parquet version. With daily bars, one bar is a complete
          session, so bar completeness is 1.0 rather than a 78-bar penalty. The candidate
          version's `generation_metadata.market_data_source` records which path scored it,
          and the step's summary now lists `added` / `removed` and rejections by reason.
          **Verify before Mon 11/30 (the month's last session; 11/28 is a Saturday):** `atp universe monthly-rotation --dry-run` on A runs the
          exact scheduler path (pool refresh and candidate scoring persist their audit rows;
          the rotation itself is rolled back), and prints the proposed adds/drops; expect
          ~500 pool names, `rejections_by_reason` without `no_market_data` in bulk, and
          churn at or under the 30% guard. `--enforce` activates it like the month-end step.
          Not reproducible from disk (neither is the pool refresh); if that matters later,
          have the refresh store the daily bars it already downloads and point the scorer at
          them.
          **First dry run on A (2026-10-31 00:52 UTC, after PR #162 deployed):** the scorer
          worked (pool 500, 493 scored, 6 `insufficient_liquidity`, 1 `no_market_data`) and
          exposed two more things, fixed on the same branch for the follow-up PR:
          - *The pool refresh had failed, so it scored the backtest's 2024-12-02 pool.*
            Alpaca now lists assets on TXSE, which the installed SDK's exchange enum does
            not know (nor does 0.44.0), and one unknown code fails validation of the whole
            asset list. The screener now asks the trading client for raw dicts
            (`raw_data=True`) and filters on exchange itself, so unknown exchanges are
            skipped rather than fatal. The 10/30 scheduled run hit the same error (it is
            only a warning in the step summary).
          - *The churn rule shrank the universe.* 18 active names, budget int(0.3 x 18) =
            5; five actives ranked 38-116 (past the retain-until-30 threshold) were
            removed worst-first and used the whole budget, so no name was added: 18 -> 13.
            Decision (user, 2026-10-31): optional removes are taken only as swaps with the
            best eligible add (2 budget units), mandatory removes (gone from the pool or
            excluded) are paid first, and any budget left fills toward the 20-name target.
            The universe never shrinks on rank alone. On the same data: 2 swaps + 1 fill,
            18 -> 19, churn 5/18. Also decided: ETFs stay eligible for now (11 of the top
            20 candidates are ETFs because the volatility penalty favours bond and sector
            funds; the strategies already trade SPY/QQQ/TLT); revisit the scoring weights
            or an equity-only pool with backtest evidence, not mid-soak.
          Re-run `atp universe monthly-rotation --dry-run` after the follow-up deploys:
          expect no `screener_refresh_failed` warning, a pool snapshot captured today,
          `added` non-empty and `symbol_count` 19 or 20.
          **Done 2026-11-03 01:50 UTC (PR #163 on A since 10/31 01:12 UTC):** `status: ok`, no
          warnings, pool snapshot captured at 01:49:54 UTC (500 names, all 500 scored, no
          rejections), 18 → 19: added HYG, LQD, XLF; removed CRM, DIA; churn 27.8% (under the
          30% guard); `market_data_source: bar_source:alpaca_daily`; rotation rolled back.
          Nothing left to verify before the Mon 11/30 close.
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
- [ ] Only after A has a clean paper record with all Phase 3 automation on: **four
      consecutive weeks with every row of the readiness scorecard (section 7) passing, all
      automation on, zero open P0 or P1**
- [ ] Add a push channel for kill switch + the heartbeat conditions from 1.4
- [ ] **Feed switch (agreed 2026-11-08):** the day a go-live date is set, schedule the
      SIP switch weekend at least six weeks before it. One feed everywhere, switched
      together (live ingestion, universe screener, backfill re-pull, re-baseline); details
      and the reasoning in `research-generation-plan.md` D1. Until then everything stays
      IEX, and the research sprint's Step 0 proceeds regardless.

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
- [ ] Verify free-plan SIP historical access (affects backtest realism): the check itself
      is scoped in `research-generation-plan.md` D1 (one-symbol two-day backfill with
      `ALPACA_DATA_FEED=sip` on that command only, then delete the test version); the
      switch is gated on the Phase 5 trigger above
- [ ] `atp runtime run-eod-step <step> --as-of DATE [--dry-run]` (section 7, rehearsal
      rule): run any EOD chain step through the same orchestrator with the calendar gate
      bypassed; `--dry-run` mandatory outside the schedule for `monthly_universe_rotation`
      and for `weekly_portfolio_review` in `auto` mode. Next small PR after Finding 26.
- [ ] Exact API contract for worker → A publishing (design doc once 5c lands)
- [ ] Settlement/T+1: does the live ledger need a daily pass, or is it simulation only?
- [x] Confirm risk/sleeve snapshots run inside the trading cycle or need their own schedule: they run inside every trading cycle, with order reconciliation and portfolio drawdown governance; no separate schedule (see `pre-launch-1.3-scheduling.md` §1)

## 7. Launch readiness scorecard and operating rules (agreed 2026-11-08)

Why: "the soak is working" so far meant the trading loop, the ledger and the EOD chain.
The bench, the portfolio review, the universe rotation, the worker's schedule and the
research job are layers of the same system and were not being scored. One weekly table,
one pass rule per row, one sentence for the go-live gate.

### 7.1 Weekly scorecard

Filled in every weekend from A's `runtime_job_runs`, `audit_logs`, the validator, B's
Loki stream on A, and the worker submissions table. A row is **n/a** until its automation
is switched on; n/a rows do not count against the gate but the gate cannot be met while
any row is n/a.

| Layer | Check | Pass |
|---|---|---|
| Trading loop | ticks completed per session; `RUN_FAILED`; `ORDER_REJECTED_BY_BROKER` | ≥ 99%; 0; 0 |
| Ledger | `validate-broker-consistency` after the first tick of the week; EOD `broker_reconciliation` drift explained only by fills after the last tick | `ok`; yes |
| Data | `SLA_BREACH` per session vs the feed's baseline (IEX baseline to be set from Nov 2026 sessions); EOD chain complete | at or below baseline; by 18:30 ET every session |
| Ops | nightly backup present; B's 20:30 ET pull registered the day's versions; disk | yes; yes; < 75% |
| Bench | Saturday `bench_resims` accepted on A (`worker_result_submissions.status`), ~63 `daily_returns` per strategy, `market_returns` present | yes, every week |
| Portfolio | Monday `weekly_portfolio_review` ran, `worker_run_id` points at that re-sim, every decision has a reason | yes, every week |
| Universe | `monthly_universe_rotation` ran on the last trading day, pool snapshot captured, churn under the 30% guard, no `screener_refresh_failed` | yes, monthly |
| Research | monthly research job posted candidates; A admitted or rejected each with a recorded reason | yes, once Phase 4 exists |
| Alerts | Grafana alert rules firing only on real conditions; no alert unacknowledged > 24 h; no heartbeat gap | yes |

### 7.2 Bug classes

| Class | Definition | Rule |
|---|---|---|
| P0 | Money or ledger wrong (cash/positions off vs broker beyond pending fills), or an order the system did not intend (wrong symbol, side, size, or outside the kill switch) | Blocks go-live. Fix, then one full clean week of soak under the fix before the gate clock resumes. |
| P1 | A scheduled process failed, skipped, or ran with wrong inputs (a Finding 14/26-class problem) | Fix **and rehearse** (7.3) within the week. The row stays failed for that week. |
| P2 | A logged finding with no behaviour change (cosmetic, docs, a metric mislabelled) | Record in the plan; no effect on the gate. |

### 7.3 Rehearsal rule for scheduled processes

Do not wait a month to learn whether a monthly step works. Every scheduled step gets an
on-demand invocation that runs **the same step function** with an injected trading date
(`atp runtime run-eod-step <step> --as-of DATE [--dry-run]`, section 6; the rotation
already has `atp universe monthly-rotation --dry-run`, the worker has
`bench-resim --as-of --no-publish`). After a failure or a fix:

1. Re-run the step for the missed date in `--dry-run`, compare the output with what the
   schedule would have produced, and record "rehearsed <date>" against the step here.
2. Only then let the next scheduled firing be the proof of **timing**, not of logic. The
   first scheduled firing of any step is still watched.
3. `--dry-run` is mandatory outside the schedule for steps that apply state
   (`monthly_universe_rotation`; `weekly_portfolio_review` in `auto` mode). In `advisory`
   mode the review is already a dry run (decisions recorded, nothing applied), so it can
   run off-schedule on any weekday.
4. A rehearsal is a deploy requirement for any **new** scheduled step: no timer or
   `applies` gate goes live without one recorded dry run on A or B.

### 7.4 Go-live gate (one sentence)

Four consecutive weeks with every scorecard row passing, every Phase 3 automation on, and
zero open P0 or P1; the SIP switch weekend done at least six weeks before the date.

### 7.5 What can be done now (Sun 2026-11-08 onward), in order

1. Merge the four open branches (Finding 26 fix; this docs branch; the research plan with
   Step 0; nothing else pending).
2. `run-eod-step` command (section 6), then rehearse `weekly_portfolio_review` in advisory
   mode for Mon 11/10 before it fires, and `rolling_raw_bars` for 11/06 against the fixed
   selector.
3. Box B: `worker bench-resim --as-of 2026-11-06 --no-publish` dry run on the full window
   (expect ~63 `daily_returns`), then `sudo infra/ops/install_worker_timers.sh` so the
   Saturday timer exists; this is the open 3.3(a) step.
4. Research sprint Step 0: R1 (Monte Carlo bootstrap) first; D1's free-tier probe.
5. Set the IEX `SLA_BREACH` baseline from the November sessions so the Data row has a
   number.

### 7.6 Rough calendar (as of Sun 2026-11-08; adjust as we go, this is for visibility not commitment)

Assumptions: deployment track and research sprint run in parallel; one person-week of
attention per week; the four-week gate clock starts only when every scorecard row is live;
the SIP switch precedes go-live by at least six weeks and the clean weeks that count are
the ones after it. Holidays (11/26, 12/24, 12/25, 1/1) thin out both markets and attention.

| Week of | Deployment track | Research sprint | Calendar events |
|---|---|---|---|
| Mon 11/10 | Merge open branches; `run-eod-step` PR; rehearse `weekly_portfolio_review` (advisory) before Mon's chain; B dry run + install Saturday timer; flip `worker_results_enabled` | R1 Monte Carlo bootstrap; D1 free-tier probe | Sat 11/15 first timed re-sim should post to A |
| Mon 11/17 | Mon review runs with a `worker_run_id` for the first time; first full scorecard fill (weekend); set IEX `SLA_BREACH` baseline | R2 walk-forward range guard + fold evidence; R6 config validator | |
| Mon 11/24 | `portfolio_review_mode=advisory` (decisions visible, nothing applied); rehearse `monthly_universe_rotation --dry-run` | R3 checkpoint wiring; R4 gate audits | Thu 11/26 closed, Fri 11/27 early close (calendar tests); Mon 11/30 rotation fires |
| Mon 12/1 | `portfolio_review_mode=auto`: Phase 3.3(a) complete; all scorecard rows except Research live | Step 1 baseline on IEX (`feed=iex`); R5 score, R7 tests | |
| Mon 12/8 | Step 3 candidate hand-off endpoint on A (additive) | Step 2 evolutionary search; rerun Step 1 with it | Decision point: set a go-live window → schedule SIP switch weekend |
| Mon 12/15 | 3.4 recorded-dataset source; Step 5 worker research job + timer, rehearsed `--no-publish` | Step 0 follow-ups (cache/identity, observability, worker memory budget) | Earliest SIP switch weekend 12/13-14 if the window was set on 12/8 |
| Mon 12/22 | Holiday week: watch, do not change | Suite `v1` window definitions (docs only) | Thu 12/24 early close; Thu 12/31 rotation |
| Mon 1/5/2027 | First scheduled monthly research run (Sat 1/2 or 1/9); Research row goes live; gate clock running | Results log compares grid / evolutionary / `feed=sip` | |
| Jan–Feb 2027 | Four clean weeks on SIP with everything on → gate met earliest **late Jan**; go-live no earlier than six weeks after the switch → **realistically February 2027** | Monthly run #2; generator iteration on the results | |

If the sprint slips, the deployment track does not wait for it, but the go-live gate does
(Research row). If the deployment track slips (a P0, a failed rehearsal), the gate clock
restarts and everything below it shifts a week per incident. Re-date this table whenever a
row moves; keep the old rows struck through rather than deleted so the drift is visible.
