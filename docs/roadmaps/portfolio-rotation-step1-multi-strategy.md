# Portfolio Rotation — Step 1: Multi-Strategy Active Portfolio

Status: implemented and verified (2026-09-28) — see "Outcome" at the end
Parent design: dynamic active set (3–6) → on-deck shadow tier → bench management →
portfolio review → rotation backtest → Airflow schedules. This document covers step 1 only.

## Goal

Run several strategies in one trading cycle, each with its own capital budget and its
own attributed positions and P&L, and rename `approved_research` → `candidate`.

## Current state (verified in code)

| Area | Today | Problem for multi-strategy |
|---|---|---|
| Strategy selection | `_resolve_active_strategy` (`scheduler/common/trading_cycle_common.py:131`) returns the single most-recently-updated paper/live row | Only one strategy ever trades; any governance write can flip which one |
| Position deltas | `calculate_deltas` (`execution/services/portfolio_construction_service.py:459`) diffs targets against **account-level** broker positions; any held symbol without a target is sold | With 2+ strategies, strategy A would liquidate strategy B's holdings |
| Order ownership | Intents carry `strategy_id`, but `run_order_submission_job` records orders/control checks with `manifest.strategy_id` | Must use the intent's strategy |
| Fill accounting | `PostFillAccountingService` updates account-level position/cash snapshots only | No per-strategy positions or P&L |
| Live metrics | `LivePerformanceMetricsService` builds each strategy's equity curve from the run's account `CashSnapshot` | Per-strategy Sharpe/drawdown is really the portfolio's |
| Netting | `PortfolioSignalAggregator` / `PortfolioConstructionPipeline` net *signals* and size the result under one `strategy_id` | Loses attribution; not usable as the sleeve foundation |
| Allocation | Policy by (state, tier) + overrides; `QualityBasedReallocationService` writes overrides for all paper/live strategies | Percentages not tied to an active set; can sum past 100% |

Backtester: `platform_replay/runtime_hooks.py` calls `run_trading_cycle` directly with the
`SimulatedBrokerClient`, so the multi-strategy cycle is exercised in backtests automatically.

## Design

### Sleeves (per-strategy books)

Each active strategy owns a **sleeve**: its own positions, cost basis and realized P&L.
Invariant: for every symbol, `sum(sleeve qty) == broker account qty` (difference is tracked
in an explicit `__unattributed__` sleeve, never silently).

Each strategy's orders are computed against **its own sleeve**, not the account. Every broker
order belongs to exactly one strategy, so fills attribute trivially via `intent → strategy_id`.

**Internal crossing:** when two strategies trade the same symbol in opposite directions in
one cycle, the overlapping quantity is transferred between sleeves at the current price
(ledger entry, no broker order) and only the residual is sent to the broker. This avoids
wash-trade rejections and pointless round-trips while keeping attribution exact.

### Active set

New membership concept, separate from governance eligibility:

- `ACTIVE` — trading, has a budget.
- `WINDING_DOWN` — removed from active (demoted, retired, disabled, or deselected) but its
  sleeve still holds positions; the cycle runs it with target 0 for everything until flat,
  then it leaves the set.

(`ON_DECK` / `BENCH` statuses are reserved for steps 2–3; step 1 creates the table so they
drop in without another migration.)

Limits in `operator_settings`: `min_active_strategies` (default 3), `max_active_strategies`
(default 6). Step 1 selector (placeholder until the step 4 review): eligible
(paper/live, enabled, not SUSPENDED) strategies ranked by blended quality score, top N up to
max. Below min → trade what exists and emit a warning metric; never fabricate strategies.

### Budgets

Active strategies' `max_pct_of_capital` is normalized so the set sums to a configurable
deployable fraction (default 95%). Weights come from quality reallocation restricted to the
active set, falling back to equal weight. Existing policy/override/drawdown-scalar layers
still apply per strategy inside that budget.

### Candidate rename

`approved_research` → `candidate` everywhere (enum, DB rows, promotion rules, allocation
policies, alias maps, CLI, frontend label). Aliases keep accepting `approved_research` as
input. Audit history rows are left as written.

## Work breakdown and verification gates

Each sub-step ends with a verification gate; we stop and review before moving on.

### 1A — Candidate rename
- `GovernanceState.CANDIDATE = "candidate"`; update `ALLOWED_TRANSITIONS`.
- Alias maps in `strategy_governance_service.py` (`_STATE_MAP`, `_RULE_STATE_ALIASES`,
  transitions, role permissions) + auto promotion/demotion `_NEXT_STATE` tables.
- Research seeding (`research_hooks.py`), `initial_state_hooks.py`, reallocation,
  catalog service, CLI (`governance.py`, `backtesting.py`), experiment pipeline cycle.
- Alembic data migration: `strategy_governance.current_state`,
  `promotion_rules.from_status/to_status`, `capital_allocation_policies.approval_status`.
- Frontend `ExperimentLab.tsx` label.
- **Gate:** full test suite green; `alembic upgrade head` + downgrade on Postgres;
  grep shows `approved_research` only in aliases/migrations/history.

### 1B — Sleeve ledger (storage + service, not yet wired)
- Contracts: `StrategySleevePosition`, `SleeveLedgerEntry` (source: `broker_fill` |
  `internal_cross` | `adoption`), `StrategySleeveSnapshot`.
- SOR tables: `strategy_sleeve_positions` (strategy_id, symbol, qty, avg_cost,
  realized_pnl, updated_at), `strategy_sleeve_ledger` (append-only entries, unique on
  fill_id/cross_id for idempotency), `strategy_sleeve_snapshots` (per strategy per cycle:
  allocated capital, market value, realized/unrealized P&L, net P&L).
- `StrategySleeveLedgerService`: `apply_fill`, `apply_internal_cross`,
  `positions_for(strategy_id)`, `snapshot(prices)`, `reconcile(account_positions)` →
  mismatch report + `__unattributed__` sleeve.
- **Gate:** unit tests for FIFO/avg-cost realized P&L, partial fills, idempotent replay of
  the same fill, cross between sleeves, reconciliation mismatch detection.

### 1C — Active set + budgets
- SOR `portfolio_memberships` (strategy_id, status, since, reason, updated_by) +
  membership transition audit; `operator_settings` limits + deployable fraction.
- `ActivePortfolioService`: `resolve_active_set()` (selector above), wind-down detection
  (non-flat sleeve of a no-longer-active strategy), `budgets()` normalization.
- Restrict `QualityBasedReallocationService` to ACTIVE members.
- **Gate:** tests for selection/limits, demotion → WINDING_DOWN, budget sum ≤ deployable
  fraction, disabled strategy handling.

### 1D — Multi-strategy trading cycle
- `TradingCycleDependencies` carries a list of per-strategy runtimes (strategy, context,
  governance state, membership status, budget) instead of one.
- Evaluation job: loop strategies → evaluate → targets sized from that strategy's budget →
  deltas vs **its sleeve** (wind-down strategies: target 0) → intents. One strategy failing
  evaluation is isolated (logged, others continue).
- New crossing step: group intents by symbol, cross opposing quantities between sleeves,
  emit residual intents.
- Submission/reconciliation: use `intent.strategy_id` (control checks, runtime state);
  after `post_fill_accounting.apply_fill`, also apply to the sleeve ledger.
- Cycle manifest: `strategy_id="portfolio"`, active set + per-strategy outcomes in metadata;
  per-strategy spans/metrics.
- End of cycle: sleeve snapshots + reconciliation check (metric + warning on mismatch).
- `PortfolioSignalAggregator` kept for conflict telemetry only (no suppression) in 1D.
- **Gate:** unit/integration tests with 3 strategies overlapping on symbols; then a short
  platform backtest (smoke profile) showing ≥3 strategies with fills and zero sleeve/account
  mismatches every tick.

### 1E — Per-strategy live metrics
- `LivePerformanceMetricsService` equity curve from `strategy_sleeve_snapshots`
  (return on allocated capital) instead of account `CashSnapshot`.
- Auto promotion/demotion/health/reallocation consume these unchanged.
- **Gate:** tests showing two strategies in the same run get different Sharpe/drawdown;
  sum of sleeve P&L ≈ account P&L (within fees/cash).

### 1F — End-to-end backtest verification
- Fixture: seeded pool of ~5 eligible strategies + candidates, 2–3 months, daily cadence,
  governance on (warn_only + auto_resume per existing backtest config guidance).
- Checks: active set size within limits each day; attribution invariant holds every tick;
  a forced demotion winds a sleeve down to flat; per-strategy P&L sums to portfolio P&L;
  artifact bundle includes per-strategy summary.
- **Gate:** review the run output together before starting step 2.

## Open decisions (defaults proposed)

1. Existing paper-account positions at cutover → `__unattributed__` sleeve, wound down
   (default) vs adopted into a strategy.
2. Budget weights in step 1 → quality reallocation restricted to actives, equal-weight
   fallback, 95% deployable (default).
3. Initial selector ranking → blended quality score (default).

## Out of scope for step 1
On-deck shadow trading, bench pruning, portfolio review/swaps, rotation backtest mode,
Airflow schedules.

## Outcome (2026-09-28)

All sub-steps 1A–1F implemented and verified. Portfolio mode is behind
`operator_settings.portfolio_mode_enabled` (default **off**); with it off the legacy
single-strategy cycle is unchanged.

### Verification
- Full backend suite green (non-integration).
- `fixtures/platform/replays/short/smoke_portfolio_mode.yaml` (5 days, 3 strategies).
- `fixtures/platform/replays/medium/portfolio_rotation_pool.yaml` (63 days, 6 strategies,
  5 seats): 63/63 trading days, 0 errors; every fill attributed; sleeves sum to the
  account every tick; every sleeve within budget (worst 1.01x); demotion on 2024-02-15
  wound a strategy holding 8 positions down to flat (sells only) and the unseated
  strategy took the seat the same day; sum of sleeve net P&L equals cash + marked
  holdings − starting cash to the cent; per-strategy live metrics persisted daily at
  replay time.

### Changes beyond the original plan (found during verification)
- **Budget cap on the whole sleeve.** PositionSizer sizes each position from the full
  strategy allocation; portfolio mode now trims a strategy's buys so its projected
  sleeve value fits its budget (sells never trimmed).
- **Ledger cash snapshot forking (pre-existing).** Post-fill accounting wrote one cash
  snapshot per fill with a random id; ties on timestamp made `get_latest()` pick an
  arbitrary row and cash/equity drifted. Now deterministic per (run, bar), like
  position snapshots.
- **Repeated-order throttle scoped per strategy.** Two strategies trading the same
  symbol/side in one bar are no longer "duplicates"; the same strategy still is.
- **Symbol-level pre-trade checks use real positions (pre-existing bug).** The cycle
  used `StubRiskStateReader` (zero exposure), so any order — including an exit — was
  new exposure: an appreciated position above `MAX_SYMBOL_EXPOSURE` could never be
  sold and the failure froze the whole cycle daily. Now `PositionAwareRiskStateReader`.
- **Per-order limit breaches reject only that order in portfolio mode**
  (`ORDER_LIMIT_ERRORS`); global safety errors still fail the cycle closed.
- **As-of time threaded through governance** (promotion, health, reallocation, active
  set). Previously every service used the wall clock, so historical backtests never
  saw live metrics.
- **Live metrics persisted before health evaluation.** Nothing called
  `compute_and_persist`, so the health lifecycle never had live inputs (live or backtest).
- Long research strategy ids overflowed `broker_orders.client_order_id` (String 64);
  long ids now use a readable prefix + digest.
- Replay timeline events now honour `actor_role` from fixtures.
- `scripts/reset_backtest_state.py` clears the new sleeve/membership tables.

### Known gaps / follow-ups
- `MAX_GROSS_EXPOSURE` and `MAX_DAILY_NOTIONAL_TRADED` still use per-order semantics
  (stub aggregate state). Enforcing them against real state needs limits re-based on
  equity (current absolute values are sized for a ~$100k paper account).
- Before enabling portfolio mode on the paper account, raise `MAX_ORDERS_PER_BAR` /
  `MAX_ORDERS_PER_HOUR` (≈ strategies × symbols per bar).
- The account-level ledger's cash-snapshot `equity` marks untouched positions at their
  last fill price, not the current close; sleeve snapshots mark to market.
- Per-symbol caps are checked per order against start-of-cycle state; several
  strategies buying the same symbol in one cycle are not aggregated intra-cycle.
- 1F surfaced duplicate strategies (e.g. `mean_reversion_v1` vs a research
  `mean_reversion__…` with identical P&L) — input for step 3 bench de-duplication.
- Quality-score history is only written by the reallocation service, which the replay
  does not run; the health lifecycle's quality-decline signal is therefore thin in
  backtests.
