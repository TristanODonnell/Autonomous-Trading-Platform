# Portfolio Rotation — Step 5b: Rotation Backtest at Production Cadence

Status: done (2026-10-01) — see "Outcome" at the end
Parent plan: `docs/roadmaps/portfolio-rotation-plan.md`. Follows Step 5
(`portfolio-rotation-step5-rotation-backtest.md`).

## Why

Step 5 found that research candidates looked good in re-sims and lost money in the rotation
backtests (`momentum__a14e…`: +18 % re-sim, −$15.9k). Discovery showed the cause:

| | Cadence |
|---|---|
| Production trading (`market_trading_dag`) | every 5 minutes, 5-minute bars |
| Research / bench re-sims | every 5-minute bar |
| Steps 1–5 rotation backtests | **once a day** at the close (`cadence_minutes: 390`, the default) |

So research matches production; the daily backtests did not. Every rotation result so far
(Steps 1–5) was measured at a cadence production never runs.

## Decision (user, 2026-09-30)

Backtest rotation at **production cadence** (`cadence_minutes: 5`); keep research and
production as they are. Reuse the Step 5 tools unchanged (export, simulator, sweep).

## Measured cost (4-day probe, full Steps 1–4 stack, 10 symbols)

| Cadence | Time per trading day | 6-month run | Orders / day |
|---|---|---|---|
| Daily (Steps 1–5) | ~0.9 min | ~2 h | ~9 |
| 5-minute (production) | ~5 min | ~10 h | ~440 |

~440 orders a day on $250k is very high churn; on the live paper account
`MAX_ORDERS_PER_BAR=2` would throttle it hard (see master plan §7). Flagged for the user; not
changed here.

## Work breakdown

1. Fixture `portfolio_rotation_6m_intraday.yaml` = the Step 5 scenario with
   `cadence_minutes: 5` and the Step 5 defaults (streak 4, tenure 60 d). Recording run in
   auto mode (~10 h).
2. Export the rotation dataset; validate the simulator against this run (decisions, and
   valuation with recorded weights) as in Step 5.
3. Sweep (324 configs) against the no-rotation baselines (3–5 and 3 seats); apply the Step 5
   rule: a config becomes the default only if it beats the matching baseline on the objective
   (Sharpe with max DD ≤ 15 % and ≤ 1 swap/month).
4. Outcome, master plan, memory.

## Run length (user decision 2026-09-30)

The 6-month intraday run slowed to ~11 min per trading day (on-deck shadow trading and more
open positions as the pool grows; ~18 h+ for 6 months). The run is stopped after
**2024-04-30** (4 months; the database is committed per tick, so no restart). Why 4 months:

| | 3 months (Mar 28) | **4 months (Apr 30)** | 6 months (Jun 28) |
|---|---|---|---|
| Swap-capable monthly reviews (60-day tenure) | 1 (Mar 18) | 2 (+Apr 15) | 4 |
| Forward record of `momentum__a14e…` (the Step 5 failure) | ~20 days, no swap decision | ~40 days incl. its Apr 15 swap decision | ~80 days |
| Market | steady Q1 rally | + mid-April pullback | + May–June recovery |
| Simulator validation | ~10 reviews | ~15 reviews | ~26 reviews |

4 months is the cheapest length that tests the exact case that failed in Step 5. Analysis
(report, export, simulator validation, re-sim vs production-cadence comparison) is bounded at
2024-04-30. No re-tuning: defaults stay at streak 4 / tenure 60 d.

## Progress notes
- 2026-09-30: recording run started (`portfolio_rotation_6m_intraday.yaml`, cadence 5, defaults
  streak 4 / tenure 60). Pace slowed from ~5 to ~11 min per trading day as on-deck shadow
  trading and open positions grew; stopped after 2024-04-30 (user decision above). 86 trading
  days, 0 failed ticks, 0 tracebacks. A stopped run writes no artifact: report and dataset were
  built from the database with a stub artifact (`portfolio_rotation_4m_intraday_record.json`);
  report and export are now bounded by the end date (test added).

## Outcome (2026-10-01)

**Production cadence does not make re-sims match how strategies trade on the platform.** The
gap Step 5 blamed on cadence is a research-engine vs trading-cycle parity problem.

### Rotation report, 2024-01-02 → 2024-04-30 (5-minute cadence)
| | Return | Sharpe | Max DD |
|---|---|---|---|
| Portfolio | +1.89 % | 0.73 | 4.19 % |
| SPY buy-and-hold | +6.23 % | 1.63 | 5.37 % |

No swaps; `momentum__a14e…` entered through an open seat on Mar 25 and lost $2.4k; one drop;
two review promotions of research candidates. **Turnover ≈ 1,950× in 4 months** (~$5.7M traded
per day on $250k, ~23× the account daily).

### Re-sim vs traded, same dates (traded − re-sim)
| Strategy | Daily cadence (Step 5) | 5-min cadence (5b) | Daily-return correlation |
|---|---|---|---|
| `mean_reversion__7932…` | +0.6 % | −6.4 % | 0.51 / 0.57 |
| `mean_reversion_v1` | −6.4 % | −9.6 % | 0.28 / 0.20 |
| `momentum_v1` | −3.5 % | −2.8 % | 0.59 / 0.49 |
| `momentum__a14e…` | −4.5 % | +1.9 % | 0.43 / 0.50 |
| `factor_based`, `macd` | re-sim 0 trades | re-sim 0 trades | — |

- Matching the cadence leaves gaps of the same size and correlations of 0.2–0.6.
- **Engine parity bug:** in the research simulator `moving_average_crossover`
  (`macd_crossover_v1`, 10/30) makes **0 trades** even on a 546-bar window where `momentum`
  makes 247; on the platform the same strategy closed 684 trades. `factor_based__19ab…`: 0
  re-sim trades vs 9,088 closed on the platform. Re-sim evidence for these strategies is a
  flat 1.0, not a measurement.

### Simulator validation (5-minute run)
5/5 real applied decisions reproduced; one extra simulated swap (Apr 15, `macd` for
`mean_reversion_v1`): on Mar 25 the sim scored `macd` just over the 10 % margin vs
`factor_based` (real: just under), putting its streak one review ahead. Same ~0.1–0.2 re-sim
evidence difference as Step 5. Sweep (for reference, not used for tuning): no-rotation 3–5
seats +2.47 % / Sharpe 1.02; current defaults +1.37 % / 0.52.

### Decisions / no changes
- Defaults unchanged (streak 4, tenure 60 d); no re-tuning (user decision).
- Rotation backtests should use production cadence (`portfolio_rotation_6m_intraday.yaml`).

### Next sprint (blockers for rotation to add value)
1. **Research ↔ trading-cycle parity:** same strategy + same bars ⇒ same signals and trades.
   Start with `moving_average_crossover` and `factor_based` producing no re-sim trades, then
   explain the remaining return gaps (sizing / costs / capital base / fills). A parity test
   harness belongs in the suite.
2. **Order churn at production cadence:** ~440 orders and ~23× turnover per day;
   `MAX_ORDERS_PER_BAR=2` on the live paper account would throttle it.
3. **Intraday backtest speed:** ~5–11 min per trading day makes production-cadence backtests
   overnight jobs.
4. Then re-record and re-sweep with the Step 5 tools.
