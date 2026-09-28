# Portfolio Rotation — Master Plan & Session Reference

**Read this first when starting any portfolio-rotation step.** It is the single source of
truth for the overall design, what is already built, and how to approach each remaining step.
Step-level detail lives in companion docs (e.g. `portfolio-rotation-step1-multi-strategy.md`).

Last updated: 2026-09-28 (Step 1 complete).

---

## 1. Why this exists

Before this work:

- Governance approved strategies but **only one strategy ever traded**: the trading cycle
  picked the most recently updated approved strategy.
- Approved strategies were **never re-tested** after approval. Nothing asked "is this still
  good?" or "is a candidate better?"
- Research kept producing candidates with **no pruning**, so the bench became a pile.
- Portfolio allocation existed (policies, overrides, quality reallocation) but had nothing
  multi-strategy to allocate across.

**Goal:** a self-managing daily loop that keeps the best 3–6 strategies live, continuously
re-tests them and the candidates behind them, swaps automatically with guardrails, and
prunes the bench so it never bloats.

## 2. Agreed design (decisions made with the user)

| Decision | Choice |
|---|---|
| Eligibility vs membership | Separate. Governance = *may* trade. Portfolio membership = *does* trade. |
| Active set size | **Dynamic with limits** (operator settings `min_active_strategies` / `max_active_strategies`, defaults 3 / 6) |
| Swaps | **Automatic**, validated by a rotation backtest before being trusted |
| `approved_research` state | Renamed **`candidate`** (done) |
| Optimisation objective | Recommended **portfolio Sharpe subject to a max-drawdown limit** (user unsure; tune in step 5) |
| Airflow | Last step; user said not yet |

### Tiered pool

```
research ──► BENCH ──► ON-DECK ──► ACTIVE
               ▲          ▲           │
               │          └───────────┘  swapped out → back to on-deck (still tracked)
            pruned ──► RETIRED
```

| Tier | Size | How it is tested | Purpose |
|---|---|---|---|
| ACTIVE | 3–6 | Real (paper) fills, own sleeve | Earning money |
| ON-DECK | ~10–15 | Shadow-traded on the same daily flow (simulated fills, no capital) | Forward track record; **only on-deck may swap into active** |
| BENCH | capped ~25–30 | Batch re-simulated on a recent window (weekly/monthly) | Cheap evidence refresh; feeds on-deck |
| RETIRED | — | Not tested | Out |

Membership statuses `on_deck` and `bench` are already reserved in
`contracts/governance/portfolio_membership.py` (`MembershipStatus`).

### Evidence weighting (one ranking across the whole pool)

```
live paper fills  >  shadow forward results  >  recent re-simulation  >  original approval backtest
   (actives)            (on-deck)                  (bench)                 (fades over time)
```

A fresh candidate with a great backtest cannot jump ahead of an active with months of real
results. It must earn on-deck and build a forward record first.

### Four lenses (the scorecard)

| Lens | Question | Existing building blocks |
|---|---|---|
| Against itself | Performing like its backtest said, or decaying? | `StrategyHealthLifecycleService`, `LivePerformanceMetricsService`, `SimulationVsPaperComparisonService` |
| Against the portfolio | Earning its allocation? Return per $, risk share, correlation with other actives? | `CorrelationMonitoringService`, `RiskBudgetingService` |
| Against the bench | Would a candidate (esp. same family) do better in this slot? | `StrategyRegistry` family metadata, research pipeline re-sim |
| Against the market | Does the current regime favour or hurt it? | Regime classification, `StrategyRegimeProfile` (`research/analysis/regimes/strategy_regime_profile.py`) |

### Cadence

| Frequency | What runs | May change the portfolio? |
|---|---|---|
| Every cycle / daily | Per-strategy P&L, drawdown, health (live data) | Protective only: shrink weight / suspend on breach |
| Weekly | Re-sim actives + bench on a recent window, rebuild scorecards, re-weight | Weights only, if the change is meaningful |
| Monthly | Full review incl. bench comparison | Swaps, with guardrails |
| Event-triggered | Regime change, drawdown-ladder rung, health CRITICAL | Pulls the review forward; never bypasses guardrails |

### Swap guardrails (prevent churn)

- Challenger must beat the incumbent **by a margin, over several consecutive reviews**.
- **Minimum tenure** in a slot before a strategy can be swapped out.
- **Cap on swaps** per period.
- **Turnover cost** counted against the benefit.

### Principle: separate "which" from "how much"

- **Selection** (who is active) = the new review logic (scorecards, challenger vs incumbent).
- **Sizing** (weights) = existing code: `QualityBasedReallocationService`, `RiskBudgetingService`,
  drawdown scaling. The review picks the set; allocation weights it.

---

## 3. Roadmap and status

| Step | Scope | Status |
|---|---|---|
| **1** | Foundation: dynamic active set, multi-strategy trading cycle, per-strategy P&L (sleeves), `candidate` rename | ✅ **Done** 2026-09-28 |
| **2** | On-deck shadow tracking | ⏳ Next |
| **3** | Bench management | Planned |
| **4** | Portfolio review (scorecard, auto decisions) | Planned |
| **5** | Rotation backtest mode (tune thresholds) | Planned |
| **6** | Airflow schedules (daily / weekly / monthly) | Planned — **user said not yet** |

---

## 4. Step 1 — what exists now (build on this)

Full detail: `docs/roadmaps/portfolio-rotation-step1-multi-strategy.md` (includes "Outcome").
Commits on branch `feat/multi-strategy-portfolio` (not pushed, no PR):

```
3f10c58cb Compute per-strategy live metrics from sleeves at replay time
3208efb66 Run every active strategy in the trading cycle (portfolio mode)
83c277002 Add active portfolio set with limits and budgets
864c23921 Add per-strategy sleeve ledger
fe1c136e2 Fix ledger cash forking, stub exposure checks and replay actor_role
479ce3d0e Rename approved_research governance state to candidate
```

### Key components

| Component | Where | What it does |
|---|---|---|
| Portfolio mode switch | `operator_settings.portfolio_mode_enabled` (default **off**) | Off = legacy single-strategy cycle, unchanged |
| Sleeves | `execution/services/strategy_sleeve_ledger_service.py`, tables `strategy_sleeve_{positions,ledger,snapshots}` | Per-strategy positions, cost basis, P&L; internal crosses; reconcile vs account; `__unattributed__` sleeve |
| Active set | `application/services/active_portfolio_service.py`, tables `portfolio_memberships`, `portfolio_membership_transitions` | Eligibility, selection (placeholder: incumbents keep seats), budgets, wind-down |
| Portfolio cycle | `scheduler/jobs/portfolio_evaluation.py` (called from `run_trading_evaluation_job`) | Per-strategy evaluation/sizing vs own sleeve, budget cap, crossing, sell clamp |
| Sleeve snapshot job | `scheduler/jobs/run_sleeve_snapshot_job.py` | End-of-cycle valuation + sleeve/account invariant check |
| Crossing | `execution/services/sleeve_crossing_service.py` | Opposing orders between sleeves transferred internally |
| Per-strategy live metrics | `LivePerformanceMetricsService` (sleeve equity curve + sleeve sells), `refresh_monitored(now)` | Persisted before health evaluation, at replay time in backtests |
| As-of time | `now=` on `AutoPromotionService.run`, `StrategyHealthLifecycleService.run`, `QualityBasedReallocationService.rebalance`, `ActivePortfolioService.refresh`, `build_trading_cycle_dependencies(now_utc=)` | Backtests use the replay tick, not the wall clock |
| Blended quality score | `QualityBasedReallocationService.blended_quality_score(strategy_id, now=)` | alpha·live + (1−alpha)·backtest; shared by selection and reallocation |

### Placeholder that Step 4 replaces

`ActivePortfolioService.refresh()` selection is deliberately simple: **incumbents keep their
seat**; open seats (ineligible incumbent, lowered max) go to the highest blended-quality
eligible strategies. It never swaps a healthy incumbent for a better challenger. That
decision belongs to the Step 4 review.

---

## 5. Remaining steps — how to start each one

Every step follows the same working agreement (see §6). Start each with a short discovery
pass, write a step doc `docs/roadmaps/portfolio-rotation-stepN-<name>.md` with sub-steps and
verification gates, get user sign-off, then implement sub-step by sub-step.

### Step 2 — On-deck shadow tracking

**Goal:** the ~10–15 most promising candidates run through the *same daily flow* as the
actives, with simulated fills and no capital, building a forward (post-approval) track record
that is directly comparable to the actives' real results.

**Scope sketch:**
- `ON_DECK` membership status (already reserved) + limits in operator settings (e.g.
  `max_on_deck_strategies`).
- Each cycle, evaluate on-deck strategies alongside actives, size them against a *notional*
  budget, and book the results into **virtual sleeves** with simulated fills at the bar price
  and the same cost/slippage model. **No broker orders.**
- Virtual sleeves must be clearly separated from real ones (e.g. a `sleeve_kind` column or a
  separate table), so they never enter the sleeve/account invariant, crossing, or real P&L.
- Live-metrics for on-deck come from virtual sleeves (evidence tier "shadow forward").
- Promotion path: bench → on-deck by rank (Step 4 decides; Step 2 can seed from `candidate`
  strategies for testing).

**Reuse assessment (checked 2026-09-28):**
- `ShadowRuntimeValidationService` (`application/services/shadow_runtime_validation_service.py`)
  is a **divergence-comparison framework** (shadow vs live across 7 dimensions), not a virtual
  trading engine. It is probably *not* the base for on-deck; at most reuse its divergence ideas.
- The **sleeve ledger + `portfolio_evaluation.py` path is the better base**: same evaluation,
  sizing and accounting, with a simulated-fill executor instead of order submission. The
  simulated execution used by the backtester (`SimulatedExecutionService`,
  `VolumeShareSlippageModel` in `platform_replay/runtime_hooks._build_simulated_broker_client`)
  is the natural fill model.

**Open questions to settle in the step doc:**
- Notional budget per on-deck strategy (equal share of a virtual pool, or mirror an active's budget?).
- Does on-deck count toward order throttles? (Should not; no broker orders.)
- Storage: extend sleeve tables with a kind, or separate `virtual_sleeve_*` tables?

**Verification gate idea:** a replay where 2–3 on-deck strategies run beside actives; virtual
sleeves accrue P&L; zero broker orders from on-deck; real sleeve/account invariant unaffected;
on-deck live metrics differ per strategy and are persisted at replay time.

### Step 3 — Bench management

**Goal:** the candidate pool stays small, diverse and current.

**Scope sketch:**
- **Grouping:** cluster candidates by strategy family + return correlation; keep one
  champion (maybe one alternate) per group; retire the rest as redundant.
  Reuse `research/intelligence/strategy_clustering_service.py` (TASK-2.5: deterministic
  hierarchical clustering; already detects duplicates / parameter spam) and registry family.
- **Admission gate:** new research output is admitted only if it is *novel* (not highly
  correlated with any group champion) or *better* (beats its group champion, which it then
  replaces). Today research only de-duplicates within a single run
  (`platform_replay/research_hooks.py`, "De-duplicate parameter-spam clusters") — not against
  the existing bench.
- **Cap:** when full, a newcomer must beat the weakest candidate, which is retired.
- **Relevance decay / expiry:** score decays unless refreshed; retire on repeated
  sub-bar re-sims, no on-deck promotion within N months, stale evidence; regime-dependent
  candidates may be demoted a tier rather than retired.
- **Batch re-simulation:** re-run bench strategies on a recent window with fixed configs (no
  generation). `_replay_strategy_set` in `research_hooks.py` already runs a fixed strategy set
  for smoke runs — a starting point. Tag results with a re-test lineage so they are distinct
  from research metrics (`MetricLineageService` exists).

**Evidence from Step 1F:** `mean_reversion_v1` and research `mean_reversion__1ef8…` produced
identical P&L; `momentum_v1` and `momentum__795c…` produced identical positions. The duplicate
problem is real and visible in the current pool.

### Step 4 — Portfolio review

**Goal:** one ranking across ACTIVE + ON-DECK + BENCH using the four-lens scorecard and the
evidence weighting; automatic decisions with guardrails.

**Scope sketch:**
- Scorecard contract (per strategy, same shape for every tier) + evidence-tier weighting.
- Decisions: swap (on-deck → active, active → on-deck), promote bench → on-deck, retire,
  re-weight. Replace the placeholder selection in `ActivePortfolioService.refresh()`.
- Guardrails from §2 (margin over N reviews, min tenure, swap cap, turnover cost).
- Weights still come from existing allocation code (restricted to ACTIVE members already).
- Full audit trail (membership transitions table exists; extend with review evidence).
- Consider shipping in **advisory mode first** (records what it *would* do) before enabling
  automatic decisions — the user chose automatic swaps, but advisory output is cheap and is
  what Step 5 tunes against.

### Step 5 — Rotation backtest mode

**Goal:** run months of history with Steps 1–4 active and check the system picks, rotates and
prunes sensibly; tune thresholds (review cadence, challenger margin, tenure, caps, objective).

**Starting point:** `fixtures/platform/replays/medium/portfolio_rotation_pool.yaml` (6
strategies, 5 seats, ~3 months, mid-run demotion) — extend with on-deck/bench pools and research
enabled. Compare objectives (portfolio Sharpe vs return vs drawdown-constrained) on the same pool.

### Step 6 — Airflow schedules (user: not yet)

Today Airflow only has ingestion and trading DAGs (`scheduler/airflow/dags/`). Governance,
rebalance, health, risk budgeting and the ladder run only inside the backtester. Add daily
(health, drawdown, live-metrics refresh), weekly (re-sim, scorecards, re-weight) and monthly
(review, research) DAGs.

---

## 6. Working agreement (how the user wants to work)

- **Verify each sub-step before moving on.** Each sub-step has a gate; report results
  honestly, including pre-existing failures and flaky tests.
- Keep a session **contained until a phase is done**; the user clears between phases.
- Plan doc per step with sub-steps + gates → user sign-off → implement.
- Gates typically include: new unit tests, full backend suite, `pre-commit` (ruff,
  ruff-format, mypy), migration upgrade → downgrade → upgrade on dev Postgres, a
  real-Postgres smoke with rollback where storage changes, and a platform backtest.
- Commit only when asked; split into a few logical commits; **no push / no PR** unless asked
  (work stays on `feat/multi-strategy-portfolio`).
- Safety-layer changes (`safety/`, pre-trade, throttles) are the user's call: explain the
  problem and the proposed fix and ask before changing them.

## 7. Operational reference & gotchas

**Running a platform backtest (wipes backtest tables in the dev DB):**

```bash
python scripts/reset_backtest_state.py
rm -f artifacts/platform/backtests/<name>.checkpoint.json   # otherwise it silently resumes and skips every tick
atp platform backtest run --fixture <fixture.yaml> --output artifacts/platform/backtests/<name>.json
```

- A run is ~30 s per trading day for 8 symbols × 6 strategies.
- Check the artifact's `errors` and `timeline_events_applied` — a run can "finish" with
  `completed_with_errors` (e.g. a rejected timeline event).
- Portfolio-mode fixtures must set `portfolio_mode_enabled: true`, and
  `portfolio_drawdown_action: warn_only` + `portfolio_drawdown_recovery_mode: auto_resume`
  (otherwise a drawdown breach halts the run).
- Governance timeline events need `actor_role` when the transition requires it (demotion to
  `candidate` needs `system_risk`/`admin`).
- New tables for later steps must be added to `scripts/reset_backtest_state.py` (children
  before parents) or state leaks between runs.

**Useful verification queries (1F review):** membership transitions ordered by time;
intents/fills/cross legs per strategy; fills without a sleeve entry (must be 0); sleeve
market value ÷ allocated capital per snapshot (budget adherence); summed sleeve quantities vs
latest account position snapshot (invariant); sum of latest sleeve `net_pnl` vs
cash + marked holdings − starting cash (must match exactly); live-metric snapshots per
strategy with replay-time `computed_at`; run manifests grouped by `last_successful_step` /
`error_message` (every day should reach `risk_snapshot`).

**Before enabling portfolio mode on the live paper account:**
- Raise `MAX_ORDERS_PER_BAR` (currently 2 in `.env`) and `MAX_ORDERS_PER_HOUR` to roughly
  strategies × symbols per bar.

**Known gaps carried out of Step 1** (details in the Step 1 doc):
- `MAX_GROSS_EXPOSURE` / `MAX_DAILY_NOTIONAL_TRADED` still use per-order semantics; enforcing
  them on real aggregate state needs limits re-based on equity.
- Account-ledger cash-snapshot `equity` marks untouched positions at their last fill price;
  sleeve snapshots mark to market (use sleeves for P&L).
- Per-symbol caps are checked per order against start-of-cycle state; several strategies
  buying the same symbol in one cycle are not aggregated intra-cycle.
- Quality-score history is only written by the reallocation service, which the replay does
  not run, so the health lifecycle's quality-decline signal is thin in backtests (relevant to
  Step 4's "against itself" lens).
- Pre-existing flaky tests seen during Step 1: a DuckDB stack overflow in
  `test_paper_trading_golden_path.py` and an intermittent pyarrow failure in
  `test_operator_paper_trading_passthrough.py` (both pass in isolation).
