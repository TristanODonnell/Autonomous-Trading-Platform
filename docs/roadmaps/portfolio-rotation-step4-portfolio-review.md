# Portfolio Rotation — Step 4: Portfolio Review

Status: implemented and verified (2026-09-29) — see "Outcome" at the end
Parent plan: `docs/roadmaps/portfolio-rotation-plan.md` (§5 Step 4). Builds on Steps 1–3.
Steps 5–6 (rotation backtest tuning, Airflow) are out of scope.

## Goal

One ranking across ACTIVE + ON_DECK + BENCH, built from a per-strategy scorecard that
weights evidence by quality (live > shadow > recent re-sim > fading approval backtest).
The review decides **who** is in each tier, including automatic swaps behind guardrails.
Existing allocation code decides **how much**.

## Current state (verified in code)

| Area | Today | Relevance |
|---|---|---|
| Selection | `ActivePortfolioService.refresh()` runs **every trading cycle**: incumbents keep seats, open seats go to highest blended quality; same for on-deck | Placeholder the review replaces. Protective exits (ineligible, suspended, wind-down) stay |
| Live evidence | `LivePerformanceMetricsService.compute_for_strategy` (sleeve equity curve) | Forward evidence for ACTIVE |
| Shadow evidence | `get_latest_shadow` (shadow sleeves) + `blocked_order_count` on shadow snapshots | Forward evidence for ON_DECK; blocked orders to be penalised (Step 2 decision) |
| Re-sim evidence | `BenchReviewService` re-simulates **every tier** on one shared window weekly; scores in `bench_evaluations`, daily returns in memory | Recent evidence for all tiers + returns for the correlation lens |
| Approval backtest | `QualityBasedReallocationService._latest_metrics` (excludes bench re-sims) | Oldest evidence; fades with age |
| Scoring | `metrics_quality_score()` shared by backtest / live / re-sim | One scale for every source |
| Maturity | `compute_alpha(days, trades)` | Weight of forward evidence |
| Health | `StrategyHealthLifecycleService` state (SUSPENDED already blocks eligibility) | Read only, no new health logic |
| Governance | Only `approved_for_paper/live` hold capital; `approved_for_paper` allowed for `risk_manager`/`system_risk`/`admin`; on-deck candidates are `candidate` | Swap-in of a candidate needs a promotion (decision 2) |
| Weights | `QualityBasedReallocationService.rebalance()` writes auto overrides; `budgets()` honours them; restricted to ACTIVE members; **not run in the replay** | Re-weighting = call it, no new sizing logic |
| Retirement | `BenchReviewService` (`system_bench`, candidates only) | The review never retires; it demotes a tier |

## Decisions (agreed with the user 2026-09-28)

1. **Mode** `portfolio_review_mode` = `off` (default) / `advisory` / `auto`. Advisory records
   scorecards and what it *would* do; auto applies it. Off = Step 3 behaviour.
2. **Swap-in of a candidate:** winning the challenger comparison is the approval. The review
   promotes `candidate → approved_for_paper_trading` itself via a new system role
   **`system_portfolio`** (allowed from `candidate` only), provided the challenger has a
   shadow record of **≥ 20 days and ≥ 10 trades**.
3. **Cadence:** runs weekly, right after the bench review (reuses its shared-window
   re-sims). Scorecards, streak counters, bench ↔ on-deck moves and re-weighting run weekly;
   **active swaps only on the monthly review** (first review ≥ 28 days after the last
   swap-eligible review). Daily protection stays with health / drawdown code.
4. **Evidence weighting = maturity blend:**
   ```
   forward  = live score (ACTIVE) or shadow score (ON_DECK); none for BENCH
   w_f      = compute_alpha(days, trades)            (shadow: × 0.9 confidence)
   w_bt     = (1 − w_f) · 0.5 · 0.5^(backtest_age_days / 90)
   w_rs     = (1 − w_f) − w_bt
   evidence = w_f·forward + w_rs·resim + w_bt·backtest   (missing sources renormalised)
   score    = evidence − decay − health − correlation − blocked
   ```
5. **Guardrails (moderate defaults, all settings):** challenger beats the incumbent's score
   by **≥ 10 %** on **3 consecutive weekly reviews**; incumbent tenure **≥ 30 days**;
   **≤ 1 swap** per monthly review; turnover cost = incumbent sleeve value × **20 bps**
   round trip ÷ total capital, deducted from the challenger's edge.
6. **Lenses:**
   - *Against itself:* decay penalty when forward evidence falls well below the
     backtest/re-sim expectation, plus health penalties (DEGRADED/CRITICAL) read from the
     existing lifecycle.
   - *Against the portfolio:* correlation penalty from shared-window re-sim returns against
     the **other** actives (for a challenger, the actives excluding the incumbent it would
     replace), so a challenger that diversifies the portfolio gets credit for the slot.
   - *Against the bench:* the challenger comparison itself.
   - *Against the market:* the current regime label is **recorded** on each scorecard;
     scoring it is Step 5.
7. **Scope of decisions:** active swaps; bench → on-deck by rank (replaces placeholder
   on-deck selection); dynamic set size (add a seat only for a challenger above an absolute
   floor, drop an active below the floor down to `min_active`); weekly re-weight by calling
   `QualityBasedReallocationService.rebalance()`. No logic duplicated from governance,
   health, drawdown, bench retirement or allocation.

Technical calls (flag if you disagree):
- **Swapped-out active** → WINDING_DOWN (sells out, as today), then ON_DECK when flat
  (still tracked, may earn its seat back). Approved strategies stay approved.
- **Demoted on-deck candidate** → BENCH (the bench review may prune it later).
- **Vacancies between reviews** (an active turns ineligible mid-week): in `auto`,
  `refresh()` fills the seat from the latest scorecard ranking among already-approved
  eligible strategies; it never promotes a candidate (only the review does).
- **Streaks** are derived from the stored decision rows of previous reviews (no separate
  counter state), so they survive restarts and are auditable.
- The review needs re-sim evidence, so it only runs when `bench_management_enabled` is on
  (otherwise it skips with a reason).
- Starting values for the lens and penalty weights (tuned in Step 5): shadow confidence
  0.9, backtest half-life 90 d, decay 0.25 · w_f · max(0, expected − forward), health
  DEGRADED −0.10 / CRITICAL −0.25, correlation 0.5 · max(0, mean ρ − 0.3), blocked
  0.25 · blocked / (blocked + shadow trades), score floor 1.0 (neutral), on-deck min
  tenure 21 days, bench → on-deck margin 10 % on 1 review.

## Work breakdown and verification gates

### 4A — Contracts, settings, storage, governance role
- Contracts `contracts/governance/portfolio_review.py`: `PortfolioReviewMode`,
  `EvidenceComponent`, `Scorecard`, `ReviewDecisionType`
  (`swap`, `add_seat`, `drop_seat`, `promote_on_deck`, `demote_on_deck`, `keep`, `challenge`,
  `reweight`), `ReviewDecision`, `PortfolioReviewResult`.
- Operator settings: `portfolio_review_mode`, `review_swap_margin`, `review_swap_consecutive`,
  `review_min_tenure_days`, `review_max_swaps_per_review`, `review_swap_interval_days`,
  `review_turnover_cost_bps`, `review_min_shadow_days`, `review_min_shadow_trades`,
  `review_score_floor`, `review_on_deck_min_tenure_days`. Migration + CLI/replay keys.
- Tables: `portfolio_reviews` (header: as_of, mode, swap-eligible flag, window),
  `portfolio_scorecards` (per review × strategy: tier, each evidence source's score and
  weight, penalties, final score, rank, regime label), `portfolio_review_decisions`
  (type, strategy, counterpart, margin, streak, guardrail results, applied flag, reason).
  `portfolio_membership_transitions.review_id` (nullable) links membership changes to review evidence.
- Governance: `system_portfolio` may target `approved_for_paper_trading` from `candidate` only.
- `scripts/reset_backtest_state.py` gets the new tables (children first).
- **Gate:** unit tests, full suite, pre-commit, migration upgrade → downgrade → upgrade on
  dev Postgres.

### 4B — Scorecard
- `PortfolioScorecardService`: gathers evidence per strategy (live / shadow / re-sim /
  backtest + age), applies the maturity blend and the lenses, and ranks all tiers together.
  Takes re-sim outcomes from the bench review when available and re-simulates only when
  called standalone.
- **Gate:** tests per rule (blend weights per tier, missing sources renormalised,
  backtest fades, shadow discount, decay, health, correlation excludes the incumbent,
  blocked-order penalty), full suite, pre-commit.

### 4C — Decision engine
- Pure function: (scorecards, memberships + tenure, prior decision rows, settings, swap-eligible flag) → decisions.
  Rules: challenge streaks, swaps (margin incl. turnover cost, streak, tenure, cap, shadow
  minimum), add/drop seat (floor, min/max), bench ↔ on-deck (margin, on-deck tenure, cap).
- **Gate:** tests per rule and per guardrail (each one blocks a swap on its own), full
  suite, pre-commit.

### 4D — Review service and application
- `PortfolioReviewService.run(now, window, resim_outcomes=None)`: scorecards → decisions → persist.
  In `auto`: governance promotion (`system_portfolio`), membership changes via
  `ActivePortfolioService.set_status` carrying `review_id`, then `rebalance()` for weights.
  In `advisory`: nothing applied.
- `ActivePortfolioService.refresh()`: when mode is `auto`, no placeholder upgrades (open
  seats and on-deck picks belong to the review); protective exits and wind-down unchanged;
  vacancies filled from the latest scorecard among approved strategies.
- **Gate:** tests (advisory changes nothing; auto applies; promotion role restricted;
  refresh no longer swaps or selects; vacancy fill; transitions carry review_id; re-weight
  called), full suite, pre-commit.

### 4E — Replay wiring and verification backtests
- `portfolio_review` runs in the bench hook's tick, right after the bench review, and
  reuses its re-sims; the monthly swap-eligible flag comes from the stored review history.
- Slim fixture: 2–3 actives, on-deck with a clearly stronger challenger, bench, ~3 months
  so a swap can clear 3 weekly reviews + tenure. Run **advisory**, then **auto**.
- **Gate:** advisory run: scorecards every review, would-swap decisions, zero membership
  changes from the review. Auto run: the challenger promoted by `system_portfolio` and
  swapped in after the streak; the incumbent winds down → on-deck; ≤ 1 swap per month;
  bench ↔ on-deck moves with reasons; weights re-balanced; sleeves = account; 0 errors.
  Then update the Outcome section, the master plan and memory.

## Progress notes

### 4A (done 2026-09-28)
- Contracts `contracts/governance/portfolio_review.py` (mode, forward source, decision
  types incl. `keep` for below-floor holds, `Scorecard`, `ReviewDecision`, result).
- 11 operator settings (defaults above), migration `ww11xx22yy33` (settings,
  `portfolio_reviews`, `portfolio_scorecards`, `portfolio_review_decisions`,
  `portfolio_membership_transitions.review_id`), `PortfolioReviewRepository`,
  `set_status(..., review_id=)`, CLI/replay keys, reset script.
- Governance: `system_portfolio` may target `approved_for_paper_trading` from `candidate`
  only. **Promotion rules still apply** (agreed with the user): the review's forward
  evidence is a second gate on top of the backtest promotion rule, not a replacement.
- Gate: 8 new tests; suite 4766 passed, 5 skipped, 1 xpassed; pre-commit clean;
  migration upgrade → downgrade → upgrade on dev Postgres.

### 4B–4D (done 2026-09-28)
- 4B `PortfolioScorecardService` + pure `build_scorecards` / `evidence_weights` /
  `correlation_penalty`; `ScorecardSet.score_for_slot` re-scores a challenger without
  the incumbent it would replace. Backtest age = days since the strategy first entered
  the portfolio pool (approval metrics carry wall-clock timestamps in backtests).
  `QualityBasedReallocationService.backtest_quality_score()` added. 17 tests.
- 4C `portfolio_review_decisions.decide` (pure). Streaks are per challenger (beat *some*
  incumbent on consecutive reviews); the swap targets the incumbent with the largest
  edge that passes tenure. Turnover cost = 1.5 × invested fraction × bps (return drag
  in score units — small at 20 bps; the margin does most of the work). 23 tests.
- 4D `PortfolioReviewService` (off / advisory / auto; apply → promotion, membership
  with review_id, re-weight via `rebalance()`); a rejected promotion cancels that move
  and is recorded (`…:governance_rejected`). `refresh()` in auto mode after the first
  review: no placeholder selection; protective vacancies filled from the latest
  scorecards (approved only); finished wind-downs return on-deck. 12 tests.
- Gate: suite 4818 passed, 5 skipped, 1 xpassed; pre-commit clean.

### 4E (done 2026-09-29)
- Portfolio review runs in the bench hook right after the bench review (bench review
  committed first, so a failed portfolio review rolls back only itself), reusing
  `BenchReviewService.last_outcomes`; regime label = on-the-fly `trend/volatility` at the
  window end (recorded only).
- Found: the dev DB and backtests have **no promotion rules**, and fixture-seeded
  candidates have no source run, so every candidate promotion would be refused.
  Fixtures can now seed `initial_state.promotion_rules` (upsert by rule_id); research
  candidates carry their research run as source run.
- Fixtures `portfolio_review_advisory.yaml` / `portfolio_review_auto.yaml` (identical
  except the mode; 2024-01-02 → 03-22, 6 symbols, min/max active 2/3, on-deck 2, bench
  cap 4, research smoke monthly, weekly bench + review; ~40 min each).

**Advisory run** (0 errors): 11 reviews (weekly + after each research tick), 21
decisions, **0 applied**, 0 membership transitions attributed to the review, governance
unchanged. Would-swap `composite_rule__145e…` → `macd_crossover_v1` on 2024-02-19 and
`mean_reversion__7932…` → macd on 03-18; second challenger held by the swap cap.

**Found by the advisory run (fixed):**
- *Streak inflation.* The extra review right after a research tick counted toward the
  streak, so "3 consecutive weekly reviews" could be met in 7 days (Jan 29 → Feb 1 →
  Feb 5). Now a review within 6 days of the next counted one neither counts nor breaks
  a streak (`STREAK_MIN_GAP_DAYS`). Regression test added.

**Auto run** (0 errors; with the streak fix):
- Jan 22: `composite_rule__145e…` bench → on-deck; Feb 1: research candidate
  `mean_reversion__7932…` admitted to the bench and moved on-deck the same review.
- Streaks now count weekly: composite 1 (Jan 29), 1 (Feb 1, extra), 2 (Feb 5), 3 (Feb 12).
- Feb 19 (monthly): swap `composite_rule__145e…` → macd **refused by governance**
  (`…:governance_rejected`, no source run); nothing moved.
- Mar 18 (monthly): `mean_reversion__7932…` (streak 4, shadow ≥ 20 days) **promoted by
  `system_portfolio`** (its research run passed the seeded rule; governance updated at
  replay time) and swapped in; macd was flat, so it went straight to on-deck. Second
  challenger held by the cap. Transitions carry `review_id` and replay timestamps.
- The new active traded (5 fills after the swap); sleeve positions = account positions
  per symbol; 0 fills without a sleeve entry.

**Found by the auto run (pre-existing, fixed):** `QualityBasedReallocationService`
wrote `completed_at` with the wall clock while `started_at` used the as-of `now`, so in
a backtest the first completed rebalance was stamped 2026 and the 24 h interval guard
skipped every later one (re-weight ran only on Jan 22). `completed_at` now uses `now`
(same as before live, where `now` defaults to the wall clock). Regression test added.
Auto run repeated to confirm weekly re-weighting — see Outcome.

## Outcome (2026-09-29)

All sub-steps 4A–4E implemented and verified. The review is behind
`operator_settings.portfolio_review_mode` (default **off**: Step 3 behaviour). Not
committed yet.

### Verification
- Full backend suite (non-integration) after each gate: 4766 → 4818 → 4825 → 4826
  passed, 5 skipped, 1 xpassed, 0 failures. pre-commit clean. Migration `ww11xx22yy33`
  upgrade → downgrade → upgrade on dev Postgres.
- Advisory backtest: 0 errors; 21 decisions, 0 applied; no review-attributed membership
  or governance change.
- Auto backtest (repeated after the rebalance fix; ~35 min): 0 errors.
  - Candidate without a source run refused by governance at its swap (Feb 19); research
    candidate promoted by `system_portfolio` and swapped in at the next monthly review
    (Mar 18); incumbent (flat) to on-deck; second challenger held by the swap cap.
  - Weekly re-weight: rebalance ran every review (completed Jan 22 with 3 changes; no-op
    below the change threshold after; completed Mar 18 giving the new active its weight).
  - Sleeves = account per symbol; 0 fills without a sleeve entry; the new active traded.

### Changes beyond the plan
- Fixture support for `initial_state.promotion_rules` (the dev DB and backtests had no
  promotion rules, so no candidate could ever be promoted). The fixture rule
  `fixture_candidate_to_paper` stays in the dev DB after a run (the reset script does not
  clear `promotion_rules`).
- Streaks count one review per week (`STREAK_MIN_GAP_DAYS = 6`).
- Pre-existing: `QualityBasedReallocationService` stamped `completed_at` with the wall
  clock, blocking every later as-of rebalance in backtests.

### Known gaps / follow-ups (Step 5 candidates)
- A swapped-out strategy keeps its last auto allocation override (harmless: budgets
  only read ACTIVE members' overrides; it is replaced at the next rebalance if it
  returns).
- Not exercised by the backtest (covered by unit tests): add seat (all seats were
  filled at bootstrap), drop seat, bench → on-deck exchange by margin, on-deck over cap,
  a swapped-out incumbent winding down before returning on-deck, protective vacancy fill.
- Penalty weights, floor, margins and streak length are starting values; the correlation
  lens inherits Step 3's market-beta caveat (long-only strategies).
- Regime label recorded, not scored.
- The swap targets the incumbent with the largest edge; a candidate refused by governance
  keeps winning the challenge every review (recorded each time) until its evidence changes.

## Out of scope
Regime-scored lens, threshold tuning / objective comparison (Step 5), Airflow (Step 6),
frontend, live-trading approval (`approved_for_live` stays human-only).
