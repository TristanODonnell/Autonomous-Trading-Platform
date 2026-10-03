# Portfolio rotation — Step 5d: corporate actions end to end

Status: plan approved 2026-10-02 (decisions D1–D8 agreed, see below) — **done 2026-10-02** (5d-A–G); see Outcome
Parent plan: `docs/roadmaps/portfolio-rotation-plan.md`. Follows Step 5c
(`portfolio-rotation-step5c-research-parity.md`, finding F7).

## Goal

A stock split or cash dividend on a held symbol changes share counts and cash, never P&L,
in every book the platform keeps — account positions, real and shadow sleeves, cash — and in
the research simulator; strategies see continuous split-adjusted history in live, backtests
and research; fills stay at real traded prices. Today none of that holds: the NVDA 10-for-1
split on 2024-06-10 booked fake losses in **both** engines of the 6-month backtest, and the
ingestion pipeline has never stored a real Alpaca corporate action.

## Discovery (2026-10-02)

Everything below was verified in code and, where possible, against the dev DB (the 6-month
intraday run, dataset `raw_bars_20261002T092744Z_73f180cb`), the Alpaca corporate-actions
API and a read-only probe of the live context builder. Two items from the 5c handoff were
wrong: the platform did **not** get the split right (F4), and the live "v1" problem is
wider than the dataset name (F5).

### F1 — Forward splits are never requested

`alpaca_corporate_action_client.fetch_corporate_actions` keeps only the `cash_dividends` and
`reverse_splits` lists from each page; `CorporateActionIngestionService` reads the same two
keys. The normalizer maps eight provider types, so this is purely a collection gap.

Alpaca, for the 10 replay symbols over 2024 (probe `alp2.py`): 39 cash dividends, **1
forward split** (NVDA 10:1, ex 2024-06-10, `old_rate 1 / new_rate 10`), 1 stock merger
(PXD → XOM 2024-05-03, `acquiree_symbol`/`acquirer_symbol`, no `symbol`/`ex_date`).

### F2 — No real Alpaca action can be parsed, even when collected

`CorporateActionNormalizationService.parse_alpaca_corporate_action` requires a `type` or
`ca_type` field on every item. Alpaca's v1 payload has **no per-item type**: the type is the
key of the list the item sits in (`cash_dividends`, `forward_splits`, …). It also reads the
dividend amount from `cash`; Alpaca sends `rate`. Run against the real NVDA split and AAPL
dividend items:

| Item | Result |
|---|---|
| NVDA forward split (real payload) | `ValueError: Corporate action missing valid 'type' field` |
| AAPL cash dividend (real payload) | same error |
| AAPL dividend with `type` injected | parses, but `cash_amount=None` |

The unit tests inject `ca_type` and `cash`, so they pass. Consequence: the pipeline has
never stored an Alpaca action. The dev DB holds exactly one `corporate_actions` row — a
synthetic AAPL `SPLIT_FORWARD` dated 2025-01-10 with `split_ratio NULL` and
`action_id ca-…` (a leaked test fixture, not Alpaca). Parse failures only go to the audit
log as `CORPORATE_ACTION_PARSE_FAILED`; the job still reports success.

### F3 — The backtest's corporate-action job fetches "now", not the replayed date

`ingestion_hooks.run_corporate_actions_at_timestamp` calls
`run_corporate_action_ingestion_cycle(source_raw_bars_dataset_version_id=…, trigger_type,
actor)` — **no `fetch_start`, `fetch_end` or `fetch_symbols`**. Inside the cycle
`cycle_start/cycle_end` come from `datetime.now()` and the fetch window is `None`, so
Alpaca is asked for its default window at the real wall clock. Alpaca with no dates returns
nothing (probe: `no-dates NVDA → {}`). Evidence in the 6-month artifact: 129 ticks each with a
`corporate_actions` summary whose `cycle_start/cycle_end` is `2026-10-01 → 2026-10-02` while
the tick is 2024; 129 `corporate_actions` dataset versions registered; 0 actions stored;
`ingestion.corporate_actions_ingested: 0`.

The paper/live EOD path (`PaperTradingGoldenPathOrchestrator.run_eod_maintenance`) calls the
cycle the same way (no window) and then **registers an empty "validated" `adjusted_bars`
dataset version every day** that nothing ever writes (see F5).

### F4 — Nothing applies an action to positions, sleeves or cash; both engines book a fake loss

No reference to `split_ratio`, corporate actions or dividends exists outside
`ingestion/corporate_actions/`, `research/` and `contracts/` — not in `execution/`
(position ledger, cash ledger, sleeve ledger, post-fill accounting), `scheduler/` or the
simulated broker. Corporate actions only produce an adjusted copy of some bars (F7).

**Platform (6-month intraday run, 2024-06-10, first tick 13:35 UTC)** — sleeve ledger:

| Sleeve | Pre-split holding (cost ≈ $1,207) | Sell on ex-date | Realized |
|---|---|---|---|
| `macd_crossover_v1` | 3 NVDA | 3 @ 120.75 | **−3,259** |
| `momentum__ab29…` | 3 NVDA (+28 bought at 120.48 that morning) | 31 @ 120.75 | **−3,246** |

Sleeve `net_pnl` 06-07 → 06-10: macd 5,532 → 2,065; momentum__ab29 3,358 → −188. So the 5c
note "the platform is right" was wrong — it only looked right because the per-symbol cap
($25k) meant only 3 shares of a $1,200 stock were held overnight. `mean_reversion_v1`'s
3.0 pp research gap (F7 in 5c) is the same bug on the research side with a larger holding.

**Live/paper path (code-confirmed; a split cannot be forced on the paper account, so this
gets a fake-broker test, not a dry run):**
1. `run_trading_evaluation_job._fetch_positions` reads broker positions: after a 10:1 split
   Alpaca reports 10× quantity and a ÷10 `avg_entry_price`.
2. `portfolio_evaluation` step 1 calls `StrategySleeveLedgerService.reconcile(…,
   adopt_unowned=True)` whenever no orders are open: the 9× shares no sleeve owns are
   **adopted into `__unattributed__`** at the broker's average cost.
3. Sleeves not backed by a trading member are treated as `WINDING_DOWN` and are exit-only →
   the adopted 9× is **sold** on the next signal-free cycle.
4. The original sleeve keeps 1× at the pre-split cost → ~90 % unrealized loss, realized when
   it exits. Portfolio drawdown governance and the health lifecycle see that loss.
5. Reverse split: sleeves hold more than the account; `reconcile` reports a mismatch (never
   auto-fixed) and step 5 caps sells at the account quantity, so the sleeve can never fully
   exit and stays mis-marked.

`BrokerRuntimeSyncService.sync_positions_from_broker` and the external broker
reconciliation are report-only (position drift ≥ 10 shares → CRITICAL) — they would flag the
day but not fix anything.

### F5 — Live strategy contexts get no bars at all

`trading_cycle_common` builds every live context with `dataset_version="v1"` on the
**ADJUSTED** dataset whenever there is no backtest override. Probe on the dev DB
(`probe_live.py`, `momentum_v1`, NVDA, 2024-06-12 15:00 UTC):

| Context | Result |
|---|---|
| live default (`adjusted_bars`, version `v1`) | **`context=None`** (no files) |
| backtest override (`raw_bars_20261002T092744Z_73f180cb`) | 6 bars, last close 125.69 |

`data/bars/` contains only `raw/` (33 raw versions); no adjusted dataset has ever been
written because the adjusted write only happens when an action is *newly created* (never,
per F2). Two further problems behind the name:
- Live ingestion creates **one raw dataset version per trading date**
  (`daily_dataset_resolver_service.get_or_create_active_daily_dataset`), so a corrected
  lookup must read a warmup window across several daily versions (or live ingestion must
  become cumulative like the backtest's single version).
- Live research (`research_hooks` without override) and features
  (`features_hooks`, default `PriceBasis.ADJUSTED`) resolve "latest validated
  `adjusted_bars`" first — which, after any paper EOD, is the empty version from F3.

### F6 — Research: no split handling, dividend plumbing is dead

- `SimulationExecutionEngine.execute` accepts `dividend_events` (A-02) and applies them per
  bar, but `SimulationRunner._execute_simulation` never forwards `dividend_events`
  (nor `settlement_days` / `execution_policy_config`) — `SimulationRunConfig.dividend_events`
  is dead config, and no caller sets it anyway (`grep dividend_events=` → none).
- No split logic anywhere in `research/`. Research in backtests reads the run's **raw** bars
  (price basis RAW with the cumulative version; `research_dataset_resolver_service` picks the
  dataset folder from the basis), so a held position across a split is marked at the raw
  post-split price against the pre-split cost.
- The research cache key hashes `dividend_events` but knows nothing about splits.

### F7 — The adjusted-bars design is partial even where it runs

`CorporateActionAdjustmentService.apply_action_to_bars` adjusts only bars **before** the
ex-date, for **one** action, from one raw source version, and writes them to a **new
timestamped adjusted version containing only that symbol's pre-date bars**. Dividends are not
applied (`supports_adjustment` = splits only). Multiple actions on a symbol do not compose. The
version is registered with `interval=ONE_DAY` although the bars are 5-minute. The
`corporate_actions` Parquet dataset is registered on every cycle but never written (actions
live in SOR only).

### F8 — Raw bars are correct for fills

Both Alpaca bars clients build `StockBarsRequest` without `adjustment`, so Alpaca returns raw
prices (NVDA 2024-06-07 close 1,208.65 → 06-10 close 121.65 in the backtest dataset). Fills at
these prices are right; everything else must adjust around them.

## Decisions for the user

**Agreed 2026-10-02:** D1 (a) collect all types, apply splits + cash dividends, alert on the
rest; D2 (a) −7/+30-day window on the as-of date; D3 shared pure rule with cash in lieu and an
idempotency table; D4 apply on the first cycle on/after the ex-date before adoption, with the
adoption guard; D5 adjust on read, splits only, retire the materialised adjusted dataset; D6 (a)
resolver across daily raw versions; D7 research gets splits + dividends through the shared
rule; D8 (a) short NVDA split fixture only, 6-month re-record deferred to the speed work.

**D1 — Which actions to ingest and apply.**
- (a) **Collect every list Alpaca returns**; fix the normalizer for the real payload (type
  from the list key, `rate` for dividends, merger/spin-off fields); **apply** forward/reverse
  splits and cash dividends; **store and alert** on stock dividends, mergers and spin-offs
  (operator warning + health check, no automatic book change; the delisting path already
  exits a symbol whose bars stop). *Recommended.*
- (b) Splits and cash dividends only; drop the rest at the client.

**D2 — Fetch window.** Every corporate-action run (backtest daily tick, live EOD, CLI) fetches
`[as_of − 7 days, as_of + 30 days]` for the universe/replay symbols, where `as_of` is the
**replayed tick date** in backtests and today in live. Upcoming actions are therefore known
before their ex-date, a missed day is backfilled, and backtests get the real historical
actions. A one-off backfill uses the existing CLI (`atp ingestion corporate-actions
--start --end --symbols`). *Recommended; alternative: ex-date-only windows, which cannot
pre-announce.*

**D3 — One shared accounting rule.** A pure function in a new domain module (e.g.
`accounting/corporate_actions.py`, no I/O):
- split: `quantity × ratio`, `avg_cost ÷ ratio`, realized P&L unchanged; fractional
  remainder from a reverse split is rounded down and paid as cash in lieu at the ex-date
  open (what Alpaca does);
- cash dividend: `cash += quantity held at ex-date open × rate`, settled immediately (as
  A-02 research does);
- applied **once per (action, book, strategy)** and recorded (new SOR table
  `corporate_action_applications`; sleeve ledger entries get a `corporate_action` source).
Used by the account position book + cash ledger (backtest), real **and shadow** sleeve
ledgers, and the research engine. Dividends are attributed to sleeves pro rata to sleeve
quantities. *Recommended.*

**D4 — Live timing and adoption guard.** The EOD job (cron 22:00 UTC) stores actions due
tomorrow. The first trading cycle on/after an ex-date applies due, unapplied actions to the
sleeves **before** the unowned-share adoption, so sleeve quantities already match the
broker's post-split account and nothing is adopted or sold. Guard: for a symbol with an action
in the last trading day, adoption is skipped and a warning logged if the broker quantity does
not yet match (Alpaca processes pre-market). In backtests the simulated account/cash
snapshots are adjusted at the same point. *Recommended.*

**D5 — Strategy history: adjust on read, retire the materialised adjusted dataset.**
`StrategyContextBuilder` (shared by live, backtest and research) multiplies bars before each
stored split's ex-date by the cumulative factor when building a context; fills keep raw
prices. Options for dividends in history: (a) **splits only** (prices stay tradeable; total
return comes from the cash rule) *recommended*; (b) also back out dividends (Alpaca
"all"-style adjustment). The `adjusted_bars` writes and the per-day empty registrations are
removed; features get the same adjusted reader. The research cache key gains a
`corporate_actions_hash` over the window. *Alternative: keep a materialised adjusted dataset
but make it complete (all symbols, composed actions, cumulative version) — more code, two
sources of truth.*

**D6 — Live dataset lookup.** Replace the hard-coded `"v1"` / ADJUSTED default with a
resolver that reads the latest validated **raw** daily versions covering `[now − warmup,
now]` and unions them. (a) **Resolver across daily versions**, no ingestion change
*recommended*; (b) make live ingestion cumulative (one version per month) — touches the daily
resolver and the S3 publish plan. The same resolver feeds live research and features.

**D7 — Research.** Forward `dividend_events` (and `settlement_days`, policy) from the run
config to the engine; load both splits and dividends for the window from SOR into the config;
apply through the D3 rule. *Recommended; no real alternative.*

**D8 — Verification depth.** Unit and service tests for every path (below), then a **new
short fixture** over the NVDA split (`corporate_actions_nvda_split.yaml`, 2024-06-03 →
06-14, 3 symbols, daily cadence, ~6 min) asserting continuous sleeve P&L and a correct
research re-sim across 06-10. Options: (a) **short fixture only now**, re-record the
6-month intraday run (≈ 10 h, background + memory watchdog) later with the speed work;
(b) re-record the 6 months as part of 5d. *Recommended (a).*

## Work breakdown and verification gates

### 5d-A — Ingestion that actually stores actions (F1, F2, F3; D1, D2)
Client collects all lists and tags each item with its list key; normalizer parses the real
payload (fixtures captured from the probe: NVDA split, AAPL dividend, PXD/XOM merger);
validation rejects dividends without an amount; fetch window + symbols threaded through the
cycle from the backtest hook, the paper EOD and the CLI; cycle metadata uses the as-of date;
drop the paper EOD's empty adjusted registration; parse failures counted in the job summary.
*Gate:* unit tests on real-payload fixtures; one-off backfill 2024-01-01 → 06-30 for the 10
replay symbols on the dev DB stores 39 dividends + 1 split (+ 1 merger flagged); full suite;
pre-commit.

### 5d-B — Shared rule + ledger application (F4; D3)
Pure rule module + tests (forward, reverse with cash in lieu, dividend, idempotency, no
P&L change); `corporate_action_applications` migration; application to the account position
snapshot + cash snapshot (backtest), real and shadow sleeve ledgers, pro-rata dividends.
*Gate:* ledger tests; migration upgrade → downgrade → upgrade on dev Postgres; suite.

### 5d-C — Trading-cycle integration and the live path (F4; D4)
Apply due actions at the top of `portfolio_evaluation` before adoption; adoption guard;
`SimulatedBrokerClient` / backtest snapshots adjusted at the same point.
*Gate:* fake-broker test — split overnight, next cycle: sleeve 10×, nothing adopted, nothing
sold, net P&L unchanged; reverse-split variant; dividend variant; suite.

### 5d-D — Strategy history and the live lookup (F5, F7; D5, D6)
Adjust-on-read in `StrategyContextBuilder`; daily-version resolver for live; features and
live research use it; remove adjusted-dataset writes.
*Gate:* context tests (bars before ex-date scaled, after unchanged, two splits compose);
`probe_live.py` returns bars on the dev DB with no override; suite.

### 5d-E — Research engine (F6; D7)
Runner forwards config to the engine; splits + dividends loaded from SOR per window; cache
key hash.
*Gate:* engine tests (split during a hold → equity continuous; dividend → cash up); the 5c
parity harness still passes; suite.

### 5d-F — Backtest over a known split (D8)
New fixture; **back up the dev DB first** (`pg_dump` to `D:\PythonVenvs\atp_db_backups\`,
as before 5c), `python scripts/reset_backtest_state.py`, delete
`artifacts/platform/backtests/<output stem>.checkpoint.json`, run in the background with the
memory watchdog.
*Gate:* NVDA sleeve P&L continuous across 2024-06-10 (no realized loss on the ex-date);
corporate actions stored for the run; research/bench re-sim of a holder shows no split loss;
0 errors.

### 5d-G — Close out
Docs (this file "Outcome", master plan row, 5c F7 marked fixed), memory note.

## Progress notes

### 5d-A — Ingestion that actually stores actions (done 2026-10-02)

Built: the client keeps every list Alpaca returns (ten types seen in June 2024 alone) and
merges pages; the normalizer takes the list key as the type, reads `rate` as the dividend
amount and the merger / spin-off / name-change symbol fields; a validation rule rejects
dividends without an amount; the cycle takes `as_of` and fetches `[as_of − 7, as_of + 30]`
(explicit `--start/--end` override it) for the symbols the source raw-bars dataset
declares (or an explicit list); the backtest hook passes the replayed tick date and the
replay symbols, the paper chains pass today and the day's symbols, the historical
backfill passes its own date range; stored-but-not-applied types (mergers, spin-offs,
stock dividends, name changes) raise a `CORPORATE_ACTION_MANUAL_REVIEW_REQUIRED` audit
event and a warning; every run reports counts (fetched / unsupported / parse_failed /
validation_failed / created / updated / manual_review); the repository upsert falls back
to the natural key when the provider re-issues an id. The job no longer writes the
partial adjusted-bars dataset (D5); the paper EOD's own adjusted registration is left
for 5d-D with the readers.

Found while building:
- **F2 was worse than parsed-nothing**: the ingestion service handed the Pydantic
  `CorporateAction` contract to a repository that expects the ORM row
  (`UnmappedInstanceError`), so even a parsable action could never have been stored. The
  old tests faked the unit of work and never hit it. The repository now maps contract ↔
  row (`to_row` / `to_contract`) and `upsert` accepts either.
- The dev DB's audit log already held **29,727 `PARSE_FAILED` events** from past
  paper/live corporate-action runs — Alpaca was returning real actions all along and every
  one failed on the missing `type` field.
- The paper full-pipeline chain read the raw-bars row after the trading cycle closed its
  session (`DetachedInstanceError`); its id and symbols are now captured up front, as the
  EOD chain already did.

Gate:
- 80 corporate-action tests (client, normalizer on real payloads, validator, service,
  cycle window/symbols/counts, repository contract mapping + natural key, backtest hook).
- Full suite under the 6 GB cap: 4,944 passed, 5 skipped, 1 xpassed, 5 failed — three were
  the paper-chain detach above (fixed), two portfolio-route tests failed only in that
  run's order; all five pass together after the fix. pre-commit (ruff, format, mypy) clean.
- One-off backfill on the dev DB (`atp ingestion run-corporate-actions --start 2024-01-01
  --end 2024-06-30 --symbols <10 replay symbols> --source-raw-bars-dataset-version
  raw_bars_20261002T092744Z_73f180cb`): 19 actions stored — 17 cash dividends (15 with
  ex-dates in the window plus SPY/QQQ December 2023 dividends paid in January), the NVDA
  10:1 split (ex 2024-06-10, ratio 10) and the PXD → XOM stock merger flagged for manual
  review. Re-running the same command: 0 created / 19 updated.

### 5d-B — Shared rule + ledger application (done 2026-10-02)

Built: `accounting/corporate_actions.py` — a pure module (no I/O) with `apply_split`
(shares × ratio, cost ÷ ratio, remainder rounded down and paid in cash at the ex-date
price, its difference to cost basis realized), `dividend_cash`, `apply_action` and
`is_applicable` (splits with a usable ratio ≠ 1, cash dividends with an amount; every
other type is manual) and `split_factor_before` (cumulative price factor for history).
`StrategySleeveLedgerService.apply_corporate_action` applies it to a sleeve in either
book: a split rewrites the position row and books a `corporate_action` ledger entry for
the share change (no P&L); a dividend leaves the position alone and books the cash as
realized income, so sleeve P&L includes it. `CorporateActionAccountingService`
walks every book — real sleeves, shadow sleeves and, in backtests only, the account
position + cash snapshots — for applicable actions due by the as-of date (30-day
lookback), skips positions whose last change is on/after the ex-date (already
post-split; reported, never guessed), and records each (action, book, scope) in the new
`corporate_action_applications` table (migration `aa55bb66cc77`) so a re-run is a no-op.
`reconcile` gained `skip_adoption_symbols`.

Gate: 23 rule tests + 13 ledger/service tests (forward 10:1 on the 6-month sleeves →
30 shares at 120.7185 and a later sell books +0.945 instead of −3,259; reverse 1:25 with
cash in lieu; dividend; live mode leaves the account book; post-ex-date position
skipped; window/lookback; manual types; idempotent re-run); the 29 existing sleeve
tests unchanged; migration upgrade → downgrade → upgrade on dev Postgres; full suite
under the 6 GB cap 4,987 passed / 0 failed / 5 skipped / 1 xpassed; pre-commit clean.

### 5d-C — Trading-cycle integration (done 2026-10-02)

Built: `scheduler/jobs/apply_corporate_actions_step.apply_due_corporate_actions` runs
at the top of `run_trading_evaluation_job` on every cycle, before anything reads
positions. It adjusts the account book only for the `SimulatedBrokerClient`
(backtests); a real broker has already applied the action to the account it reports.
Prices for cash in lieu come lazily from the broker (only the symbols with a due
action). The outcome feeds the adoption guard in `run_portfolio_evaluation`: symbols
with an action due today or yesterday, or with a skipped application, are never
adopted that cycle (mismatches are reported with a warning instead); if the step fails
the cycle still trades but adoption is withheld entirely and a
`CORPORATE_ACTIONS_STEP_FAILED` audit event is written; applications write
`CORPORATE_ACTIONS_APPLIED`.

Gate: 8 step tests with a fake Alpaca broker — split overnight: sleeves 3 + 3 → 30 + 30,
broker 60, nothing adopted, nothing sold, sleeve net P&L unchanged at the equivalent
post-split mark; the old path's behaviour documented (27 shares adopted into
`__unattributed__`); broker not yet adjusted → mismatch reported, no adoption; reverse
split; dividend (no price fetch); simulated broker also rewrites the account snapshot;
failed step withholds adoption; nothing due is quiet. Full suite under the cap
4,995 passed / 0 failed / 5 skipped / 1 xpassed; pre-commit clean.

### 5d-D — Strategy history and the live lookup (done 2026-10-02)

Built: `StrategyContextBuilder` reads **raw** bars and split-adjusts history on read
(`accounting.corporate_actions.adjust_bars_for_splits`: every bar before a split's
ex-date, ex-date on or before the evaluation date, is expressed in post-split terms;
a future split is never applied; two splits compose). It takes a
`dataset_version_resolver` so a read window can span several dataset versions
(merged by timestamp, later version wins) and a `split_source`
(`SorSplitSource` reads the stored actions; `StaticSplitSource` for research/tests;
`with_split_source` gives research a copy with its own). `build_strategy_runtime_context`
no longer defaults to ADJUSTED `v1`: with no override it uses `LiveBarDatasetResolver`
(validated raw versions overlapping the window — live ingestion writes one per day)
and the SOR split source; backtests pass their one cumulative raw version. The vol
scalar's recent closes go through the same builder. The feature pipeline split-adjusts
its loaded frame the same way (`FeatureDatasetResolverService(split_source=...)`,
wired in `run_feature_pipeline_cycle`), the replay features/research/bench hooks read
raw bars only, the backtest hands the feature hook its cumulative raw version, and
the paper EOD no longer registers an adjusted-bars dataset: features run on the day's
raw version (`features` dataset metadata `price_basis=raw`, `stage=eod`). The
materialised adjusted-bars dataset is retired (the Parquet definition stays for old
files).

Gate: 8 context-builder tests (before/after ex-date, no lookahead, two splits compose,
no source → raw, multi-version merge with re-ingested bar, resolver fallback, recent
closes adjusted/empty) + 3 feature-resolver tests; paper/historical golden paths and
the hook tests pass. `probe_live.py` on the dev DB with no override now returns bars
(F5 fixed): NVDA at 2024-06-10 13:40 UTC shows the June 7 bars as 121.22/120.85/…
with volume ×10, `adjusted`, factor 0.10, and the June 10 bars raw — one continuous
series across the 10:1 split; 2024-06-12 15:00 UTC returns 6 raw bars (was
`context=None`). Full suite under the cap: 5,007 passed / 0 failed / 46 skipped / 1 xpassed; pre-commit clean.

### 5d-E — Research engine (done 2026-10-02)

Built: `SimulationRunner` takes a `corporate_action_source` (`SorCorporateActionSource`
in `build_simulation_context`: the stored applicable splits and cash dividends for the
run's symbols, loaded from 400 days before the window start so warmup history is
adjusted too). Every loaded split goes to the strategy's history via
`context_builder.with_split_source(StaticSplitSource(...))`; the in-window splits go to
`SimulationExecutionEngine.execute(corporate_actions=...)`, which applies them on the
first bar of the ex-date through the shared rule (`accounting.apply_action`: whole
shares × ratio, cost ÷ ratio, fraction paid in cash at that bar's price, realized P&L
booked) before anything is marked or sized, and rescales the sizer's close history;
in-window cash dividends become the engine's `dividend_events` (`dividend_events_from`),
whose cash now goes through `accounting.dividend_cash`. The request also carries
`settlement_days`, `execution_policy_config`, and explicit `corporate_actions` /
`dividend_events` overrides (a given list, even empty, bypasses the source); all are
forwarded — the F6 dead plumbing is live. New result frame `corporate_action_log`.
The cache key gains `corporate_actions_hash` (explicit actions only; runner-loaded
actions are implied by dataset + window, and old persisted keys still load).

Gate: 6 engine tests (10:1 split during a hold → equity +0.5%/+0.5% across the ex-date
instead of the −90% the old books showed — that case is kept as a test of what 5d
fixed; position 98 → 980 at cost 102 → 10.2; 1:4 reverse split pays the half share in
cash at the ex-date price with its gain realised; a split with nothing held is a no-op),
4 runner tests (window + history load, in-window filter, dividend events, settlement /
policy forwarded, explicit overrides), 3 SOR source tests, 3 cache-key tests; dividend,
checkpoint, cache and the 5c research↔trading parity harness unchanged. Schema drift vs
the dev Postgres 55 passed. Full suite under the cap: 5,022 passed / 0 failed / 46 skipped (14 Alpaca external, 27 schema drift needing a Postgres URL — run separately: 55 passed, 5 pre-existing) / 1 xpassed; pre-commit clean.

### 5d-F — Backtest over a known split (done 2026-10-02)

Fixture `fixtures/platform/replays/medium/corporate_actions_nvda_split.yaml`: NVDA, AAPL,
MSFT, daily cadence, **2024-05-20 → 2024-06-14** (three weeks before the ex-date so
positions exist by then — the plan's 06-03 start would have had no holders), the four
approved seeds, research smoke monthly, bench weekly. Dev DB backed up first
(`D:\PythonVenvstp_db_backupsatp_before_5d_reset_20261002_1803.dump`, 97 MB), reset,
checkpoint removed, run under the 6 GB cap in the background (~4.5 min for 19 trading
days). Artifact `artifacts/platform/backtests/corporate_actions_nvda_split.json`.

Result (second run; the first found one bug, below): **20 ticks, 0 errors.**
- Stored actions: every daily corporate-action job fetched 3 actions for the three
  symbols over its as-of −7/+30 window (NVDA 10:1 split 2024-06-10, NVDA $0.01 dividend
  2024-06-11, MSFT $0.75 2024-05-15) and upserted them — the 6-month run stored 0.
- 2024-06-10, first cycle: `corporate_action_applications` holds the split for the
  account book (18 → 180 shares, cost 1,125.53 → 112.55) and both NVDA sleeves
  (factor_based 9 → 90 at 1,131.98 → 113.20; momentum_v1 9 → 90 at 1,135.55 → 113.56),
  cash in lieu 0, realized 0; sleeve ledger shows the +81-share `corporate_action`
  entries; audit `CORPORATE_ACTIONS_APPLIED` "3 applied, 2 skipped" (the skips are the
  AAPL/MSFT dividends whose ex-dates precede the run's positions —
  `position_changed_on_or_after_ex_date`, correct). Nothing adopted, nothing sold
  because of the split: momentum's 90-share sell that day is its own 5-bar momentum
  signal (−0.37 on split-adjusted history, not a −1,087 jump) and realised +728.54 at
  121.65 against the post-split cost — sleeve net P&L 2,551.81 → 2,622.46 across the
  ex-date; factor_based 2,415.86 → 2,174.87 (NVDA fell 0.6 % that day), both continuous.
- 2024-06-11: the dividend credited 90 × 0.01 = 0.90 to the factor sleeve and the
  account book (cash 218,128.69 → 218,129.59); momentum had no position — skipped.
- Bench re-sims over 05-20 → 06-10 (all four strategies, NVDA in the universe): max
  drawdown −1.1 % to −2.5 %, total return −0.2 % to +6.0 % — no split loss (the 5c
  re-sim across the same day booked −90 % on NVDA).

Bug found by the first run and fixed: the replay features hook failed on Memorial Day
(2024-05-27, ingestion wrote 0 rows) with `No bar data found for dataset_version_id=…`
and the artifact recorded 1 error. Before 5d-D the hook silently skipped every backtest
day (it looked for an adjusted dataset that never existed), so this never surfaced.
The hook now treats a day with no bars as `skipped` with a warning, like the paper EOD
(2 tests). The feature cycle still logs its own ERROR traceback for that day before the
hook catches it — noise only.

Observation (not 5d): the two runs' sleeves match to the cent through 06-11, but the
simulated account's equity differs between runs from 06-11 (273,314 vs 255,280 with
identical positions and cash) — an account-level mark-price difference
(`evaluation_job.missing_price` warnings), worth a look with the speed work.

## Outcome

All five handoff items are fixed and verified end to end (5d-A–F); 5c's F7 is closed.
- **Ingestion** stores every Alpaca action type with real dates (list-keyed payloads,
  `rate`/`old_rate`/`new_rate`), fetches as-of −7/+30 days per cycle (backtests pass the
  replayed date), repairs the contract→ORM bug that meant nothing was ever stored, and
  routes mergers/spin-offs/name changes to a manual-review audit event.
- **One shared rule** (`accounting/corporate_actions.py`) applies splits (whole shares,
  cash in lieu, cost ÷ ratio) and cash dividends to real sleeves, shadow sleeves, the
  simulated account book and the research engine, idempotently
  (`corporate_action_applications`, migration `aa55bb66cc77`).
- **Trading cycle** applies due actions at the first cycle on/after the ex-date before
  reconciliation; the adoption guard never adopts post-split broker shares.
- **History is split-adjusted on read** (`StrategyContextBuilder`, features, research);
  the adjusted-bars dataset is retired; live contexts read the validated daily raw
  versions covering the window (no more `v1`), so live no longer evaluates on nothing.
- **Research** receives splits and dividends from the SOR per window; settlement and
  policy config reach the engine; the cache key carries an actions hash.
- Verified by 5d-F (above): continuous sleeve P&L across NVDA's 10:1 split, dividend
  credited, bench re-sims with no split loss, 0 errors.
Tests added across 5d: ~80 (ingestion, normalization, validator, repository, rule,
sleeve/account application, cycle step, context builder, feature resolver, engine,
runner, sources, cache key, hooks); final full suite 5,022 passed / 0 failed. Pre-commit
clean. Nothing committed (user commits on request).

Left open: the 6-month intraday re-record (D8 option a, with the speed work); live
ingestion still writes one raw version per day (D6 chose the resolver); cash-in-lieu
for live comes from the broker's own booking (we only guard adoption); the research
cache key does not hash runner-loaded actions (keyed by dataset + window).

## Out of scope
Mergers/spin-off book changes (alerted only, D1), short positions, options, Airflow (Step 6),
intraday backtest speed (5c-H, still open), frontend.
