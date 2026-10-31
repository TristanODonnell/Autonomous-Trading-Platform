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

*Seed an initial active set (exercise the portfolio path during the soak):*
- [ ] Decide the members: proposal = the three paper-approved rows (`momentum_v1`,
      `mean_reversion_v1`, `macd_crossover_v1`) as ACTIVE, equal budgets, no on-deck yet
- [ ] Confirm `operator_settings.portfolio_mode_enabled` is on; keep
      `portfolio_review_mode=off` and `worker_results_enabled=false` until 3.3(a)
- [ ] Insert the `portfolio_memberships` rows + `portfolio_membership_transitions` audit
      (via the governance CLI/API if one exists, else a reviewed SQL script committed to
      `scripts/`); record who/why in the transition reason
- [ ] Next session's log shows one `trading_cycle.active_strategy_resolved` per member and
      `portfolio_signal_aggregator.summary` with three inputs; sleeve ledgers populate
- [ ] Nightly rebalance stops logging `STRATEGY_ALLOCATION_REBALANCE_NOOP` once quality
      scores exist (weekly interval guard still applies)
- [ ] Note in this doc which path the soak days before the seed exercised (single-strategy)

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
| Fri 10/30 close | `monthly_universe_rotation` | `is_last_trading_day_of_month`, churn guard on, reason `scheduler_monthly` | never run on A (active universe is still the dev dump `proposed_2024-12-02`, 22 names) | [x] ran, step `failed`: empty proposal (Finding 16, 3.3a); chain continued, universe unchanged. Re-verify with `atp universe monthly-rotation --dry-run` after the fix deploys, then Mon 11/30 close for real |
| Sun 11/1 | daylight saving ends | all times via `America/New_York` zoneinfo | first clock change during the soak | [ ] Mon 11/2 ticks land at boundary + 10 s from 09:30 ET; EOD chain starts after 16:00 ET, not 15:00 or 17:00 |
| Mon 11/2 | weekly review on the first session after a rotation | | | [ ] `completed`; review sees the new universe, no error |
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
    - [x] **Finding 15: the `rolling_raw_bars` step failed every EOD after deploy (10/27-10/30).**
          `checksums.dataset_version` is a foreign key to `dataset_versions` on Postgres;
          the service committed the checksum rows before it saved the version row. The
          SQLite test suite does not enforce the constraint, so four tests passed. Three
          retries a night left 12 unregistered version directories on A, which
          `publish_datasets` shipped to S3 (it uploads every local version directory) and B
          pulled, unregistered. Fixed in PR #161 (2026-10-30, deployed to A and B): version
          row first, then checksum rows; any failure after the Parquet write removes the
          directory; the foreign key is emulated in the tests. Orphans deleted from A, S3
          and B the same night. First real run: Mon 2026-11-02 EOD.
    - [ ] **Finding 16: the first month-end universe rotation on A (Fri 10/30) proposed an
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
