# Portfolio Rotation — Step 3: Bench Management

Status: implemented and verified (2026-09-28) — see "Outcome" at the end
Parent plan: `docs/roadmaps/portfolio-rotation-plan.md` (§5 Step 3). Builds on Steps 1–2.
Steps 4–6 (portfolio review, rotation backtest, Airflow) are out of scope.

## Goal

Keep the candidate pool small, diverse and current: research output is admitted to the
bench only when it is novel or better; the bench is re-simulated on a recent window every
week; near-duplicates collapse to one champion per group; stale and weak candidates are
retired; the bench is capped.

## Current state (verified in code)

| Area | Today | Relevance |
|---|---|---|
| Research → pool | `research_hooks._seed_research_governance_from_intelligence` inserts every deployable survivor as governance `candidate`; de-duplication only within one run's parameter-spam clusters | No admission gate, no cap, no pruning; the pool only grows |
| Strategy instantiation | `_instantiate_strategy` (`scheduler/common/trading_cycle_common.py`) passes `strategy_configs.config_json` straight to the registry builder. Research configs are stored wrapped `{type, parameters, strategy_id}` | **Bug:** research strategies trade with family defaults (composite_rule fails → stub). Research simulated the real parameters. See 3A |
| Re-simulation | `SimulationRunner.run(SimulationRunRequest)` runs a stored `{type, parameters}` config over any window → equity curve + metrics; persists `simulation_runs` / `metrics_summary` | Engine for bench re-sims; runs must not leak into `_latest_metrics` (approval-backtest evidence) |
| Clustering | `StrategyClusteringService` clusters research feature vectors (metrics), not return streams | Not used for redundancy; return correlation is the right signal |
| Membership | `MembershipStatus.BENCH` reserved; on-deck pool = all `candidate` + unseated approved | On-deck must draw from BENCH instead |
| Governance | `candidate → retired` allowed; `retired` is terminal; target `retired` requires `operator` / `risk_manager` / `admin` | Pruning needs a system role (decision 1) |
| Replay scheduling | `_CadenceScheduler` supports daily/weekly/monthly per job | New weekly `bench` job |

## Decisions (agreed with the user 2026-09-28)

1. **Pruning = governance `retired`** (terminal, audited, removes the strategy from every
   eligibility check) via a new system role **`system_bench`**, allowed to move `candidate`
   → `retired` only. Research ids are content hashes, so research never re-adds a pruned config.
2. **Redundancy** = correlation of daily re-simulated returns **≥ 0.85, across all families**.
3. **One champion per group**, no alternate.
4. **Expiry:** retire a bench member after **3 consecutive reviews below the score floor**, or
   after **120 days on the bench without reaching on-deck**. Regime-dependent demotion is
   deferred to Step 4.
5. **Bench cap 25** (`max_bench_strategies`, configurable).
6. **Re-sim window:** trailing **63 trading days**, weekly and right after each research run.
7. **Re-sim results drive bench decisions only** in Step 3; they do not feed blended quality
   or on-deck ranking (evidence weighting is Step 4).

Technical calls:
- ACTIVE and ON_DECK members are never pruned by the bench review. They are still
  re-simulated and grouped, so a bench strategy redundant with one of them is pruned (the
  higher tier has better evidence) and they can be group champions.
- Winding-down strategies are not re-simulated and not grouped.
- Bench re-sim runs are tagged (`experiment_id` prefix `bench_resim_`) and excluded from
  `_latest_metrics`, so Step 1/2 scoring does not change silently.

## Work breakdown and verification gates

### 3A — Research strategies trade with their researched parameters
- `_instantiate_strategy` unwraps `config_json["parameters"]` when the config is a wrapped
  research config; warmup from the real parameters.
- Tests for wrapped / unwrapped configs, every family incl. composite_rule.
- Re-run `portfolio_on_deck_shadow.yaml`; correct the "duplicate strategy" notes (Steps 1–2).
- **Gate:** unit tests, full suite, pre-commit, 2E re-run (momentum pair diverges,
  composite_rule trades).

### 3B — Bench tier and storage
- Settings: `max_bench_strategies` (25), `bench_correlation_threshold` (0.85),
  `bench_resim_window_days` (63), `bench_score_floor`, `bench_floor_strikes` (3),
  `bench_max_idle_days` (120). Migration + CLI/replay keys.
- `bench_evaluations` table: one row per (review, strategy) — window, metrics, score,
  group id, champion flag, decision, reason.
- BENCH membership in `ActivePortfolioService`; on-deck pool = BENCH + unseated approved.
- Governance: `system_bench` role may target `retired` from `candidate` only.
- **Gate:** tests, full suite, pre-commit, migration round trip.

### 3C — Bench re-simulation
- `BenchResimulationService`: re-simulate ACTIVE, ON_DECK, BENCH and pending (candidate,
  never reviewed) strategies over the trailing window with fixed configs; daily returns.
- Tagged runs; `_latest_metrics` ignores them.
- **Gate:** tests (results match a direct runner call; tagging/exclusion), full suite, pre-commit.

### 3D — Bench review
- Group by return correlation (single linkage ≥ threshold); champion = best re-sim score
  (higher tier wins ties). Admission: pending strategy admitted if novel or champion of its
  group. Redundant bench members and non-admitted pending strategies retired. Cap: retire
  the lowest-scored bench members above `max_bench_strategies`. Expiry: floor strikes, idle days.
- Every decision recorded in `bench_evaluations` and membership/governance transitions.
- **Gate:** tests per rule (novel, better, redundant, protected tiers, cap, strikes, idle),
  full suite, pre-commit.

### 3E — Replay wiring and verification backtest
- Weekly `bench` scheduled job + a review right after each research tick.
- Slim fixture with research enabled (smoke profile) so new candidates flow in.
- **Gate:** backtest shows admissions, pruning with reasons, cap held, actives/on-deck never
  retired, re-sim runs excluded from approval metrics; update Outcome, master plan, memory.

## Out of scope
Portfolio review / scorecard / swaps, evidence weighting, regime-based demotion, rotation
backtest mode, Airflow, frontend.

## Progress notes

### 3A (done 2026-09-28)
- `_instantiate_strategy` unwraps research configs and validates/default-fills parameters
  with `defn.normalize_parameters` (same as the research `StrategyFactory`); warmup from the
  real parameters. All 35 stored configs now build a real strategy (0 stubs; composite_rule
  works). 7 new tests. Full suite 4639 passed, 5 skipped. pre-commit clean.
- 2E re-run (`portfolio_on_deck_shadow.yaml`): 0 errors; composite_rule now trades
  (16 shadow trades); `momentum__795c…` diverges from `momentum_v1` (32 vs 10 trades);
  `mean_reversion__1ef8…` (window 5) makes no trades where it used to mirror the defaults.
  Real sleeves = account; 0 on-deck orders.
- **Found during verification (pre-existing, not fixed):** `settings.py` calls `load_dotenv()`
  at import, so `DATABASE_URL` from `.env` wins over conftest's `setdefault(sqlite)`. Two
  non-integration tests use `get_engine()` directly and TRUNCATE dev Postgres tables on every
  suite run: `tests/runtime/test_run_manifest_service.py` (`run_manifests`) and
  `tests/ingestion/test_corporate_action_ingestion_service.py` (`market_bars`,
  `corporate_actions`). Running the suite during the 2E re-run wiped the run's early manifests.
  **Fixed (user asked, 2026-09-28):** conftest sets the test env (sqlite `DATABASE_URL`)
  before any app import, and both tests use the shared sqlite `db_session`. Full suite
  4639 passed; a row-count tripwire over all 85 dev tables showed no change during the run.

### 3B–3D (done 2026-09-28)
- 3B: bench settings (`bench_management_enabled` default **off**, `max_bench_strategies`,
  `bench_correlation_threshold`, `bench_resim_window_days`, `bench_score_floor`,
  `bench_floor_strikes`, `bench_max_idle_days`), `bench_evaluations` table (migration
  `vv00ww11xx22`), BENCH tier in `ActivePortfolioService` (on-deck pool = BENCH + unseated
  approved when bench is on; candidates leaving a tier return to BENCH; bench members no
  longer `candidate` leave), governance `candidate → retired` + `system_bench` role
  (candidate-only) + `now=` on `transition()`. Suite 4650 passed.
- 3C: `BenchResimulationService` (stored config, shared window, runs tagged
  `bench_resim_*`), `metrics_quality_score()` shared by backtest/live/bench scoring,
  `_latest_metrics` ignores bench re-sim runs, `stored_config_parameters()` moved to
  `strategy/configs/`. Suite 4658 passed.
- 3D: `BenchReviewService` (grouping, champion, admission, expiry, cap, persistence).
  19 tests. Suite 4677 passed. A test caught a design slip: tier outranked score, so a
  better newcomer could never replace a bench incumbent; fixed (tier only separates
  protected from unprotected; incumbent wins ties).

## Outcome (2026-09-28)

All sub-steps 3A–3E implemented and verified. Bench management is behind
`operator_settings.bench_management_enabled` (default **off**: Step 2 behaviour, every
candidate may go on-deck). Not committed yet.

### Verification
- Full backend suite after each sub-step (non-integration): 4639 → 4650 → 4658 → 4677 →
  4681 → 4686 passed, 5 skipped, 0 failures. pre-commit clean. Migration
  `vv00ww11xx22` upgrade → downgrade → upgrade on dev Postgres.
- `fixtures/platform/replays/medium/portfolio_bench_management.yaml` (48 trading days,
  6 symbols, 2 active, 2 on-deck, bench cap 4, research smoke monthly, weekly bench,
  idle limit 35 days; ~27 min): 0 errors, 49/49 portfolio cycles completed.
  - Reviews skipped until the window had ≥ 15 days (Jan 2/8/15), then ran weekly and
    after each research tick (9 reviews).
  - 2024-01-22 first review of the 6 pending candidates: `composite_rule__145e…` and
    `mean_reversion__e77f…` admitted (novel); `momentum__795c…` (ρ 0.996 with on-deck
    `momentum_v1`), `momentum__a14e…`/`__d5ca…` (ρ 0.966 with each other, grouped with
    `momentum_v1`) and `mean_reversion__7932…` (ρ 0.928 with `momentum_v1`) retired as
    redundant. Next day `composite_rule__145e…` moved bench → on-deck.
  - 2024-02-26 `mean_reversion__e77f…` retired `idle_on_bench` (35 days, never reached
    on-deck).
  - No ACTIVE/ON_DECK strategy ever retired; every retirement is governance `retired` by
    `system_bench` at replay time, with a bench_evaluations row and reason.
  - 47 bench re-sim runs; none became approval metrics (`_latest_metrics` checked for
    every re-simulated strategy). 0 candidate order intents; sleeves = account.
- First run found a bug (below); fixed and re-run with the numbers above.

### Changes beyond the original plan (found during implementation)
- **3A parameters bug** (pre-existing): research strategies traded with family defaults.
- **Test isolation** (pre-existing, fixed at the user's request): the suite truncated dev
  Postgres tables (`run_manifests`, `market_bars`, `corporate_actions`).
- **Research re-seeded retired strategies** (found by the 3E run): research seeding
  keyed on `(strategy_id, config_hash)`, and different seeders hash differently, so the
  Feb research tick inserted a second `candidate` row for the retired
  `mean_reversion__7932…`, which the next review re-admitted. Seeding now skips any
  strategy id that already has a governance row (ids are content hashes), and never
  back-fills a non-candidate row. 5 regression tests.
- Replay jobs missing from a fixture default to daily; the `bench` job therefore runs
  only when the fixture declares it (and the hook no-ops unless bench management is on).

### Known gaps / follow-ups
- **Short windows over-group.** The first review had ~13 daily points (the replay's data
  starts at the run start), and all strategies are long-only, so part of every
  correlation is market beta; `mean_reversion__7932…` joined the momentum group at
  ρ 0.928. Live, the 63-day window is full from the start. Candidates for Step 5 tuning:
  correlation on market-excess returns, a larger minimum overlap (now 10 days), threshold.
- Re-sims (like research) run on the dataset's intraday bars, while this backtest's
  trading cycle evaluates once a day; research, bench and trading cadence should be
  aligned (pre-existing research/trading mismatch, not introduced here).
- Long-warmup strategies (e.g. `factor_based__19ab…`, 100 bars) make no trades in early
  re-sim windows: the backtest dataset has no bars before the run start.
- Not exercised by the 3E run (covered by unit tests): cap eviction, floor strikes, a
  newcomer beating a bench incumbent. Research smoke regenerated only known configs, so
  no genuinely new research output was admitted in the run.
- Bench ranking and on-deck selection still use the Step 1 placeholder (blended quality);
  weighting re-sim / shadow / live evidence is Step 4.
