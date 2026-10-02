# Portfolio Rotation — Step 5c: Research ↔ Trading-Cycle Parity

Status: in progress — plan approved 2026-10-01 (decisions below); 5c-A–G and I done (2026-10-02); 5c-H partly done; new finding F7 (splits) open
Parent plan: `docs/roadmaps/portfolio-rotation-plan.md`. Follows Step 5b
(`portfolio-rotation-step5b-production-cadence.md`, "Outcome" and "Next sprint").

## Goal

The same strategy on the same bars produces the same signals and the same trades in the
research simulator (`SimulationRunner` → `SimulationExecutionEngine`) and in the platform
trading cycle (`portfolio_evaluation.py` → `StrategyEvaluationService` →
`PortfolioConstructionService` → simulated broker). Until it does, research admission, bench
re-sims and the review's re-sim evidence measure a different strategy from the one that trades.

Secondary (to be prioritised by the user): order churn at production cadence, intraday
backtest speed, then re-record and re-sweep with the Step 5 tools.

## Discovery (2026-10-01)

Five differences, found in code and confirmed on the 5b data (dev DB still holds the
4-month intraday run; dataset `raw_bars_20260930T190635Z_413a0176`).

### F1 — Research hands every strategy 20 bars (root cause of the 0-trade cases)

Every research entry point builds its context with a hard-coded `lookback_bars=20`
(`research_hooks.py`, `bench_hooks.py`, `rotation_hooks.py`, `cli/commands/platform.py`
export). `StrategyContextBuilder.build_from_window` returns no context until 20 bars exist and
then passes exactly the last 20. The trading cycle passes each strategy its **registry
warmup** (`_instantiate_strategy` → `defn.warmup_bars_fn(params)`).

| Strategy | Needs (warmup) | Research gives | Result |
|---|---|---|---|
| `macd_crossover_v1` (MA 10/30) | 31 bars | 20 | crossover never computable → 0 trades |
| `factor_based__19ab…` (windows 100) | 100 bars | 20 | z-score / vol / volume never computable → 0 trades |
| `momentum_v1` (5), `momentum__a14e…` (1), `mean_reversion_v1` (20), `mean_reversion__7932…` (5) | ≤ 20 | 20 | trade; signals match once enough bars exist |

Probe (research runner, real 5-min bars, Jan 8–31 2024, 10 symbols):

| Strategy | lookback 20 (today) | lookback 101 |
|---|---|---|
| `macd_crossover_v1` | 0 fills | 432 fills, +3.0 % |
| `factor_based__19ab…` | 0 fills | 5,812 fills, +4.0 % |
| `momentum_v1` | 2,784 fills | 2,611 fills (first ~100 bars skipped: lookback gate ≠ loaded warmup) |

Consequences beyond these two strategies:
- **Research search space is silently capped**: any generated candidate needing more than 20
  bars (> 100 minutes of history) makes 0 trades and is filtered out. Research has only ever
  been able to admit short-window strategies.
- The built-in indicators are trailing-window (path-independent), so "enough bars" is
  sufficient for them; but composite components `exponential_moving_average` and `rsi_wilder`
  are **path-dependent** — their value depends on how many bars are passed. Parity therefore
  needs the **same bar count** on both sides, not just "at least warmup".

### F2 — Backtest broker fills every intraday order at the day's closing price

`SimulatedBrokerClient._fetch_bar_from_parquet` reads all bars for the tick's **date** and
takes the last row (`idx = num_rows - 1`). Backtest ingestion writes the whole day before the
first tick, so at `cadence_minutes: 5` every fill, every sizing price (`get_latest_trades`) and
every on-deck shadow fill (`bar_for`) uses the day's last bar.

Evidence: all AAPL fills on 2024-03-12 (14:35 → 15:45 UTC, buys and sells) are at 173.22, the
19:55 bar's close; the bars at those times were 172.38–172.81.

Consequences:
- Intraday round trips in the platform record cost and earn nothing (bought and sold at the
  same price); platform P&L is effectively "positions held at the close, marked close-to-close".
  The 5b platform forward record and its rotation result are not a measurement of intraday
  trading.
- Fill prices see the future (lookahead) relative to the decision time.
- Signals are **not** affected: the strategy context reads only bars `< bar_timestamp`.
- Daily cadence (Steps 1–5) is unaffected in practice (one tick at the close).

### F3 — Position sizing differs fundamentally

| | Research (`SimplePositionSizer`) | Platform (`PositionSizer` + portfolio cycle) |
|---|---|---|
| Size of one BUY | fixed $100k ÷ number of symbols (= $10k with 10 symbols) | **whole sleeve budget** × vol scalar, capped at `MAX_SYMBOL_EXPOSURE` ($25k) |
| Sleeve fit | none needed (10 × $10k = 100 %) | all buys scaled down to the sleeve budget (`_cap_buys_to_budget`) |
| Vol scaling | none | yes (15 % annual target from the last 20 bars) |
| Capital base | $100k regardless of `initial_cash` | budget % × account equity, changes with re-weights |

Measured fill sizes (Jan–Apr): research median ~$9.9k per position on all trading strategies;
platform median $10–16k, max at the $25k cap. A platform sleeve of ~$60k holds ~2–3 positions
(whichever symbols signalled first, until the budget is used up) where the re-sim holds up to
10 equal positions. Different holdings → different returns and low return correlation, even
with identical signals.

### F4 — Fill model differs

Research re-sims: `SimulatedFillModelConfig()` with **no** volume-participation cap. Platform
and shadow fills: `build_platform_execution_service()` with a **5 %** participation cap. Same
slippage and commission models otherwise. Both fill at the close of the bar stamped at the
decision time (signal from bars before it) — once F2 is fixed.

### F5 — Backtest session hours ignore daylight saving (backtest only)

`platform_replay/ingestion_hooks.py` ingests 14:30–21:00 UTC ("EST, no DST adjustment") and
`_intraday_bar_timestamps` ticks 14:30–20:55 UTC all year. From 2024-03-10 (EDT) every day has
66 bars instead of 78: **the first trading hour (09:30–10:25 ET) is missing**, and the replay
keeps ticking for an hour after the close. 36 of 83 trading days are affected. This hits
research and platform equally (same dataset) but makes all EDT-period results wrong.
Production ingestion is DST-aware (`ingestion/helpers/session.py`), so live is unaffected.

### Churn and speed (secondary) — what the data says

- **Churn is mostly real round trips, not resizing.** Platform fills Jan–Apr classified as
  open / close / resize: `factor_based` 8,850 / 8,848 / 2,847 (resize 6 % of notional),
  `momentum_v1` 4,075 / 4,075 / 4,375 (17 %), `mean_reversion_v1` 904 / 902 / 225 (3 %). Short
  lookbacks on 5-minute bars flip signals constantly. Because of F2 these round trips were
  free in the platform record, so nothing (review, bench, research) penalised them.
- No no-trade band: any 1-share change in the vol-scaled target becomes an order
  (`calculate_deltas`).
- Speed (5–11 min per trading day): not profiled yet. Per-tick work includes the features job,
  rebuilding all trading-cycle dependencies and strategy contexts, one Parquet read per
  strategy × symbol (`StrategyContextBuilder.build`, no cache) and on-deck shadow trading.

## Decisions for the user

**Agreed 2026-10-01:** D1 registry warmup in both paths; D2 (a) fill at the close of the bar
stamped T; D3 (a) equal split; D4 DST fix; D5 churn → speed → re-record. Plan approved.

**D1 — Lookback (F1).** One shared rule: every strategy gets exactly its registry warmup bars
in both paths; research loads that many warmup bars and gates on the same number.
*Recommended; no real alternative if composites with EMA / Wilder RSI must match.*

**D2 — Fill timing (F2).** A decision made at tick T (from bars before T) fills at:
- (a) **the close of the bar stamped T** (5 minutes after the decision) — what research does
  today, so research metrics and caches stay valid; slightly conservative. *Recommended.*
- (b) the open of the bar stamped T (≈ the moment the order would hit the market live) — more
  realistic, but changes every research result too (research fill model gets a new policy).

**D3 — Sizing (F3).** One shared, pure sizing function used by both paths. Which semantics?
- (a) **Equal split** per position: sleeve budget ÷ number of symbols the strategy trades ×
  vol scalar, capped at the symbol cap. Diversified, independent of signal order, close to what
  research and approval measured. **Changes production sizing** (`execution/`, not `safety/`).
  *Recommended.*
- (b) Keep platform semantics (whole budget per position, cap, trim to budget) and make
  research copy it. No production change, but positions stay concentrated in whichever 2–3
  symbols signal first, and re-sims must model the path dependence.
- Either way: vol scaling applies in both, the re-sim capital base becomes a notional sleeve
  budget (the strategy's equal share of starting cash), and research uses the platform fill
  factory (F4, 5 % participation cap).

**D4 — Backtest session hours (F5).** Fix ingestion hook, tick schedule and backtest close
time with `America/New_York` (zoneinfo, as production ingestion does). Requires re-ingesting
the backtest dataset. *Recommended.*

**D5 — Secondary priorities.** Proposed order after parity: churn (measure first with real
fill prices; options below) → speed (profile, then fix the top hotspots) → re-record + re-sweep.

## Work breakdown and verification gates

### 5c-A — Parity test harness (first, documents the gaps)
- `tests/.../test_research_trading_parity.py` with a tiny synthetic 5-minute Parquet dataset in
  `tmp_path` (several symbols, a few days, trends and reversals).
- **Signal level**: for every registered strategy type (default params, plus long-window params
  and a composite using EMA and Wilder RSI), evaluate every bar through the research context
  (`build_from_window`) and the trading-cycle context (`build` over Parquet); signals per
  (bar, symbol) must be identical.
- **Trade level**: one strategy, same bars, same capital: research `SimulationRunner` vs the
  platform cycle driven tick by tick (portfolio evaluation with the simulated broker on the
  SQLite test DB). Fill lists (timestamp, symbol, side, qty, price) must be identical.
- Lands with the current gaps marked `xfail(strict=True)` with the finding id; each later
  sub-step removes its marker.
- **Gate:** harness runs in the default suite (< 1 min); xfails match F1–F4 exactly.

### 5c-B — Lookback parity (F1, D1)
- Shared `strategy_lookback_bars(strategy_type, params)` (registry warmup); research runner
  uses it for both window warmup and the context gate; the four `lookback_bars=20` call sites
  go away; trading cycle keeps using the same function.
- **Gate:** signal-level harness passes for all types; probe shows `macd` / `factor_based`
  trade in re-sims; research smoke run still completes; full suite, pre-commit.

### 5c-C — Backtest fills at the decision bar (F2, D2)
- `SimulatedBrokerClient` returns the bar stamped at the tick (latest bar `≤` tick when there is
  none, never a later one) for fills, `get_latest_trades`/quotes and shadow fills.
- **Gate:** tests (intraday fills use the tick's bar; no bar after the tick is ever used; daily
  cadence unchanged); 2-day production-cadence backtest spot check — fill prices vary through
  the day and match the bar closes.

### 5c-D — Session hours with DST (F5, D4)
- Ingestion hook, `_intraday_bar_timestamps` and the backtest close use `America/New_York`;
  early closes respected.
- **Gate:** tests for a January and a July date (78 bars, correct UTC times) and an early close;
  re-ingested dataset has 78 bars on EDT days; full suite.

### 5c-E — Sizing and fill-model parity (F3, F4, D3)
- Shared pure sizer (per D3) used by `SimulationExecutionEngine` and the portfolio cycle;
  research builds its fill service from `build_platform_execution_service()`; re-sim capital =
  notional sleeve budget.
- **Gate:** trade-level harness passes; sizing unit tests; existing portfolio-cycle, shadow and
  research tests updated; full suite, pre-commit.

### 5c-F — Parity check on real data
- 2-week production-cadence backtest (4 seeded strategies), then export the rotation dataset
  and compare each strategy's re-sim with its platform sleeve over the same dates: trade
  counts, daily-return correlation, return gap.
- **Target:** correlation ≥ 0.9 and |return gap| ≤ 1 % per strategy; any residual explained.
  Known residuals the re-sim cannot see: cross-sleeve symbol-cap trims, internal crossing,
  per-order risk blocks and throttles, re-weights during the window.

### 5c-G — Churn (after parity; plan only, details after measuring)
- Re-measure orders/day and turnover with real fill prices (5c-F run).
- Options: (1) no-trade band for resizes in the shared sizer (e.g. skip a resize under ~10 % of
  the target) — sizing, my side; (2) penalise turnover in research admission / scorecards using
  the now-visible costs; (3) minimum holding period / signal hysteresis in strategies (changes
  strategy semantics, research parameter); (4) `MAX_ORDERS_PER_BAR` / `MAX_ORDERS_PER_HOUR` for
  the live paper account — **throttles, user's call**.

### 5c-H — Intraday backtest speed (plan only, details after profiling)
- cProfile one production-cadence trading day; fix the top hotspots (candidates: per-tick
  dependency rebuild, uncached per-symbol Parquet reads, per-tick features job, shadow cycle).
- **Target:** ≤ 2 min per trading day; results identical to before (same fills on a 2-day run).

### 5c-I — Re-record and re-sweep
- Reset, re-ingest, record `portfolio_rotation_6m_intraday.yaml` (auto, defaults streak 4 /
  tenure 60), export, validate the simulator, sweep 324 configs vs the no-rotation baselines;
  Step 5 rule for any new defaults.
- Outcome section, master plan, memory.

Each sub-step: new tests, full suite (`python -m pytest -m "not integration and not external
and not alpaca"`; `test_dashboard_api_matches_paper_trading_runtime_state` is a known pyarrow
flake), `pre-commit`, and a backtest where it matters. Commits only when asked.

## Progress notes

### 5c-A — Parity harness (done 2026-10-01)
- `tests/utilities/parity_harness.py` + `tests/platform/test_research_trading_parity.py`:
  synthetic 3-symbol, 4-day raw 5-minute dataset; signals compared on the last day for
  momentum, mean reversion, MA crossover 10/30, factor (windows 100) and an EMA-crossover
  composite; trades compared for momentum through the real `run_trading_cycle` with the
  backtest `SimulatedBrokerClient` vs the production `SimulationRunner`.
- Before fixes: mean reversion passed; the other four signal cases failed on F1 (research 0
  vs platform 407 signals for the crossover, 0 vs 25 for factor; momentum missed the
  window's first bars; composite 19 vs 12 — EMA path dependence); trades failed (platform
  fills at the day's last close, F2).
- Gate: 1 passed, 5 strict xfails, ~75 s (over the < 1 min target: the platform path does one
  DuckDB Parquet read per tick × symbol — the same cost 5c-H looks at).

### 5c-B — Lookback parity (done 2026-10-01)
- `StrategyDefinition.context_lookback_bars(params)` = registry warmup, at least 1 bar.
  `SimulationRunner` loads that many warmup bars and gates the context on the same number
  (`StrategyContextBuilder.with_lookback`, a per-run copy); the trading cycle
  (`_instantiate_strategy`) uses the same function. `build_simulation_context` lost its
  `lookback_bars` argument (callers passed 20; CLI / pipeline callers silently used 50).
- Also fixed: the legacy single-strategy cycle (portfolio mode off) merged research's
  wrapped `{type, parameters}` config over the defaults, so a research candidate traded
  with default parameters there (the Step 3A bug, fixed then only for portfolio mode).
- Harness: all five signal cases pass. Real data (Jan 8–31 2024, 10 symbols, research
  runner): `macd_crossover_v1` 0 → 444 fills, `factor_based__19ab…` 0 → 5,823,
  `momentum_v1` 2,784 → 2,812 (now trades from the window's first bar).
- Consequence: research can now generate and admit strategies needing more than 20 bars.

### Suite blocker — DuckDB bar reads leaked native memory (fixed 2026-10-01)
- The 5c-B suite run crashed the machine (PyCharm died: Windows commit limit exhausted).
  Cause: every `HistoricalBarDatasetReader.read_with_duckdb` call left ~147 MB of DuckDB native
  memory behind inside the pytest process (not reproducible standalone; result tables held
  0 MB, the Arrow pool was empty). `test_dashboard_api_matches_paper_trading_runtime_state`
  reads 250 symbols → ~37 GB. It was the "known pyarrow flake": with less memory DuckDB's OOM
  fallback ran `read_with_pyarrow`, which dropped the `symbol` partition column → "Casting
  field 'symbol' with null values". Pre-existing (clean HEAD fails identically).
- Fix: pyarrow is the default bar-read engine everywhere (`reader.read`, bar repository,
  window loader, universe candidate builder); `read_with_pyarrow` adds the partition columns
  (symbol / year / month) fragments lack, and returns `schema.empty_table()` on an empty
  range (it raised). DuckDB stays available via `engine="duckdb"`. Side effect: timestamps
  come back in UTC (DuckDB converted them to the machine's local time zone).
- Full suite: peak 0.84 GB (was > 30 GB), **3 min 22 s** (was ~15 min).
- Test: `tests/storage/test_bar_reader_partition_columns.py`.

### 5c-C — Backtest fills at the decision bar (done 2026-10-01)
- `SimulatedBrokerClient` uses the latest bar stamped ≤ the tick (never a later one) for fills,
  `get_latest_trades` / quotes and shadow fills (`bar_for`); no bar ≤ tick → order stays open.
  Each symbol's day of bars is read once per day (kept across intraday ticks, cleared on a new
  date). Daily cadence (tick at the close) still uses the last bar.
- Tests: `tests/execution/test_simulated_broker_tick_bar.py` (fill = own bar close and prices
  vary through the day; tick between bars; before the open; daily close; one read per day).

### 5c-D — Session hours with DST (done 2026-10-01)
- `MarketCalendarService.regular_session_utc(day)`: 09:30–16:00 America/New_York (13:00 on
  early closes) in UTC. Backtest ingestion window, `_intraday_bar_timestamps` and the backtest
  close tick use it. Added 2024 early closes (Jul 3, Nov 29, Dec 24) to the static calendar.
- Tests: `tests/application/services/test_backtest_session_hours.py` (Jan / Jul: 78 bars at
  14:30 / 13:30 UTC; early closes in EST and EDT; daily tick at the close; ingestion window).
- Not done (outside D4): the backtest still ticks on NYSE holidays (`_trading_dates` = weekdays;
  ingestion finds no bars) and the static calendar has no 2024 holidays.

### 5c-E — Sizing and fill-model parity (done 2026-10-01)
- Shared rule `execution/services/sleeve_sizing.py`: position budget = sleeve budget ÷ symbols
  traded (equal split) × vol scalar (last 20 closes before the bar), capped, floored to shares.
  Platform: `PositionSizer.compute_quantity(symbol_count=…)` ← `manifest.universe_member_count`
  in the portfolio cycle and on-deck shadow cycle (legacy single-strategy mode unchanged).
  Research: `SimplePositionSizer` uses the same rule + `VolatilityScalingService`; the engine
  feeds it the closes before each bar and sizes from equity at the previous bar
  (`capital_scale`); the runner loads ≥ 20 warmup bars (context still gets exactly its
  lookback). Research fills through `build_platform_execution_service()` (5 % cap).
- Found while matching trades: the backtest broker reported equity from the last cash
  snapshot, which is only rewritten on fills, and the cycle syncs total capital to broker
  equity every tick → sizing drifted between fills. `get_account` now marks positions at the
  close of the last bar stamped before the tick (previous session's close at the open).
- Cache key: `SimulationCacheKey` gains `sizing_model` and `max_volume_participation_rate`;
  older cached results (incl. pre-5c-B lookback) can no longer match.
- **Trade-level harness passes: identical fills (time, symbol, side, qty, price).**
- Not done: re-sim capital base stays $100k (not the sleeve's notional budget); at these sizes
  the 5 % participation cap rarely binds — 5c-F measures whether scale matters.
- Tests: `tests/execution/test_sleeve_sizing.py`, broker equity test in
  `test_simulated_broker_tick_bar.py`, parity harness.

### F6 — Platform exited every position that was not re-signalled (found and fixed 2026-10-01)
- Found by the first 5c-F run. `PortfolioConstructionService.calculate_deltas` gave every held
  symbol without a signal this bar target 0, so the next bar's silence sold it. Entry/exit
  strategies signal only on the crossing bar: on the platform **100 % of `macd_crossover_v1`
  positions closed after one bar** (median hold 5 min vs 145 min in research), mean reversion
  10 min vs 1,100 min. Momentum (signals every bar) was unaffected — and the harness only
  compared momentum trades, so it missed this.
- User decision (2026-10-01): **the platform holds** — an ACTIVE strategy keeps a position until
  it signals SELL/FLAT; a symbol outside the trading universe (rotation, delisting) still exits;
  WINDING_DOWN / orphan sleeves still exit everything.
- `generate_order_intents(hold_symbols=…)`; the trading cycle passes its resolved universe
  through `run_trading_evaluation_job(universe_symbols=…)` (resolved there when a caller does not)
  to the portfolio cycle (ACTIVE only), the on-deck shadow cycle and the legacy single-strategy
  cycle.
- Tests: trade-level parity now parametrized over momentum, MA crossover and mean reversion
  (the two new cases fail without the fix); `TestHoldUntilExitSignal` in
  `test_portfolio_construction_service.py` (held / exit signal / left the universe / exit-only).

### Backtest ingestion recorded ~693 false missing-bar incidents per day (fixed 2026-10-01)
- `IngestBarsJob.run_once` fed a fetch symbol by symbol; a 5-minute cycle is finalized when a
  later bar arrives, so a multi-cycle window (a backtest's full-day fetch) finalized every cycle
  with only the first symbol received: 9 symbols × 77 cycles = 693 per day. Bars were written
  correctly; only the incidents (and freshness metrics) were wrong. Production fetches one
  cycle per run, so it was unaffected. Bars are now fed in (timestamp, symbol) order.
- Test: `test_multi_cycle_window_records_no_false_missing_bars` (498 incidents with the old
  order, 0 now).

### 5c-F — Parity check on real data (done 2026-10-01)
- Fixture `fixtures/platform/replays/short/parity_2w_intraday.yaml`: Mar 4–15 2024 (spans the
  Mar 10 DST switch), 5-minute cadence, the four step-5 seeds as fixed sleeves (research, bench,
  review off), $250k. Dev DB backed up first:
  `D:\PythonVenvs\atp_db_backups\ratp_before_5c_reset_20261001_2048.dump` (the 4-month 5b record;
  restore with `pg_restore --clean -d ratp`).
- Run: 10/10 days, 0 failures, ~5,500 orders, 55 min, peak 0.8 GB. DST verified on real data:
  from Mar 11 trading runs 13:30–19:55 UTC.
- Re-sim (`export-rotation-dataset`) vs platform sleeve, same dates:

| Strategy | Fills matched | Daily-return corr | Re-sim / platform return | Gap | 5b (corr / gap) |
|---|---|---|---|---|---|
| `factor_based__19ab…` | 93 % | **1.000** | +0.45 % / +0.49 % | +0.04 pp | re-sim 0 trades |
| `macd_crossover_v1` | 87 % | **1.000** | −1.41 % / −1.36 % | +0.05 pp | re-sim 0 trades |
| `mean_reversion_v1` | 89 % | **0.998** | +1.30 % / +1.35 % | +0.05 pp | 0.20 / −9.6 % |
| `momentum_v1` | 86 % | **1.000** | +0.94 % / +0.93 % | −0.01 pp | 0.49 / −2.8 % |

- **Target met** (corr ≥ 0.9, |gap| ≤ 1 %). Residual: every unmatched fill on either side is a
  resize of ≤ 2 shares — rounding at different capital bases (re-sim $100k, sleeve ~$59k;
  platform quantities are 0.59× the re-sim's). Internal crossing (8–30 % of ledger entries)
  does not disturb parity.
- Before the F6 fix the same run gave corr 0.42 / 0.51 and gaps +1.4 / −1.6 pp for macd and mean
  reversion (momentum and factor_based already within target).

### 5c-G — Churn measured (2026-10-01; decision for the user)
Platform sleeves, same 2 weeks, real fill prices (turnover = notional traded per day ÷ sleeve):

| Strategy | Fills/day | Open / close / resize | Resize % notional | Turnover/day | Median hold |
|---|---|---|---|---|---|
| `factor_based__19ab…` | 382 | 1438 / 1430 / 568 | 4.6 % | 25.6× | 10 min |
| `momentum_v1` | 224 | 787 / 784 / 670 | 5.8 % | 12.9× | 20 min |
| `macd_crossover_v1` | 32 | 143 / 135 / 43 | 7.9 % | 2.1× (was 2.1×) | 145 min (was 5) |
| `mean_reversion_v1` | 32 | 94 / 91 / 137 | 17.9 % | 1.6× (was 3.5×) | 1,100 min (was 10) |

- F6 removed the forced one-bar round trips of the entry/exit strategies.
- What remains is **strategy-intrinsic**: `factor_based` (momentum lookback 1 bar) and
  `momentum_v1` (lookback 5 bars = 25 min) flip on 5-minute noise. Resizes are ~5 % of their
  notional, so option (1) no-trade band would barely help; the levers are (2) penalise
  turnover in research admission / scorecards and (3) minimum holding period / hysteresis —
  both change what research admits or how strategies behave (user's call). Research now
  measures these trades at the platform's fill model and slippage, so admission already sees
  their costs.

### 5c-G — Churn levers A + B (user decision 2026-10-02; done)
User chose (A) penalise turnover in grading and (B) a minimum lookback for research
candidates; no change to how strategies trade (no holding period).
- One measure everywhere: `daily_turnover` = notional traded per trading day ÷ mean equity
  (`research/experiments/filtering/metrics/trade_metrics.py`), carried on `TradeMetrics` (the
  simulation runner passes the equity curve), on bench `ResimOutcome` and in the rotation
  simulator.
- **A, research:** `FilterConfig.max_daily_turnover` (off by default); the replay research
  pipeline sets `RESEARCH_MAX_DAILY_TURNOVER = 10` on the initial filter and the walk-forward /
  Monte Carlo filter. Slippage is already in research returns; the cap is a backstop.
- **A, review:** scorecard lens `turnover_penalty = 0.02 × (re-sim turnover − 2)` per day above
  2× (13×/day → −0.22, 26×/day → −0.47; a Sharpe point is +0.40). Stored on
  `portfolio_scorecards` (migration `zz44aa55bb66`); the offline rotation simulator applies the
  same lens from the dataset's re-sim fills, so its validation still holds.
- **B:** `ParameterSpec.is_window` on the 8 window / lookback parameters; research generation
  (`ParameterSpaceResolver`) keeps them ≥ `MIN_RESEARCH_WINDOW_BARS = 10` (50 min), and the
  composite-rule catalog drops instances under it (mom_5, roc_5, sma_5, ema_5). Validation is
  unchanged, so existing 1- and 5-bar strategies (the seeds) stay valid and keep trading —
  the review's turnover lens is what can rotate them out.
- Tests: `tests/research/test_turnover_and_window_floor.py`, turnover lens tests in
  `test_portfolio_scorecard_service.py`, bench re-sim outcome carries turnover.

### 5c-H — Intraday backtest speed (partly done 2026-10-02)
- Pace at production cadence: 5b ran ~5 → 11 min per trading day (slowing as history grew);
  5c-F ran 2.3 min on day 1 rising to ~5.5 min (55 min for 10 days). Biggest win already landed
  with the DuckDB → pyarrow reader switch.
- cProfile, 3 days (234 ticks, 891 s): strategy context builds 181 s (each of 4 strategies × 10
  symbols read Parquet every tick and converted **1.09 M rows** to `MarketBar` to keep the last
  ≤ 100 — a 5-bar strategy's window is ~24 days); DB statements ~38 % of the run (~70k per
  simulated day; writes ~4 ms per row, Postgres round trip 0.8 ms); sleeve position writes
  94 s; recent closes 56 s; the universe bootstrap's live Alpaca screener calls ~36 s once per
  run.
- Fixed:
  - `StrategyContextBuilder.build` filters and slices in Arrow and converts only the bars handed
    to the strategy; `_fetch_recent_closes` reads only the close column.
  - Per-tick lookups on growing tables had no index (`cash_snapshots` / `position_snapshots`
    latest by timestamp, open / reconcilable `tracked_orders`, a strategy's latest
    `order_intents`): migration `yy33zz44aa55` + model indexes. On 3 days the tables are
    small; this is what made long runs slow down day by day (Postgres stats: `order_intents`
    73,888 sequential scans / 940 M rows read over the 5b run).
- Same results: identical fills per day on Mar 4–6 before and after (224 / 486 / 677), parity
  harness unchanged. 3 days: ~11.3 → 10 min.
- **Target (≤ 2 min/day) not reached.** What remains is spread out: ~70k DB statements per
  simulated day (one unit of work / flush per position, snapshot, manifest, audit event), the
  per-tick portfolio refresh (0.24 s/tick) and Parquet reads (4 strategies read the same
  symbol files each tick). Getting to 2 min needs fewer writes per tick (batch a tick's writes
  into one transaction, skip per-tick mark-to-market rewrites in backtests) — a larger change
  to the cycle's persistence, proposed as a follow-up.

### 5c-I — Re-record with all fixes + A/B (2026-10-02)
`portfolio_rotation_6m_intraday.yaml` (Jan 2 – Jun 28 2024, 5-minute cadence, auto review,
streak 4 / tenure 60), output `portfolio_rotation_6m_intraday_5c*.json`. 129/129 days, 0 failed,
0 tracebacks, 70,291 orders, 11 h (2.4 → 7 min per day — still grows, see 5c-H), peak 1.1 GB,
no false missing-bar incidents.

| | Return | Sharpe | Max DD |
|---|---|---|---|
| Portfolio (6 months) | **+10.04 %** | **2.71** | **2.26 %** |
| SPY buy-and-hold | +15.13 % | 2.73 | 5.37 % |
| 5b portfolio (4 months, old code) | +1.89 % | 0.73 | 4.19 % |

- Turnover 1,036× in 6 months (~8× the account per day) vs ~1,950× in 4 months in 5b (~23×/day).
- Rotation: research added two low-churn candidates (`momentum__ab29…` lookback 60, ~3.3×/day;
  `mean_reversion__e2d8…` window 10, ~3.2×/day) → on-deck Mar 1 → Mar 25 `momentum__ab29…`
  **swapped in for `momentum_v1`** (13×/day, turnover penalty −0.23) and `mean_reversion__e2d8…`
  took an added seat. `factor_based` (27×/day, penalty −0.51) kept its seat: largest
  contributor (+$10.6k, 42 % of P&L) — its edge clears the penalty.
- Re-sim vs platform (same dates, daily-return corr / gap): factor_based 0.998 / +0.15 pp,
  macd 0.995 / −0.47, mean_reversion__e2d8 0.993 / −0.23, momentum__ab29 0.995 / −0.03,
  momentum_v1 0.997 / +0.43; **mean_reversion_v1 0.825 / +3.62 pp → F7** (below).
- Research: all candidates had windows ≥ 10 bars; the 10×/day turnover cap never had to fire.
- Simulator validation: same decisions except which of the two Mar 25 entrants came in by swap
  vs added seat; return 9.32 % vs recorded 10.04 %, DD 2.37 % vs 2.26 %.
- Sweep (324 configs): no-rotation baseline +9.88 % / Sharpe 2.85 / DD 1.91 %; current defaults
  (sim) +9.32 % / 2.50; best +10.10 % / 2.86 (3 fixed seats, margin 0.05, streak 2) — a tie with
  the baseline, not a winner. Defaults unchanged.

### F7 — Research re-sims ignore stock splits (found 2026-10-02, not fixed)
Re-sims run on the run's **raw** bars and the research engine has no split handling. A re-sim
holding NVDA over its 10-for-1 split (2024-06-10) booked a ~90 % "loss" on it: 3.0 of
`mean_reversion_v1`'s 3.3 pp gap is that one day. The platform is right (corporate actions
adjust positions). Affects research admission, bench re-sims and rotation exports for any
strategy holding overnight across a split (or reverse split); signals around the split see the
jump too. Fix options: run research on split-adjusted bars, or apply split events in the
simulation engine as dividends already are (A-02).

## Out of scope
Step 6 (Airflow), live trading, scorecard weights, frontend. Throttle / pre-trade / safety
changes only with explicit user approval.
