# Portfolio Rotation — Step 5: Rotation Backtest & Tuning

Status: done (2026-09-30) — see "Outcome" at the end
Parent plan: `docs/roadmaps/portfolio-rotation-plan.md` (§5 Step 5). Builds on Steps 1–4.
Step 6 (Airflow) is out of scope.

## Goal

Run six months of history with Steps 1–4 active, check that the system picks, rotates and
prunes sensibly, and choose the review's numbers (swap guardrails, score floor, set size)
by measuring the resulting portfolios instead of guessing. Winners become the new
defaults only if they beat a no-rotation baseline in a full platform backtest.

## Current state (verified in code)

| Area | Today | Relevance |
|---|---|---|
| Backtest artifact | Final value, P&L %, equity-curve point count, per-tick domain summaries | **No portfolio-level scorecard**: no Sharpe, max drawdown, turnover, swap counts, per-strategy contribution or baseline comparison |
| Run cost | ~30–40 s per trading day with research on (Step 4E: 11 weeks ≈ 35–40 min) | 6 months ≈ 1.5–2 h per full run → a sweep of full runs is impractical |
| Review decisions | `portfolio_review_decisions.decide()` is pure; scorecard maths (`build_scorecards`, `evidence_weights`) is pure | Can be driven offline |
| Evidence | Forward (live / shadow sleeves), re-sim (`BenchResimulationService`, any window), approval backtest score | One full-period re-sim per strategy gives the daily returns an offline simulator needs |
| Correlation | Raw daily-return correlation (bench grouping + scorecard lens) | Long-only strategies all look correlated through market beta (Step 3 caveat) |
| Defaults | Review settings in the model, repository default row and migration `ww11xx22yy33` | A new migration is needed to change server defaults |

## Decisions (agreed with the user 2026-09-29)

1. **Hybrid approach:** record once, sweep offline, confirm with full backtests.
2. **Scope:** 2024-01-02 → 2024-06-28, ~10 liquid symbols, 3 seeded approved strategies +
   monthly research feeding the bench; seats 3–5, on-deck 4, bench 10.
3. **Objective:** maximise portfolio Sharpe subject to max drawdown ≤ 15 % and ≤ 1 swap
   per month on average; report return, drawdown and turnover alongside. A config must
   beat the no-rotation baseline to justify rotation.
4. **Knobs:** swap guardrails (margin, streak, tenure, swap interval) and score floor /
   set size. Correlation switches to **market-excess** returns. Other scorecard weights
   and bench settings stay as they are.
5. **Result:** winners become the code/migration defaults (only if they beat the baseline
   in the confirmation backtest); the full sweep is recorded here.

Technical calls (flag if you disagree):
- **Offline simulator recomputes scorecards** from each strategy's full-period daily
  returns rather than replaying the recorded scorecards: a different config rotates
  differently, which changes each strategy's evidence (shadow vs live, stint lengths).
  Forward evidence = the strategy's own returns since it entered its current tier (same
  fill model for shadow and real, as in Step 2), re-sim evidence = trailing 63 days,
  backtest = stored approval score, fading as in Step 4.
- **Pool timeline is taken from the recording run** (when research added each candidate,
  when the bench admitted / retired it). Bench settings are not tuned, so this holds for
  every config.
- **Market-excess correlation** = correlation of residuals after regressing each
  strategy's daily returns on SPY's (beta removed). Applies to both the scorecard lens
  and bench grouping (same helper), since the Step 3 over-grouping is the same problem.
- **No-rotation baseline** = `portfolio_review_mode: off` on the same fixture (the
  placeholder keeps incumbents and only fills vacancies).
- **Simulator validation:** before trusting the sweep, run the simulator with the current
  defaults over the recording run and compare its decisions and portfolio return with the
  real auto backtest on the same fixture; large gaps are investigated first.

## Work breakdown and verification gates

### 5A — Rotation report
- `RotationReportService` + CLI `atp platform backtest rotation-report --run <artifact>`
  (and a `rotation` section in the backtest artifact): portfolio daily equity from sleeve
  snapshots/cash → total return, Sharpe, max drawdown, volatility; SPY buy-and-hold
  benchmark; swaps / seat changes / promotions / retirements / on-deck moves per month;
  turnover; per-strategy contribution and days per tier.
- **Gate:** unit tests on known equity/transition fixtures; report on the Step 4E auto
  artifact matches hand-checked numbers; full suite, pre-commit.

### 5B — Market-excess correlation
- Shared `excess_correlation(a, b, market)` helper; bench grouping and the scorecard lens
  use it (market series = SPY daily returns over the same window, from the dataset's bars).
- **Gate:** tests (two strategies that both track the market but differ otherwise are no
  longer "correlated"; genuine duplicates still are); bench + scorecard tests updated;
  full suite, pre-commit.

### 5C — Recording run and rotation dataset
- Fixture `portfolio_rotation_6m.yaml` (advisory mode, scope above).
- After the run, `atp platform backtest export-rotation-dataset`: per strategy a
  full-period re-sim (daily returns, trade counts per day), approval score, family; the
  pool timeline (research arrivals, bench admissions / retirements); SPY daily returns.
  Written as a versioned artifact next to the backtest output.
- **Gate:** run completes with 0 errors; dataset covers every strategy that was ever in the
  pool; spot-check two strategies' re-sim returns against their shadow sleeve returns.

### 5D — Offline rotation simulator and sweep
- `RotationSimulator` (pure): weekly review loop over the dataset — scorecards via
  `build_scorecards`, decisions via `decide()`, tier bookkeeping (wind-down approximated
  as one week), portfolio valued from the actives' daily returns at equal weight with
  turnover cost on swaps.
- Validation against the real auto backtest (technical call above).
- Sweep: margin {5, 10, 20 %} × streak {2, 3, 4} × tenure {14, 30, 60 d} × swap interval
  {14, 28 d} × floor {0.9, 1.0, 1.1} × seats {fixed 3, dynamic 3–5} ≈ 324 configs
  (seconds each). Rank by the objective; report the top 10 and the baseline.
- **Gate:** simulator tests (hand-built 3-strategy datasets: an obvious swap happens, churn
  guardrails bind, no-rotation baseline equals holding the initial set); validation gap
  explained; sweep table in this doc; full suite, pre-commit.

### 5E — Confirmation backtests and defaults
- Full backtests on `portfolio_rotation_6m.yaml` in auto mode with the top 1–2 configs,
  plus the baseline (mode off). ~2 h each.
- If a winner beats the baseline on the objective: new migration changing the server
  defaults, model/repository defaults updated; otherwise keep the defaults and record why.
- **Gate:** rotation reports for all runs; sleeves = account; 0 errors; Outcome section,
  master plan, memory.

## Progress notes

### 5A — Rotation report (done 2026-09-29)
- `RotationReportService` + `performance_metrics()` (contracts `rotation_report.py`),
  `platform_replay/rotation_hooks.py` (SPY buy-and-hold from the replay's own bars
  dataset), `rotation` section in every portfolio-mode backtest artifact, CLI
  `atp platform backtest rotation-report --artifact … [--rebuild --starting-cash …]`.
- Portfolio equity = starting cash + Σ each real sleeve's latest net P&L (carried forward
  after a strategy leaves). On the Step 4E auto run this matches cash + sleeve market
  value to the cent ($272,610.32); the artifact's older `portfolio.portfolio_value`
  ($275,411) marks positions at their last fill price (known Step 1 gap).
- Step 4E auto run, first report: portfolio +9.04 % (Sharpe 3.56, max DD 1.65 %) vs SPY
  +10.30 % (Sharpe 3.98, DD 1.70 %); 1 swap; **turnover 17.7× in 11 weeks**.
- Gate: 7 tests; suite 4839 passed (with 5B), pre-commit clean.

### 5B — Market-excess correlation (done 2026-09-29)
- `market_excess_returns()` (r − β·SPY, β on the shared days; raw returns when the
  market series is missing or too short) applied before bench grouping and the
  scorecard correlation lens; the bench hook loads SPY daily returns from the re-sim
  dataset and records `correlation_basis` in its summary.
- Gate: 6 tests (market trackers no longer grouped, genuine duplicates still are, raw
  fallback, bench review with/without market, scorecard lens); suite 4839 passed.

### 5C / 5D — Dataset export and simulator (built; awaiting the recording run)
- Recording run = **auto mode with the current defaults** (not advisory): the same run
  gives the dataset and the real path the simulator is validated against, saving a
  ~3.5 h run. Pool timeline (research arrivals, bench admissions / retirements) does not
  depend on the mode.
- `SimulationRunResult.trade_logs` (new optional field) so window trade metrics can be
  sliced from one run. `StrategyGovernanceService.would_pass_promotion()` runs the real
  promotion checks inside a rolled-back savepoint (no audit rows).
- `RotationDatasetService` + CLI `export-rotation-dataset`: one full-period re-sim per
  pool strategy (bar-level equity + fills; tagged `bench_resim_rotation_export`),
  availability, promotability, review dates, SPY returns, settings, recorded path.
- `RotationSimulator` + CLI `rotation-sweep`: re-sim evidence sliced from the bar-level
  curve with the research metric functions (5-min annualisation, as the bench re-sims),
  forward evidence from the trailing 20 calendar days in-stint (daily, √252, as the live
  metrics), shared weekly-streak helper `weekly_review_dates()` (also used by the review
  service now); equal-weight portfolio of the actives, turnover cost on seat changes;
  mode `off` baseline; 324-config grid; ranking by Sharpe under DD ≤ 15 % and
  ≤ 1 swap/month.
- Tests: 8 simulator + 3 export/governance-check tests.
- Run time: the 6-month, 10-symbol recording run takes ~1.7 min per trading day
  (~3.5 h), so each 5E confirmation run is ~3.5 h too.

### First recording run (2026-09-29) — discarded, found a safety-layer bug
- 0 errors; portfolio +5.9 % (Sharpe 1.29, DD 4.7 %) vs SPY +15.1 % (Sharpe 2.73);
  1 swap (Apr 15, research `momentum__a14e…` for `momentum_v1`), 2 add-seats (a research
  candidate promoted by `system_portfolio` on Feb 26), 2 drops; turnover 51× in 6 months.
  Artifact kept as `portfolio_rotation_6m_record_prefix.json`.
- **Bug (pre-existing, safety layer):** `momentum_v1` (swapped out Apr 15) and
  `macd_crossover_v1` (dropped Apr 22) never finished winding down: each held 125 JPM.
  On Apr 8 both had bought 125 JPM in the same cycle — each order checked against
  start-of-cycle holdings (the Step 1 known gap) — so the account held 300 JPM
  (≈ $59.6k) against `MAX_SYMBOL_EXPOSURE=25000`. `PreTradeRiskService` then rejected
  every JPM order, **including the risk-reducing sells**: it required the exposure
  *after* the sell ($34.3k) to be under the cap. JPM froze for 2.5 months (~$50k
  unmanaged). Reproduced against the final DB state.
- **Fixed (user decision 2026-09-29):**
  1. Safety: the per-symbol cap blocks an order only if it raises the symbol's exposure
     (same rule the portfolio-level symbol check already had). Tests: partial sell of an
     over-cap position passes; a sell that flips into an over-cap short is still blocked.
  2. Root cause: `portfolio_evaluation._cap_buys_to_symbol_limit` trims buys after all
     sleeves' intents are known so the account's combined exposure per symbol stays under
     the tightest cap (`PreTradeRiskService.symbol_exposure_cap_usd`); sells free room
     first. Closes the Step 1 "per-symbol caps not aggregated intra-cycle" gap. Tests on
     the helper and on a real portfolio cycle.
- Recording run repeated with both fixes.

### Second recording run (2026-09-29, with both fixes)
- 0 errors, ~2 h. Wind-downs now finish in 2 days. Portfolio −0.34 % (Sharpe 0.02, DD 9.1 %)
  vs SPY +15.1 % (Sharpe 2.73). 2 swaps: Apr 15 research `momentum__a14e…` in for
  `momentum_v1` (then lost $15.9k), May 13 `momentum_v1` back in for
  `mean_reversion__7932…`; 2 add-seats, 3 drops. The first run's +5.9 % was mostly the
  frozen JPM rallying.

### Simulator validation (found and fixed three fidelity gaps)
1. **Re-sims do not trade like the platform.** Full-period re-sims on intraday bars: `factor_based`
   and `macd` make 0 trades (on the platform they traded all run; `factor_based` +$15.4k),
   `momentum__a14e…` +18 % (−$15.9k on the platform). The re-sim/research cadence (5-min bars)
   vs the daily trading cycle is the known Step 3 gap. Forward evidence and portfolio value
   now come from each strategy's **forward record** (real sleeve while active, shadow sleeve
   while on-deck: daily return on allocated capital), exported per strategy; re-sims are used
   only for the re-sim evidence (as the bench does).
2. **Forward metrics mirrored exactly**: 20-day equity window, trade count / win rate over the
   last 50 closed trades, days live from the first record (as `LivePerformanceMetricsService`).
   Scorecard comparison at 4 reviews: forward scores match the real run to ~3 decimals, re-sim
   scores within ~0.1–0.2 (slice of one run vs fresh window).
3. **Weights**: the sim now re-weights actives like `QualityBasedReallocationService` (blended
   quality, water-fill to the cap, min change 2 %); bootstrap trims seeds to max_active.
- Result: the simulator reproduces the real run's review path **exactly (10/10 applied
  decisions)**; valuing the recorded path with the recorded weights gives −0.52 % vs the real
  −0.34 % (DD 9.24 % vs 9.09 %).

**Bug found (Step 4, fixed):** the review called `rebalance(actor="portfolio_review")`, so its
overrides looked manual (`overridden_by != auto_rebalance`) and no later rebalance ever changed
them — weights were set once (Jan 22) and frozen. Now called as `auto_rebalance` with
`trigger_source="portfolio_review"`; test asserts the actor.

### Sweep (324 configs, 6 min)
| Config | Return | Sharpe | Max DD | Swaps/mo |
|---|---|---|---|---|
| No rotation, 3 seats | +7.16 % | **2.62** | 2.34 % | 0 |
| Best rotation (3 seats, streak 4, tenure 60, interval 14; margin/floor irrelevant) | +9.53 % | 2.51 | 5.07 % | 0.17 |
| No rotation, 3–5 seats | +6.23 % | 1.74 | 2.37 % | 0 |
| Current defaults (3–5 seats) | −3.92 % | −0.39 | 12.87 % | 0.34 |

- Mean Sharpe by knob: fixed 3 seats 0.47 vs dynamic 3–5 −0.30; tenure 60 d 0.74 vs 14/30 d
  −0.20/−0.28; interval 14 d 0.42 vs 28 d −0.25; margin and floor barely matter. 252 of 324
  configs are feasible (DD ≤ 15 %, ≤ 1 swap/month).
- **No rotation config beats the matching no-rotation baseline on the objective.** The best one
  rests on a single swap (Mar 4: `mean_reversion__7932…` in for `macd`). The pool is tiny
  (4 seeds + 2 research candidates) and the research candidates' re-sim evidence overstated
  them (cadence gap), so the sweep mostly says "rotate less".

## Outcome (2026-09-30)

Step 5 is closed with **conservative defaults, no tuned "winner"** (user decision): the
sweep found no rotation config that beats the matching no-rotation baseline on the
objective, and every trend pointed at rotating less.

- **Defaults changed** (migration `xx22yy33zz44`, server defaults only; existing settings
  rows keep their values): `review_swap_consecutive` 3 → **4**, `review_min_tenure_days`
  30 → **60**. Seats stay dynamic (3–6); fixed 3 seats did better on this data and is
  recorded as a finding, not adopted.
- Simulator prediction for the new defaults on the 6-month fixture (seats 3–5): −2.89 %,
  Sharpe −0.30 (old defaults −3.92 %, −0.39; no rotation 3–5 seats +6.23 %, 1.74). The
  confirmation backtest was **skipped** (user decision): the simulator reproduced the real
  run's decisions 10/10, and the damaging Apr 15 swap happens under any guardrail setting
  because the candidate's scorecard really rated it higher.
- **Why rotation loses here, and the next step:** the rotation backtests ran the trading cycle
  once a day (backtest default `cadence_minutes: 390`), while research / bench re-sims evaluate
  every 5-minute bar — so re-sim evidence (up to half a newcomer's evidence weight) said little
  about how a strategy traded in *these backtests* (`momentum__a14e…`: +18 % re-sim, −$15.9k).
  **Correction (5b discovery, 2026-09-30):** production trades every 5 minutes too
  (`market_trading_dag`: `*/5 * * * 1-5`), so research matches production and the *daily
  backtests* were the unrepresentative part. Step 5b re-records the rotation scenario at
  production cadence and re-sweeps (`portfolio-rotation-step5b-production-cadence.md`).

### Verification
- Full suite after each gate: 4839 → 4850 → 4857 (+1 pyarrow flake that passed in isolation
  and on re-run) → 4863 → 4863 (final); pre-commit clean; migration `xx22yy33zz44`
  upgrade → downgrade → upgrade on dev Postgres.
- Two 6-month recording runs (the first discarded after it exposed the JPM freeze), 0 errors.

### Changes beyond the plan
- **Safety (user decision):** the per-symbol cap no longer blocks orders that reduce an
  over-cap position; **root cause:** buys are trimmed in-cycle so several sleeves buying one
  symbol keep the account under the cap (closes a Step 1 known gap).
- **Step 4 bug:** re-weight overrides were written as `portfolio_review` and then treated as
  manual, so weights never updated after the first rebalance; now written as `auto_rebalance`.
- `SimulationRunResult.trade_logs`, `StrategyGovernanceService.would_pass_promotion()`,
  `PreTradeRiskService.symbol_exposure_cap_usd()`, shared `weekly_review_dates()`.
- Checkpoint gotcha corrected in the master plan: the checkpoint is named after `--output`.

### Tools left for the next tuning round
`rotation-report` (and the artifact's `rotation` section), `export-rotation-dataset`,
`rotation-sweep` (324-config grid, simulator validated against a real run), fixture
`portfolio_rotation_6m.yaml`, dataset `artifacts/platform/backtests/portfolio_rotation_6m_dataset.json`.

### Known gaps / follow-ups
- Research / bench re-sim cadence vs daily trading (above) — next step.
- Tiny pool: 4 seeds + 2 research candidates over 6 months; results are directional only.
- Simulator: no health lifecycle, drawdown ladder, risk blocks or throttles; wind-down
  immediate; re-sim evidence is a slice of one continuous run (within ~0.1–0.2 of fresh
  windows); counterfactual periods use the other book's forward record.
- Research candidates are admitted and promoted with inflated evidence; consider down-weighting
  re-sim evidence (`BACKTEST_SHARE` / forward weight) once cadence is aligned.

## Out of scope
Scorecard weights beyond correlation, bench settings, regime-scored lens, Airflow (Step 6),
frontend, live trading.
