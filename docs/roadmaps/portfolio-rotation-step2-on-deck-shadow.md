# Portfolio Rotation — Step 2: On-Deck Shadow Tracking

Status: implemented and verified (2026-09-28) — see "Outcome" at the end
Parent plan: `docs/roadmaps/portfolio-rotation-plan.md` (§5 Step 2). Builds on Step 1
(`portfolio-rotation-step1-multi-strategy.md`). Steps 3–6 (bench, review, rotation backtest,
Airflow) are out of scope.

## Goal

The most promising non-active strategies ("on-deck", default up to 10) run through the same
daily flow as the actives — same evaluation, sizing, budget cap, risk checks, throttles and
fill model — but their orders are filled in simulation into **shadow sleeves** instead of
going to the broker. Each builds a forward (post-approval) track record directly comparable
to the actives' real results. No capital, no broker orders.

## Current state (verified in code)

| Area | Today | Relevance |
|---|---|---|
| Membership | `MembershipStatus.ON_DECK` reserved, unused; `ActivePortfolioService.refresh()` manages ACTIVE / WINDING_DOWN / INACTIVE | On-deck tier drops into the same table and transition log |
| Portfolio cycle | `scheduler/jobs/portfolio_evaluation.py` evaluates ACTIVE runtimes, sizes each against its own sleeve, caps buys to budget, crosses, clamps sells | Shadow path reuses evaluation → sizing → budget cap, then diverges before crossing/clamp/submission |
| Sizing | `PositionSizer` → `PortfolioEngine.get_allocation`, which uses `set_cycle_budgets` when the strategy has a cycle budget | Give on-deck a notional cycle budget; candidates are sized under the paper policy |
| Pre-trade risk | `generate_order_intents` calls `pre_trade_risk_service.assert_order_allowed`; portfolio mode drops `ORDER_LIMIT_ERRORS` breaches. Real reader = `PositionAwareRiskStateReader` (symbol exposure/qty from account; gross/daily/reserved = 0) | Shadow uses a `PreTradeRiskService` over a shadow-sleeve reader with the same semantics |
| Throttle | `OrderThrottleService.assert_order_allowed_for_submission` at submission; built per cycle with `StubOrderActivityReader` (counts only this cycle's reservations) | Shadow uses its own per-strategy, per-cycle `OrderThrottleService` with the same settings |
| Fill model | Backtest `SimulatedBrokerClient` → `SimulatedExecutionService` (close price, 5% volume participation cap, `VolumeShareSlippageModel`, zero commission; no random partials/rejections); unfilled remainder dropped | Shadow builds its execution service from the same factory and fills against the same bar |
| Sleeves | `strategy_sleeve_{positions,ledger,snapshots}` keyed by strategy; `reconcile()` / `aggregate_quantities()` read every row | Shadow must never be in these tables (invariant, crossing, adoption) |
| Live metrics | `LivePerformanceMetricsService` builds per-strategy curves from sleeve snapshots / ledger sells; `get_latest()` feeds health lifecycle and blended quality | Shadow metrics computed the same way from shadow sleeves, labelled `shadow`, never returned as live |

## Decisions (agreed with the user 2026-09-28)

1. **Notional budget** = what an equal-weight active seat gets: deployable % ÷ number of
   ACTIVE members (÷ `max_active` when none are active), capped by `per_strategy_cap`,
   × current total capital. Returns are comparable and sizing matches promotion.
2. **On-deck eligibility** = governance `candidate`, or paper/live-approved but not ACTIVE;
   has a strategy config; operator-enabled; not health-SUSPENDED; not WINDING_DOWN (a
   winding-down strategy becomes eligible once flat → INACTIVE).
3. **Fills: one model for real and shadow.** Shadow fills use the execution service from
   the same factory as the backtest broker, against the same bar (the sim broker's bar in
   backtests). Participation-capped partial fills apply identically; nothing is random.
   Live paper: real fills come from Alpaca; shadow uses the model against the latest
   validated bar, falling back to the cycle price (no volume cap) when no bar exists.
4. **Risk and throttles apply to shadow orders.** Same pre-trade limits (dry run against the
   shadow sleeve) and same throttle limits (counted per strategy). Blocked orders are not
   filled; they are logged and counted per cycle (`blocked_order_count` on the shadow
   snapshot) so Step 4 can use them. **No changes to `safety/`** — the shadow path only
   instantiates its classes; if a change there turns out to be needed, stop and ask.
   Known difference: shadow orders do not compete with actives for the account-wide
   throttle slots, and the portfolio symbol-exposure check uses the sleeve's own exposure
   against total equity.
5. **No health lifecycle on shadow metrics.** Shadow metrics are recorded only; health
   matters once a strategy holds capital.
6. `operator_settings.max_on_deck_strategies`, default **10**; only effective with
   `portfolio_mode_enabled`; 0 disables on-deck.
7. **Slim verification backtest** (~6 weeks, 6 symbols) that still exercises every behaviour.

Technical calls:
- **Separate tables** `shadow_sleeve_{positions,ledger,snapshots}` (same columns as the
  real ones, plus `blocked_order_count` on snapshots). Real invariant/crossing/adoption code
  cannot see them; a strategy can hold a real (winding-down) sleeve and a shadow sleeve.
- **Leaving on-deck liquidates the shadow sleeve** at the cycle price (ledger source
  `tier_exit`), so a later return to on-deck starts flat instead of with stale positions.
  Done in the cycle (which has prices): any shadow sleeve whose owner is not ON_DECK is
  closed, which also covers on_deck → active.
- Selection placeholder (Step 4 replaces it): on-deck incumbents keep their seats; open
  seats go to the highest blended-quality eligible strategies. The existing active
  placeholder may pick a paper-approved on-deck strategy into an open active seat
  (on_deck → active).
- Shadow evaluation runs after the actives; any on-deck failure is logged and isolated,
  never fails or degrades the cycle. Shadow strategies do not drive the strategy runtime
  state machine.

## Work breakdown and verification gates

### 2A — Shadow sleeve storage
- ORM: `ShadowSleevePositionRow`, `ShadowSleeveLedgerRow`, `ShadowSleeveSnapshotRow`
  (`storage/sor/models/strategy_sleeves.py`), migration.
- `ShadowSleeveRepository` (same interface as `StrategySleeveRepository`), on the UoW as
  `uow.shadow_sleeves`.
- `StrategySleeveLedgerService(book=...)` selects the real or shadow repository; the shadow
  book adds `apply_shadow_fill` / `liquidate`; `reconcile` / `adopt` are real-only.
- `scripts/reset_backtest_state.py` clears the new tables.
- **Gate:** unit tests (shadow book accounting, isolation from real reads), full suite,
  pre-commit, migration upgrade → downgrade → upgrade on dev Postgres, Postgres smoke.

### 2B — On-deck tier
- `operator_settings.max_on_deck_strategies` (default 10) + migration + settings API/replay
  initial-state key.
- `ActivePortfolioService.refresh()` maintains ON_DECK after the active set: eligibility,
  placeholder selection, transitions (`selected_on_deck`, `on_deck_over_max`,
  `on_deck_no_longer_eligible`, `promoted_from_on_deck`). Target statuses for both tiers
  are decided first and written once, so active → on_deck is a single transition.
  `on_deck_members()`, `on_deck_budget_pct()`. Refresh result gains `on_deck` /
  `on_deck_added` / `on_deck_removed` / `max_on_deck`.
- **Gate:** unit tests (limits, eligibility incl. candidate, over-max, promotion to active,
  disabled/suspended exits, wind-down → on-deck), full suite, pre-commit, migration round trip.

### 2C — Shadow execution in the cycle
- `resolve_strategy_runtimes` builds ON_DECK runtimes (strategy context + notional budget);
  `set_cycle_budgets` includes them.
- `portfolio_evaluation`: after actives, evaluate on-deck runtimes; size against the shadow
  sleeve; budget cap; dry-run pre-trade risk (shadow reader) and throttle (per strategy);
  fill via `ShadowFillService`; book into shadow sleeves; liquidate shadow sleeves whose
  owner is no longer ON_DECK (`tier_exit`). Shadow intents are never returned
  to the order path. Outcomes report `shadow` status and blocked/filled counts.
- Shared factory for the simulated execution service (sim broker + shadow); public bar
  lookup on `SimulatedBrokerClient`.
- Sleeve snapshot job values shadow sleeves (allocated capital = notional budget).
- **Gate:** tests — zero broker orders / order intents rows from on-deck; real invariant
  unaffected; budget cap; risk and throttle blocks counted and not filled; partial fills
  match the real model; on-deck failure isolated; full suite; pre-commit.

### 2D — Shadow metrics
- `MetricLineageType.SHADOW`. `LivePerformanceMetricsService.compute_for_strategy(..., book=)`
  reads shadow sleeves; persisted with lineage `shadow` into a **separate table**
  `strategy_shadow_performance_snapshots` (changed from "same table + lineage filter"
  after finding that correlation monitoring and risk budgeting read the live table
  directly with no lineage filter — same reasoning as the separate sleeve tables).
- `get_latest()` reads the live table only; `get_latest_shadow()` for shadow.
  `refresh_monitored` also refreshes shadow metrics for ON_DECK members (`refresh_shadow`).
  Live OTel gauges are not recorded for shadow metrics.
- Health lifecycle and blended quality untouched (they read live only).
- **Gate:** unit tests (lineage separation, per-strategy shadow metrics), full suite, pre-commit.

### 2E — Verification backtest
- Fixture `fixtures/platform/replays/medium/portfolio_on_deck_shadow.yaml`: ~6 weeks,
  6 symbols, 3 active seats, `max_on_deck_strategies: 3`, 4 on-deck-eligible strategies
  (mix of `candidate` and unseated paper-approved); mid-run demotion of an active so a
  paper-approved on-deck strategy is promoted and its on-deck seat refilled.
- Checks: shadow sleeves accrue P&L; zero broker orders/intents from on-deck; real sleeves
  still sum to the account every tick; on-deck promotion liquidates the shadow sleeve and
  the seat is refilled; on-deck count ≤ max every day; shadow metrics per strategy differ
  and carry replay-time `computed_at`; blocked counts recorded; every day reaches
  `risk_snapshot`.
- **Gate:** review the run together; update Outcome here, master plan status, memory.

## Out of scope
Bench management, portfolio review/scorecard/swaps (including "only on-deck may swap into
active"), rotation backtest mode, Airflow, frontend.

## Outcome (2026-09-28)

All sub-steps 2A–2E implemented and verified. On-deck only runs with
`portfolio_mode_enabled`; `max_on_deck_strategies: 0` turns it off (cycle identical to Step 1).
Not committed (awaiting the user).

### Verification
- Full backend suite green after each sub-step (non-integration): 4601 → 4617 → 4628 →
  4632 passed, 5 skipped, 0 failures. pre-commit (ruff, ruff-format, mypy) clean.
- Migrations `ss77tt88uu99` (shadow sleeves), `tt88uu99vv00` (`max_on_deck_strategies`),
  `uu99vv00ww11` (shadow performance snapshots): upgrade → downgrade → upgrade on dev
  Postgres. Real-Postgres smoke of the shadow book with rollback (2A).
- `fixtures/platform/replays/medium/portfolio_on_deck_shadow.yaml` (29 trading days,
  6 symbols, 7 strategies, ~13 min): 0 errors, both timeline events applied, every
  portfolio cycle `completed` at `risk_snapshot`.
  - Start: 3 active, 4 on-deck (approved-but-unseated `momentum_v1` + 3 candidates).
  - 2024-01-16 demotion of `mean_reversion_v1` (flat): active → on_deck in one
    transition; `momentum_v1` `promoted_from_on_deck`, its 4 shadow positions closed
    (`tier_exit`) the same tick its real sleeve started trading.
  - 2024-01-29 `max_on_deck_strategies: 2`: the two lowest-ranked on-deck strategies went
    inactive (`on_deck_over_max`); open shadow positions closed. On-deck count ≤ max every day.
  - 0 order intents / broker orders from strategies that were only ever on-deck; 0 real
    sleeve entries for them. Broker orders all belong to active strategies.
  - Real sleeves sum to the account (0 reconciliation mismatches in the log; latest
    sleeves = latest account snapshot); 0 clamped sells.
  - Shadow sleeves accrued P&L per strategy; shadow budget adherence ≤ 1.014x (price drift).
  - Shadow metrics persisted daily at replay time into the shadow table only (lineage
    `shadow`; 0 shadow rows in the live table); returns/Sharpe differ per strategy.
  - 0 blocked shadow orders — expected: the backtest runner raises throttle limits and no
    per-order limit was hit. Blocking is covered by the cycle tests (risk + throttle).

### Changes beyond the original plan (found during implementation)
- **Separate shadow metrics table** instead of lineage filtering: correlation monitoring
  and risk budgeting read the live snapshot table directly with no lineage filter.
- **Shadow-sleeve closing happens in the cycle** (it needs prices), and the shadow step
  also runs when no strategy is on-deck but shadow positions remain — found by a failing
  test (a strategy leaving on-deck kept its shadow positions).
- A price-only shadow fill (live paper, no validated bar) lifts the volume cap: the
  execution model treats "no volume" as "fill nothing".
- `generate_order_intents` gained `pre_trade_risk_service` (override) and
  `on_order_rejected` (callback); unchanged for real orders.
- `build_platform_execution_service()` is the single fill-model factory for the backtest
  broker and the shadow; `SimulatedBrokerClient.bar_for()` exposes the fill bar.

### Known gaps / follow-ups
- **`composite_rule` strategies cannot be instantiated in the trading cycle (pre-existing).**
  Their `strategy_configs.config_json` is stored wrapped (`{type, parameters, strategy_id}`)
  and `_instantiate_strategy` passes it unchanged to the builder, which rejects the extra
  keys; the cycle falls back to a stub (no signals). In 2E `composite_rule__145e07…` sat
  on-deck with a flat record for this reason. Affects active seats too. Not fixed in Step 2.
- Shadow orders do not compete with actives for account-wide throttle slots; shadow sleeves
  do not cross internally; the portfolio symbol-exposure check uses the sleeve's own
  exposure against total equity.
- Live paper: shadow fills use the latest validated bar for the tick date, else the cycle
  price (slippage, no volume cap); real fills come from Alpaca.
- On-deck ranking is still the Step 1 placeholder (blended quality, which ignores shadow
  evidence); weighting shadow evidence is Step 4.
- Each on-deck strategy adds a full evaluation per cycle (backtest time grows accordingly).
- ~~2E showed duplicate strategies~~ — not real: research strategies were running with
  default parameters (fixed in Step 3A, see the Step 3 doc). The composite_rule gap above
  is fixed by the same change.
