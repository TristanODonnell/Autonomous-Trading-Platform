# Pre-launch 1.3: scheduling in the soak-loop runner

_Drafted 2026-10-05; decisions taken with the user the same day (§4). Implementation in progress. Parent: `pre-launch-deployment-plan.md` §4, 1.3._

## 1. What the survey found

**The end-of-day order in the parent plan (§3) is not the backtester's order.** The parent plan says
to use the backtester's hook order, so this doc follows the backtester.

| | Order |
|---|---|
| Parent plan §3 | health monitor/lifecycle → drawdown ladder + portfolio drawdown → correlation/risk budgeting → promotion/demotion → rebalance |
| Backtester (`platform_backtest_service.py` tick loop) | risk snapshot → drawdown ladder → advisory risk budget → live + on-deck shadow metrics → auto-promotion → auto-demotion → health lifecycle → portfolio snapshot → [monthly research] → [weekly bench review + portfolio review] → operations health |

**Four jobs in the parent plan's end-of-day row have never run in a backtest:**

| Job | Where it lives today | Changes the portfolio? |
|---|---|---|
| Allocation rebalance (`run_allocation_rebalance_cycle`) | Manual trigger only. In backtests, weights come from the trading cycle's `ActivePortfolioService` and the weekly portfolio review | Yes |
| Health monitor (`run_strategy_health_monitor_cycle`) | Manual trigger only (the *lifecycle* does run in backtests) | Depends on mode (observe/alert/enforce) |
| Correlation monitoring | Manual trigger only | No (observability) |
| Broker reconciliation (`ExternalBrokerReconciliationService`) | CLI only; read-only comparison against the broker | No |

**Already covered, no new schedule needed:**
- Order reconciliation, sleeve snapshots, risk snapshot and portfolio drawdown governance all run
  inside every trading cycle. This closes the parent plan's §6 question on risk/sleeve snapshots.
- On-deck shadow marking is part of the live-metrics refresh (`refresh_monitored`).
- The market calendar already gets holidays and half days from `exchange_calendars`
  (`RealMarketCalendar`; its docstring saying early closes are unhandled is stale).

**Two bugs in the current runner that matter unattended:**
- If end-of-day maintenance raises, the loop calls it again immediately, forever.
- "End of day done" is held in memory, so any restart after 18:00 ET re-runs the whole chain.

**The weekly bench review needs re-simulations**, which the parent plan assigns to the worker
(Phase 3). Box A has no source of scorecards until then.

## 2. Proposed design

**One chain, one start time.** The whole end-of-day chain starts at 18:00 ET on trading days (today's
trigger), not 16:15. Alpaca corporate-action data is the reason for 18:00 (step 5d, D4), and a final
ingestion at 18:00 picks up the closing bars in the same pass.

**Step runner.** A small `EodChainRunner` runs named steps in order. For each step it:
- writes a `runtime_job_runs` row (child of one parent row per trading date) and a
  `runtime_job_run_steps` row;
- retries retryable failures with backoff, up to a per-step cap;
- checks the shutdown flag before starting the next step;
- on restart, reads today's rows and resumes after the last completed step.

A step is either **blocking** (later steps are skipped if it fails) or **independent** (failure is
recorded and the chain continues). After the cap is reached the chain stops for the day and is
visible as failed; it does not spin.

**Chain (launch):**

| # | Step | Kind | Source |
|---|---|---|---|
| 1 | Final ingestion | independent (the chain can still run on the intraday dataset) | `run_market_ingestion_cycle` |
| 1b | Resolve the day's raw-bars dataset | blocking | existing lookup |
| 2 | Broker reconciliation (report only) | independent | `ExternalBrokerReconciliationService` |
| 3 | Corporate actions | blocking | existing |
| 4 | Features | blocking | existing |
| 5 | Risk: snapshot → drawdown ladder → advisory risk budget | independent | same code as `run_risk_at_timestamp` |
| 6 | Governance: live + shadow metrics → promotion → demotion → health lifecycle | independent | same code as `run_governance_at_timestamp` |
| 7 | Correlation monitoring (observability only) | independent | `run_correlation_monitoring_cycle` |
| 8 | Allocation rebalance (`run_allocation_rebalance_cycle`), gated by `auto_rebalance_enabled`; `min_rebalance_interval_hours=168` so it re-weights weekly | independent | existing |
| 9 | Operations health snapshot | independent | same code as the replay hook |
| 10 | Weekly (first trading day of the week): portfolio review, per `portfolio_review_mode` (off until Phase 3) | independent | `PortfolioReviewService` |
| 11 | Monthly (close of the month's last session): raw pool refresh → candidates → rotation, churn guard on | independent | same code as `run_universe_at_timestamp` |
| 12 | Publish Parquet to S3 | independent | new |

Steps 5, 6, 9 and 11 call the same functions the replay hooks call, so live and backtest cannot drift.
`dataset_version_id` from step 1 is passed to steps 3, 4 and 12.

**S3 publish.** Upload new immutable dataset-version directories under `data/` plus a small manifest
(dataset name, version id, coverage, checksum) per trading date, so the worker can pin exact versions.
Uses `boto3` with the instance role; nothing is deleted from S3.

**Calendar.** No refresh job. Add a daily cross-check of the next 10 sessions against Alpaca's
calendar API that logs and counts a mismatch; `exchange_calendars` stays the source of truth.

**Retention (host timer, weekly).** Prune Docker images and build cache, container logs, local
backup dumps and `artifacts/` older than a set age. Deleting Parquet versions is left out until the
S3 publish has run cleanly for a while.

## 3. Sub-steps and gates

Each sub-step ends with: new unit tests, `pre-commit`, and the full backend suite.

| Sub-step | Content | Extra gate |
|---|---|---|
| A ✅ 2026-10-05 | Step runner: per-step rows, retry/backoff, stop between steps, resume after restart; fixes both runner bugs | Tests for retry cap, resume, SIGTERM between steps (10 runner tests + 2 soak-loop tests); full suite 5056 passed |
| B ✅ 2026-10-05 | Chain steps 1–9 wired into `run_eod_maintenance` | Golden-path test runs the full 10-step chain on SQLite with a fake broker: every step completes, reconciliation passes 5 checks, governance evaluates the seeded strategies, rebalance reports `skipped: auto_rebalance_disabled`. Full suite 5056 passed. The backtest-parity run was not needed: no file under `platform_replay/` or the backtest service changed (the chain imports the hooks as they are). The in-container end-of-day run is deferred to the soak: it needs a real day's dataset and would write to the dev data directory |
| C ✅ 2026-10-05 | Weekly and monthly steps (10, 11) | Calendar tests for the week's first session (Labor Day week) and the month's last session (weekend month-ends); weekly step verified a no-op with the review off; full suite 5068 passed. The monthly step runs at the close of the month's **last** session (the backtester rotates at the start of the first), with the churn guard on; `run_universe_at_timestamp` gained `force_rotation`/`rotation_reason` parameters whose defaults keep replay behaviour |
| D | S3 publish (12) | Run against a real bucket from the dev machine |
| E | Calendar cross-check; retention timer | Dry-run output of the retention script reviewed |

## 4. Decisions (taken 2026-10-05)

1. **Rebalance.** `QualityBasedReallocationService.rebalance()` is one sizing engine with two triggers:
   the weekly portfolio review (which calls it) and the nightly `run_allocation_rebalance_cycle`.
   The review is off until Phase 3, so the nightly step runs as the interim trigger with
   `min_rebalance_interval_hours=168`, which keeps the agreed weekly re-weight cadence. Both
   triggers share the interval guard and the `auto_rebalance_enabled` switch.
2. **Monitoring.** Correlation monitoring is added (observability only). The older
   `StrategyHealthMonitor` is left out: it has never run in a backtest, and daily protection is
   already covered by the health lifecycle, the drawdown ladder and portfolio drawdown governance.
3. **Weekly review.** Wired now, `portfolio_review_mode` stays off until the worker supplies
   scorecards in Phase 3.
4. **S3.** Bucket, region and credential model still to be supplied before sub-step D.
5. **Retention.** 14 days for local dumps and container logs, 30 days for `artifacts/`, Docker
   image/build-cache prune weekly. Parquet versions are not deleted in this phase.

## 5. Box A settings for the chain

Operator settings live in A's database, not in files, so these are set once after the first
deploy (`atp settings ...` or the settings API):

- `min_rebalance_interval_hours = 168` and `auto_rebalance_enabled = true` (decision 1): weekly
  re-weight through the nightly step until the review is on.
- `portfolio_review_mode` stays `off` until Phase 3 (decision 3).
