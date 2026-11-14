# Research & Experiments Sprint — Pipeline Integrity and Strategy Generation

**Read this first before touching `research/` (pipeline stages, validation, checkpoints,
strategy generation) or the worker's research jobs.** It records what the research engine
does today, what we are changing, how each change is measured, and the numbers as they
land. Deployment state lives in `pre-launch-deployment-plan.md` (Phase 3.3(b), 3.4, Phase
4); portfolio governance (bench admission, tiers, review) lives in
`portfolio-rotation-plan.md`. This doc is the research-side companion to both.

**This sprint runs beside the deployment track, not after it.** Nothing in `research/` is
imported by Box A's trading loop, so every step here can be built, tested and merged while
the paper soak and the 3.3(a) flips continue. The only coupling points are the additive
pieces on A (Step 3's endpoint) and the worker timers (Step 5), and both stay inert until
switched on. Deployment findings keep going into the pre-launch plan; research findings
(numbered R1, R2, …) go here.

Last updated: 2026-11-14 (R1 built, verified on real bars and pushed, no PR yet; R1
follow-up and the small R2 set built, checked on real bars and committed on the same
branch, not pushed; the rest of R2 and R3–R9 not started).

**Companion backlog:** `interview-study-revisit-backlog.md` (2026-11-12) holds every defect
found while reading the codebase for interviews, tagged `[domain.section #item]`. The
research-side items from it are folded in below with their tags, so the tag is the link
back to the longer note. When one lands, tick it in both files and add a row to that
file's fix log.

---

## 1. Why this exists

The paper soak on Box A is stable (Phase 3.3(0), three clean sessions under the fixed
code) and the data plumbing the research engine needs has landed in the last week: the
63-session rolling bench window with the August–October backfill (Findings 14/15/26), the
nightly dataset pull to Box B, and the Saturday bench re-sim. That makes the generator the
next thing that limits how good the portfolio can get.

What the generator is today:

- **A template enumerator, not a search.** `composite_generation._build_skeletons` combines
  indicators, rule types, aggregators and optional filters from static domain tables into
  **652 composite skeletons** (thresholds with regime/volatility gates, same-family
  crossovers, cross-indicator comparisons, two-rule combinations). `grid` returns all of
  them, `random` draws `n_samples`, and `evolutionary` only flips the aggregator on the
  first `generations` templates: evolutionary in name only.
- **Per-family parameter sweeps** (`GridSearchGenerator`, `RandomSamplingGenerator`,
  `EvolutionaryGenerator`) over each strategy family's `ParameterSpec`s. The
  `EvolutionaryGenerator` is a deterministic mutation pass with no fitness feedback.
- **A real pipeline after generation:** simulation → walk-forward, overfitting, Monte Carlo,
  regime and stress gates (`research/pipeline/stages`), scoring
  (`experiments/filtering/scoring.py`), and within-run clustering that drops parameter spam
  (`intelligence/strategy_clustering_service.py`). Nothing feeds back into generation.
- **No admission against the live bench.** Dedup is within a run only
  (`portfolio-rotation-plan.md`, Step 3 "Admission gate").
- **Runs on demand only.** The worker has a nightly pull and a Saturday bench re-sim; there
  is no monthly research timer and no candidate hand-off to A (`pre-launch-deployment-plan.md`
  3.3(b)).

**Goal:** a generator whose output is measurably better than the template pool on a
held-out window, running monthly on Box B, handing admitted candidates to A's bench.

## 2. Ground rules (agreed with the user, 2026-11-06)

| Rule | Why |
|---|---|
| Nothing here may change Box A's paper record | A's trading loop never imports `research/`; keep it that way. New endpoints on A are additive and uncalled until the worker job exists. |
| `grid` stays the default method everywhere | Every existing fixture, experiment YAML and test keeps producing the same configs. New search lives behind the existing `evolutionary` method name (and new options) until it has earned the default. |
| Every generator change ships with a before/after measurement on the same bars | IEX bars make absolute scores untrustworthy; relative comparisons on identical data are fine, and that is all we need. |
| Holdout is never tuned on | The last ~15 sessions of the window are scored once per candidate set, after selection. |
| Research code is tested by the backtester, not by the soak | "Is the gen changing?" is answered by pipeline runs on B (or a laptop with pulled datasets), never by what A trades. |

## 3. Steps

Order matters. Step 0 comes first because it changes what the pipeline's scores mean: a
better generator scored by a vacuous Monte Carlo and leaky walk-forward folds would look
better than it is. Steps 1 and 2 are one track (the baseline is the "before" for the
generator change). Steps 3–5 are plumbing that follows once the generator produces
something worth handing over, and 3.3(a) on the deployment track gives us the hand-off
pattern to copy. Step 0c (checkpointing) is a cost/convenience fix and can slide to just
before Step 5.

### Step 0 — Pipeline integrity (audited 2026-11-07, fixes not started)

Three suspicions the user raised while studying the backtesting funnel, each confirmed by
reading the code (three parallel audits, key claims re-verified by hand). Fix these before
trusting any before/after number from Steps 1–2.

Order from here (proposed 2026-11-14, not yet agreed by the user):
1. One PR from `research/r1-monte-carlo-bootstrap` with three commits: R1 (pushed), the R1
   follow-up, the small R2 set with these notes. R2 stays open; the PR is "R2, partial".
   The real-bars check of the R2 change is done (under R2). Still before the PR: the
   low-spread question under "R1 verification", because a cause in the resampling would
   be an R1 defect and belongs in the same PR.
2. The longer-history data question (read-only first). It unblocks the selection / test
   split and holdout the user chose for R2, and Step 1 waits on R2.
3. R4, starting with the stress gate: in every real-bars run on 2026-11-14 it eliminated
   every strategy that reached it.
4. R5 and R6.
5. R3, before Step 5 (it changes cost, not what a score means).

**R1 — Monte Carlo is not a Monte Carlo (blocks trust in scores).**
`pipeline/stages/monte_carlo_stage.py:371-392` builds `n_runs` identical
`SimulationRunRequest`s whose only difference is `random_seed = base_seed + i`. The seeded
RNG is consumed only by the fill model's `partial_fill_probability`,
`order_rejection_probability` and `fill_probability_on_touch`
(`research/simulation/services/simulated_execution_service.py:183-237, 304`), and research
runs build the execution service through `build_platform_execution_service()`
(`execution/clients/simulated_broker_client.py:54-59`) with all three left `None`, so the
RNG is never drawn. Every trial is identical, `pass_rate` is exactly 0 or 1, and the
verdict equals "did the full-window simulation clear the same `FilterConfig` thresholds".
Tests pass because they feed hand-built results (Sharpe 2.0, `min_sharpe=-999`) or a numpy
toy that varies with the seed, and `experiments/staged/exp_mc_smoke_test.yaml` sets
thresholds its own comment calls "nearly impossible to fail". Also: a strategy whose trials
all return `None` is kept (`monte_carlo_stage.py:276-278`), and `_safe_*` in
`pipeline/aggregation/monte_carlo_aggregator.py:253-281` coerce a missing metric to 0.0,
which always passes the drawdown test.
- [x] Resample something real: block bootstrap of per-bar returns from the single
      full-window simulation (`n_runs` resamples, block length = one session). Built
      2026-11-14 with the gate form the user chose: `min_pass_rate` is the fraction of
      resampled paths that clear the Sharpe, return and drawdown thresholds, not explicit
      tail fields; P5/P95 of each metric are reported beside it. Stochastic fills
      (`SimulatedFillModelConfig` probabilities) stay a separate, opt-in knob: they
      measure execution noise, not strategy fragility.
- [x] Fail closed: a missing base run, an empty or too-short equity curve and a
      non-finite path metric all count as failures. (`n_runs_completed < n_runs` no longer
      exists: the resamples are computed in-process from one run.)
- [x] Seeds: the base run's seed and the resampling seed come from `derive_seed(...)`
      with window roles `mc_base` and `mc_bootstrap`, so they cannot collide with
      neighbouring experiments' base seeds **[7.10 #6]**. There are no per-trial seeds:
      one generator draws every session index up front.
- Alternatives weighed (**[7.10 #1]**, confirmed from saved Parquet rows): MC-only
  stochastic fills, `shuffle_timestamp`, per-run cost perturbation, block bootstrap.
  Bootstrap first because it leaves the simulator untouched (parity rule below); cost
  perturbation is the natural second axis and reuses the stress stage's
  `cost_multiplier` plumbing.
- [x] Tests with realistic thresholds, asserting `sharpe_dist.std > 0` and that the
      verdict can differ from the single-run filter's
      (`tests/research/pipeline/test_monte_carlo_stage.py`: curves with a chosen
      full-window Sharpe and metrics from the production functions). The real
      `SimulationRunner` on synthetic bars is exercised by
      `tests/research/test_research_stages_smoke.py`, with permissive thresholds only.
      The comment in `experiments/staged/ma_sweep_staged.yaml` is fixed.

**Verify (run 2026-11-14, results under "R1 verification" below):** on the rolling window B holds, run the current
six-stage pipeline (`research_hooks.py`: initial_quality_filter → walk_forward →
monte_carlo → regime → stress → overfitting) before and after; before, every strategy's
`sharpe_dist.std` is 0.0; after, it is not, and at least one strategy that entered Monte
Carlo fails it. (User, 2026-11-14: the six-stage pipeline is the current one;
`ma_sweep_staged.yaml` is the older four-stage layout, and its 2022–2025 window is not
on B, so it is not the verification target.)

**R1 design investigation (2026-11-14, code re-read plus a synthetic probe; nothing built).**
- Raw material exists: `SimulationRunResult.equity_curve` is the live (post-warm-up)
  per-bar curve, and `risk_metrics.py` computes Sharpe and drawdown from bar returns of
  exactly that frame. One simulation per strategy is enough; the stage runs `n_runs` today.
- Stages can see earlier results (`bind_prior_results`, `find_reusable_reference`); regime
  and stress already reuse a matching full-window run. Monte Carlo does not bind them yet.
- Three consumers read `sharpe_dist.coefficient_of_variation`, which is 0 today:
  `validation_orchestrator.py:297` (score `1 - cv`), `overfitting_analysis.py:224`
  (`cv / 2`) and `robustness_score.py:153` (`1 / (1 + cv)`, weight 0.20). So every strategy
  currently gets the maximum Monte Carlo stability score. With a real distribution the CV
  is unusable: it divides by the mean Sharpe and reached 12 in the probe for a strategy
  with mean near zero. These three must change in the same PR (overlaps R5).
- Probe (synthetic 5-minute returns, 15% annual vol, 2,000 session-block resamples):

  | Window | Bootstrap Sharpe std | Example: true Sharpe 2 |
  |---|---|---|
  | 63 sessions | 1.7 to 2.2 | P5 Sharpe −1.55, P(Sharpe > 0) 0.83 |
  | 252 sessions | about 1.0 | P5 Sharpe −0.04, P(Sharpe > 0) 0.95 |
  | 756 sessions | about 0.56 | (true Sharpe 1 draw) P5 1.90, P(Sharpe > 0) 1.00 |

  A "P5 Sharpe ≥ `min_sharpe`" gate is close to unpassable on 63 sessions (needs an
  observed Sharpe of about `min_sharpe` + 3.3). A probability gate is workable there.
- Block length: with AR(1) bar noise (φ = 0.2) the Sharpe std was 1.95 for single bars,
  2.39 for one-hour blocks, 2.72 for one-session blocks, 2.52 for one-week blocks. Single
  bars understate the spread by about 30%; one session is the right default.
- Reordering sessions without replacement cannot move Sharpe (std 0.0000) and only varies
  drawdown, so it is not a substitute.
- Cost: 2,000 resamples of a 63-session curve took 228 ms and 79 MB as one array. Longer
  windows need the resamples computed in chunks (756 sessions would be about 940 MB).
- Tests that encode "N simulations per strategy" and must be rewritten: five in
  `test_monte_carlo_artifact_identity.py`, one each in `test_parallel_monte_carlo_stage.py`
  and `test_monte_carlo_resume.py`. `research_hooks.py:599` builds the stage with
  `n_runs=3 if smoke else 5`, which must become a resample count.
- No storage model references the Monte Carlo distribution: it lives in the in-memory
  `StageDiagnostics` and the validation summary only.
- Proposed design (awaiting the user's go-ahead, nothing built):
  1. Reuse a matching full-window run from earlier stages, or run one simulation.
  2. Bar returns from its equity curve, grouped by session date (sessions have unequal
     bar counts: early closes, missing IEX bars).
  3. Draw `n_runs` paths of whole sessions with replacement from one generator seeded by
     `derive_seed`; draw the session-index matrix up front so chunking cannot change results.
  4. Sharpe, total return and max drawdown per path with the `risk_metrics.py` formulas.
  5. Fail closed: no base run, an empty curve or a missing metric eliminates the strategy.
  The simulator is untouched and `grid` stays the default.
- Open decisions for the user, with the recommendation given on 2026-11-14:
  1. Gate form. Recommended: keep `n_runs`, `min_pass_rate` and `filter_config`, with pass
     rate redefined as the fraction of resampled paths that clear the thresholds (a real
     probability; existing YAML stays valid), and report tail statistics alongside. The
     alternative is the new explicit tail fields in the checkbox above. Catch: old configs
     keep loading but mean something different.
  2. What replaces the CV in the three scorers. Recommended: P(Sharpe > 0), already in
     [0, 1] and stable near a zero mean.
  3. `min_trades`. Recommended: apply to the base run only (a resampled path has no trade
     count).
- Scope if approved: the stage, the aggregator, the stage config validators, the three
  scorers, `research_hooks.py:599`; the seven tests above plus new real-runner tests; the
  two staged YAMLs. Not verified: the probe is synthetic; the real before/after on our bars
  is the Verify step above and runs on B once the code exists.
- Full write-up of the investigation: `Claude outputs/r1-monte-carlo-investigation.md`
  (untracked).

**R1 implementation (2026-11-14, branch `research/r1-monte-carlo-bootstrap`, commit
`4dd0671ae`, pushed, no PR yet).**
- Decisions (user): (1) reuse `n_runs` / `min_pass_rate` / `filter_config`; (2) the three
  scorers read P(Sharpe > 0), with the Sharpe spread and P(Sharpe ≥ `min_sharpe`) reported
  but not scored; (3) `min_trades` on the base run only.
- Code: `pipeline/aggregation/session_block_bootstrap.py` (new, pure numpy),
  `monte_carlo_aggregator.py` (paths in, verdict out), `monte_carlo_stage.py` (one base
  run, reused via `find_reusable_reference` when an earlier stage recorded one), the three
  scorers, `stage_configs.py` (default `n_runs` 30 → 1000), `research_hooks.py`
  (`n_runs` 3/5 → 200/2000), both staged YAMLs.
- Differs from the investigation notes: `SimulationStage` records no `reference_result`,
  so in the usual order (simulation → walk-forward → Monte Carlo) there is nothing to
  reuse and the stage runs one simulation per survivor, where it ran `n_runs`.
- Behaviour changes to expect:
  - The replay hook's Monte Carlo gate (`min_sharpe` 0.3, `min_pass_rate` 0.6) used to
    repeat the single-run filter. It is now a 60% probability test on a window of at most
    90 days, so fewer candidates will reach regime, stress and the bench.
  - `mc_instability` is `1 − P(Sharpe > 0)`: about 0.5 for a strategy with no edge, where
    the old formula reached 1.0 at a coefficient of variation of 2. Whether to rescale it
    is open, and belongs with R5.
  - A resumed stage whose base run is skipped by a checkpoint now eliminates the strategy
    (it used to keep it). Nothing in production resumes; R3's rehydration fixes it.
  - (Found during verification, 2026-11-14; missing from this list when R1 was committed.)
    The gate applies `min_sharpe`, `min_total_return` and `max_drawdown` to each path and
    `min_trades` to the base run. `min_consistency_score`, `min_profit_factor`,
    `min_win_rate` and `max_daily_turnover` in the stage's `filter_config` are not
    enforced here. Corrected 2026-11-14: this note first said R1 had dropped them. It had
    not. `main`'s aggregator checked Sharpe, total return, drawdown and trade count on
    each run and nothing else (`_evaluate_runs`), so these four were never enforced at
    this stage on any version; the real-bars run on `main` shows it (below). The
    follow-up below adds the enforcement; it is new, not a restoration.
  - (Same.) The reasons on a Monte Carlo `FilterScoreOutput` are the base run's single-run
    filter failures, not the gate's own (`MonteCarloAggregation.failure_reasons`, which is
    only logged). `main` did the same; there the two verdicts could never differ. Fixed by
    the follow-up below.
- Checked: the identity path reproduces `risk_metrics.py` to 1e-9 relative; chunk size
  does not change results; research tests pass on the laptop. The Verify step on real bars
  is recorded under "R1 verification" below.

**R1 follow-up (2026-11-14, same branch, commit `0cf733584`; user: "yes this" to "check those four thresholds
on the real run, the same rule you chose for `min_trades`, and report the gate's own
reasons").**
- This is stricter than `main`, not a return to it: `main` never applied consistency,
  profit factor, win rate or turnover at this stage (see the corrected note above). The
  user approved it when the notes still said R1 had dropped those checks; told
  2026-11-14, reverting is a small change if the old leniency is wanted.
- Rule: `min_sharpe`, `min_total_return` and `max_drawdown` are checked on every resampled
  path. Every other threshold in the stage's `filter_config` is checked once, on the base
  run, and any failure eliminates the strategy whatever its pass rate. It is done by
  running the existing single-run filter on the base run with those three thresholds
  switched off (`base_run_filter_config` in `monte_carlo_aggregator.py`), so a threshold
  added to `FilterConfig` later is enforced here without a change to this stage.
- Reasons: a failed strategy's `FilterScoreOutput.filter_result.failures` is now the
  gate's own list. Base-run failures come first, prefixed `base run: `; then, when too
  few paths cleared the bar, a count such as `1199/2000 paths clear the thresholds, need
  60%` (a count because 59.95% prints as 60%); then each per-path threshold with the share
  of paths that missed it. A passed strategy has an empty list and the base run's score; a
  failed one has no score.
- `MonteCarloAggregator.aggregate` takes `base_run_failures` in place of
  `base_trade_count`; the stage is its only caller.
- Real bars (2026-11-14, the 30-strategy run under "R1 verification", working tree
  against `4dd0671ae`): all 30 verdicts and every Monte Carlo number are identical (3
  pass, 27 fail). No passed strategy carries a reason and no failed one lacks one. The
  near miss reads `1196/2000 paths clear the thresholds, need 60%`; the high-turnover
  strategy reads `base run: daily_turnover 13.77x > max_daily_turnover 10.0x` first. No
  verdict changed because every strategy that broke a base-run rule here also missed the
  pass rate. A share above zero that rounds to 0% prints as `<1%` (seen in that run as
  "on 0% of paths", fixed after it).
- Why it matters more after the R2 change below: stage 1 now selects on the first 45
  days, so "stage 1 already applied these on the same window" no longer holds in the hook.
- Tests: `test_monte_carlo_aggregator.py` (base-run failure with a full pass rate, both
  kinds of reason together, the count wording, only three fields differ in
  `base_run_filter_config`) and `test_monte_carlo_stage.py` (turnover, consistency, profit
  factor and win rate each fail a strategy whose paths nearly all pass; a near miss
  carries the count; a base run just under `min_sharpe` gets no base-run reason).

**R1 verification (2026-11-14, on the laptop, real bars).**
- Setup: B's rolling raw-bars version `raw_bars_20261113T230046Z_ce936880` copied to the
  laptop read-only (22 symbols, IEX five-minute bars, 2026-08-03 to 2026-11-13, 70
  sessions, 90,310 rows). This is the rolling version from before the 2026-11-14 repair:
  8/31, 9/30, 10/08 and 10/09 are absent, which is why the curves have 60 sessions and
  not 64. The repaired version is `raw_bars_20261114T041002Z_c2faf2e7` (74 sessions,
  99,118 rows). Both sides of every comparison here used the same data. The hook's own six-stage experiment definition for tick
  2026-11-13 (window 2026-08-15 to 2026-11-13; 60 sessions in the resampled curve).
  `main` at `413d16472` and the branch at `4dd0671ae`, side by side in two git worktrees,
  same harness (a pytest file kept outside the repo).
- What the harness is not: production. SQLite test database with the 22 symbols
  registered as the universe; a shortened experiment id (Windows path limit); no
  corporate-action rows (the data has no bar-to-bar gap over 25%); 30 strategies
  generated where the hook asks for 36 (not explained); run on the laptop, not on B.
- Hook as is. Funnel identical on both: 30 generated, 4 pass stage 1, 2 pass
  walk-forward, 2 of 2 pass Monte Carlo, 2 of 2 pass regime, 0 of 2 pass stress. No
  survivors on either.

  | Monte Carlo stage | `main` | branch |
  |---|---|---|
  | Simulations run | 10 (5 identical per strategy) | 2 |
  | Sharpe spread (std), strategy 1 / 2 | 0.00 / 0.00 | 1.54 / 0.95 |
  | Pass rate, strategy 1 / 2 | 1.00 / 1.00 | 0.81 / 0.67 |
  | P(Sharpe > 0), strategy 1 / 2 | not computed | 0.85 / 0.78 |

  Strategy 1: composite rule, base Sharpe 1.61. Strategy 2: mean reversion, base Sharpe
  0.70. The first half of Verify holds (spread 0 before, not 0 after). The second half
  does not show here: both strategies that reached the gate pass it.
- Upstream gates opened so that all 30 strategies reach Monte Carlo (stage 1 and
  walk-forward thresholds made permissive in the harness; the Monte Carlo thresholds are
  the hook's: `min_sharpe` 0.3, `min_pass_rate` 0.6, 2,000 paths):
  - Branch: 3 pass, 27 fail. Passes: base Sharpe 1.61 (rate 0.81), 0.96 (0.61), 0.70
    (0.67). Sharpe spread: 2.0 to 2.3 for 23 strategies, 1.54 for one, 0.47 to 0.95 for
    four with few trades (155 to 729), 0 for two that never traded. With a spread near 2,
    "60% of paths at or above 0.3" needs a base Sharpe of about 0.85; the 0.70 strategy
    passes because its spread is 0.95.
  - Open, not explained: the four low spreads. Independent session returns give a spread
    of about √(252/60) ≈ 2.05 whatever the trade count, so a spread under 1 means the
    bar returns of those curves are not independent within a session (or something else
    is off). It matters because a narrow spread makes the gate easier to pass at a low
    Sharpe, which is how the 0.70 strategy got through. To look at: those four equity
    curves. If the cause is in how the curve is cut into sessions or resampled, it is a
    defect in R1 and is fixed on this branch before the PR; if the bar returns really are
    dependent within a session, the gate is reading them correctly and the note stays as
    a property of those strategies.
  - After the gate in that run: 2 of the 3 pass regime, 0 of 2 pass stress. No survivors.
  - The two cases behind the follow-up above: base Sharpe 0.76 fails with 1,196 of 2,000
    paths (0.598, shown as 0.60) and its result carried no reason; base Sharpe 0.70
    trading 13.77× equity a day (cap 10×) fails on pass rate 0.58, and would not have
    failed on turnover.
  - `main`, same setup: 5 pass, 25 fail, from 150 simulations (the branch: 30). All 30
    strategies have a Sharpe spread of exactly 0 and a pass rate of exactly 0 or 1. Base
    Sharpes are identical on the two versions, strategy by strategy. After the gate: 3 of
    5 pass regime, 0 of 3 pass stress.
  - The two strategies `main` passes and the branch fails are the two cases above: base
    Sharpe 0.76 (1,196 of 2,000 paths) and base Sharpe 0.70 at 13.77× daily turnover.
    `main` passed the second with `daily_turnover 13.77x > max_daily_turnover 10.0x`
    listed on a result marked passed: its Monte Carlo stage never checked turnover. So
    the second half of Verify holds: strategies that entered Monte Carlo and passed on
    `main` fail on the branch.
  - Wrong prediction, recorded: before this run finished the expectation was 4 passes on
    `main`, on the belief that the old stage enforced turnover. It did not.
- Not verified: anything on B itself; the same comparison on a window longer than 60
  sessions.

**R2 — Walk-forward test folds are not out of sample (blocks trust in scores).**
- Selection leakage: in `ma_sweep_staged.yaml` the `intermediate` simulation stage ranks
  configs over 2022-01-01..2025-03-31 and `heavy_walk_forward` runs over the identical
  range, so every test fold lies inside the window the survivors were picked on.
- No training happens: `walk_forward_stage.py:423, 490` run `config.model_dump()`
  unchanged on train and test; the train fold is a pass/fail filter. This is rolling
  out-of-sample evaluation of a fixed config (fine), not walk-forward optimisation. The
  `WindowType` enum (ROLLING/EXPANDING/ANCHORED) in `validation/walk_forward_validation.py:25`
  is never used; only rolling exists (`walk_forward_stage.py:365-388`).
- Survivorship in the evidence: `_to_fold_input` (`walk_forward_stage.py:133-154`) drops
  folds that failed the train filter, so `OverfittingStage` and
  `WalkForwardValidationService` compute fold consistency and degradation only on folds
  where the strategy already looked good.
- Smaller: folds are calendar days (trading calendar ignored, boundary day in both train
  and test); `step_days < test_days` and `min_folds_passed > n_folds` are not validated;
  with `resample_to_daily=True`, `warmup_bars = 0` (`simulation_runner.py:292`) so the test
  fold starts cold, and Sharpe is annualised with the 5-minute factor (19,656 bars/yr),
  inflating it ≈ 8.8×; `consistency_score` over 4 sub-windows of a 90-day fold is 5 values.
- What is right: each fold is a fresh simulation with its own dates, and 5-minute runs load
  a proper warm-up from before the fold start.
- [ ] Range guard: `StagedPipelineConfig` validation rejects a walk-forward stage whose
      first `test_start` is not after every earlier selection stage's `end_date` (or the
      YAML declares a `holdout_from` date that earlier stages may not cross). Fix
      `ma_sweep_staged.yaml` accordingly.
- [ ] Feed all folds into the overfitting evidence (train-failed folds with test metrics
      null and `test_passed=False`), and report `folds_skipped_at_train`.
- [ ] Gate on a pooled or median test Sharpe across folds in addition to per-fold pass
      counts; validate `step_days >= test_days` and `min_folds_passed <= n_folds`.
- [ ] Either implement EXPANDING/ANCHORED windows or delete `WindowType` and fix the docs
      (`docs/backend/research/advanced_validation_framework.md`).
- [ ] Daily resample: load daily warm-up bars and pass `bars_per_year=252`.
- [ ] Fold geometry (added 2026-11-14, user's question: "more folds, maybe longer?"). The
      items above make folds honest; none sets how many there are or how long they run.
  - [ ] Folds in sessions from the trading calendar, not calendar days (also removes the
        shared boundary day).
  - [ ] A minimum test span per fold, validated in the config. A Sharpe estimate's
        precision depends on the calendar span, not the bar count: a 10-session fold of
        5-minute bars has 780 observations and an annualised-Sharpe standard error of
        roughly 5 (√(252/10)), so short folds are near coin flips and the pooled gate
        above is the one that carries weight.
  - [ ] Fold count and length derived from the history available, with EXPANDING as the
        way to get more test folds from a short history (ties to the `WindowType` item).
        On the 74-session rolling window with 15 held out, 20 train / 10 test / 10 step
        gives 3 folds; more folds and longer folds compete for the same sessions.
  - [ ] Longer folds are blocked on more history: schedule with the SIP historical-bars
        check in the data section below.
- R2 in the six-stage replay hook (2026-11-14, read-only investigation; the findings above
  were written against the four-stage YAML):
  - Selection leakage applies, and more broadly: `initial_quality_filter` and
    `walk_forward_robustness` both run `start_date → tick_date`
    (`research_hooks.py:553-554, 581-582`), and so do Monte Carlo, regime and stress. No
    stage sees a date the others did not; there is no holdout.
  - Geometry on a 90-day window: 45 train / 30 test / 15 step gives two folds whose test
    windows overlap by half, and `min_folds_passed=1` passes on either.
  - Does not apply: the daily-resample items (the hook never sets `resample_to_daily`).
  - New: with one completed fold, `fold_sharpe_cv` is 0
    (`walk_forward_validation.py:117-123`), so `fold_instability` is 0.0 and counts as a
    present indicator at weight 0.20 (`overfitting_analysis.py:84, 213-214`), lowering the
    overfitting probability. Train-failed folds are dropped, so one-fold evidence is common.
  - New: under 75 days of data the hook builds stage 1 only (`research_hooks.py:576`); the
    other five stages are absent, with no warning.
  - Size of the leak, random daily returns of known true Sharpe in the hook's fold layout,
    Sharpe thresholds only (an illustration, not a pipeline measurement): after stage 1,
    walk-forward passes 68% of no-edge strategies and 80% of true-Sharpe-2 ones. With
    stage 1 restricted to the first 45 days: 55% and 78%.
  - Direction (user, 2026-11-14): the target is a selection / test split plus a final
    holdout over a longer history ("combining A and C and maybe just an overall longer
    timespan"); for now "no other changes to the walk forward aside from anything else you
    recommend ... without a big update". Fold lengths, overlap, the pass rule and the
    holdout wait for more history.
  - Small set (user, 2026-11-14: "the 3 small changes"), implemented the same day:
    1. Stage 1 selects on the first 45 days when walk-forward follows
       (`selection_end` in `_build_replay_experiment_definition`), so the first
       walk-forward test fold starts after the selection window. The second test fold
       still overlaps the first, and Monte Carlo, regime and stress still run the whole
       window, selection days included. Under 75 days stage 1 keeps the whole window, as
       before.
    2. A single completed fold yields no `fold_instability` (`overfitting_analysis.py`).
       Not changed: `research_feature_vector_builder.py:325` and
       `validation_artifact_repository.py:149-150` still read a one-fold CV of 0 as stable.
    3. Docstrings in `walk_forward_stage.py` say what the stage is (a fixed config
       evaluated on rolling sub-periods; no fitting) and where it sits.
    Consequence handled with item 1: stage 1's run now covers 45 days, so the governance
    `source_run_id` is taken from the strategy's full-window run (the reference run of
    the later stages) and falls back to stage 1's run only when no later stage recorded
    one.
    Tests: `test_research_stages_smoke.py` (selection window, the stage-1-only case, the
    source run) and `test_overfitting_analysis.py` (one fold against two).
  - Real-bars check of the small set (2026-11-14; data, harness and caveats as under "R1
    verification"; hook as is, working tree against `4dd0671ae`):

    | Passed | Stage 1 on 90 days (`4dd0671ae`) | Stage 1 on the first 45 days |
    |---|---|---|
    | Stage 1, of 30 | 4 | 2 |
    | Walk-forward | 2 | 1 |
    | Monte Carlo | 2 of 2 | 1 of 1 |
    | Regime | 2 of 2 | 1 of 1 |
    | Stress | 0 of 2 | 0 of 1 |

    The strategy that still reaches Monte Carlo is the composite rule with base Sharpe
    1.61, with the same Monte Carlo numbers as before (its base run is the full window in
    both). The one that no longer does is the mean-reversion strategy with base Sharpe
    0.70; the harness records counts per stage only, so whether it dropped at stage 1 or
    at walk-forward is not known. One window and 30 strategies: this shows the change
    takes effect on real bars, not how much selection leakage it removes.
  - More history, what it takes (not started): the rolling version's length is the
    operator setting `bench_resim_window_days` (default 63,
    `paper_trading_golden_path_orchestrator.py:778`) and is shared with the bench re-sims,
    so research history wants its own dataset; `atp ingestion run-backfill` takes 31 days
    per run by default (`--max-days`) and follows `ALPACA_DATA_FEED`. Unchecked: how far
    back Alpaca serves IEX five-minute bars, and whether a year fits in B's 3 GB (a
    70-session run used about 1 GB on the laptop). Undecided: which universe applies to
    old dates. The hook resolves the universe active at the window's start and the
    survivorship guard rejects symbols outside it, so a year-old window needs either
    universe versions that reach back that far or a fixed symbol list declared in the
    experiment (the CUSTOM mode), which reintroduces survivorship if it is today's list.
  - Tasks for the six-stage hook (added 2026-11-14; the checkboxes above were written
    against the four-stage YAML and none of them changes the hook. User: "this seems all
    super doable everything in the sprint"). The code is small; what is short is the
    history loaded today (70 sessions on B), not the history that exists (user,
    2026-11-14: backfill can fetch many years, in any span or market condition). On 60
    sessions a three-way split leaves a 15-session holdout whose Sharpe has a standard
    error near 4 (√(252/15)), so these follow the longer-history item except where noted.
    With years loaded the holdout can be a separate period, not the last 15 sessions.
    - [ ] The hook's lookback is a hard-coded 90 days (`research_hooks.py:486`). Loading
          more history changes nothing for the hook until this is a setting, or until
          long-window research runs from an experiment file.
    - [x] Stage 1 selects on days walk-forward does not test (small set, above).
    - [ ] Window layout in one place in `_build_replay_experiment_definition`: selection
          days, evaluation days, holdout days, derived from the sessions available, with a
          minimum length for each and a logged reason when the window is too short.
    - [ ] Walk-forward, Monte Carlo, regime and stress read evaluation days only (the
          overfitting stage reads their evidence). Today Monte Carlo, regime and stress
          run the whole window, selection days included, so the run Monte Carlo resamples
          is flattered by the days the strategy was picked on.
          The governance source run becomes the evaluation-window run.
    - [ ] Holdout: the last N sessions, read by no gate, scored once for the final
          survivors and stored with the candidate. Open decision for the user: report
          only, or a final pass/fail.
    - [ ] Fold geometry in the hook: test folds that do not overlap (`step_days >=
          test_days`), no train window covering another fold's test days, and a pooled or
          median pass rule in place of "any one fold" (`min_folds_passed=1`).
    - [ ] The R2 and R6 validators cover stage configs built in Python, not only YAML;
          otherwise the hook can drift from its own rules the way the four-stage file did.
    - [ ] Repeated attempts. Research in the platform backtest runs on a monthly cadence
          (`platform_backtest_service.py:1035-1051`; the cadence comes from the run's
          scheduled-jobs config, not traced through every config) with a 90-day lookback,
          so consecutive runs share about two thirds of their days and a strategy that
          fails gets another attempt on mostly the same data. First confirm the cadence
          the real runs use; then decide between counting attempts per strategy identity
          and accepting it with the holdout as the guard.

**Verify:** the config validator rejects the current `ma_sweep_staged.yaml`; a fixture with
one train-failed fold shows it in the overfitting evidence; a daily-resampled fold's Sharpe
matches a hand computation within rounding.

**R3 — Research checkpointing is built but never instantiated (cost/convenience).**
`ResearchCheckpointService` is complete and `SimulationStage`, `WalkForwardStage` and
`MonteCarloStage` accept `checkpoint_service` and route through `run_simulation_unit` when
set. No production path passes it: the three stage constructors in
`application/services/platform_replay/research_hooks.py:550, 577, 599` omit it,
`pipeline_runner.py` and `experiment_orchestration_service.py` have no reference, and the
only constructions outside tests are the read-only `research inspect-checkpoints` and
`research plan-restart` (`cli/commands/research.py:1475, 1488`), which read a store file
nothing ever writes. The resume tests pre-populate an in-memory service by hand. The
worker's `<name>.checkpoint.json` (`worker_run_service.py`, runbook "drop the stale
checkpoint") is the platform-replay tick checkpoint, a different and working system.
Trap: a skipped unit returns no `result` (`research_checkpoint_service.py:179-215`) and the
stages treat a missing result as missing evidence (`_gate_stage_support.py:117`), so a
resumed run would drop every already-completed strategy from the survivors. The identity
hardcodes `universe_version="v1"` and omits the seed; `experiment_id` must be stable across
restarts.
- [ ] Rehydrate skipped units from `cached_run_id` / `metadata.run_id` (reload the
      `SimulationRunResult` from the SoR) so a resumed stage keeps its survivors. Store
      the pass/fail verdict with the checkpoint as well, and re-filter on resume: resume
      is currently wrong in both directions (a strategy that failed before the crash can
      come back as a survivor, and a partial resume loses earlier passes) **[7.10 #3]**.
- [ ] Identity: thread the real `universe_version`, `random_seed` and `pipeline_id`, and
      include the `SimulationResultCache` key's `key_id` (today the identity lacks seed,
      symbols, date range and cost model) **[7.10 #4]**.
- [ ] Instantiate in `experiment_orchestration_service.py` (where
      `plan.staged_pipeline_config.stages` is built) with
      `persist_path=<artifacts>/research/<experiment_id>.checkpoints.json`, pass
      `checkpoint_service`, `resume_mode`, `force_rerun`, `dry_run` to the three stages;
      same in `research_hooks.py` if replay research should resume. The concrete call
      sites are `from_dict()` and `_build_replay_experiment_definition`, and
      `SimulationResultCache` is wired there in the same change **[7.10 #2]**.
- [ ] CLI: `--resume`, `--checkpoint-store`, `--force-rerun` on the full-pipeline command;
      retire the deprecated `resume-experiment` alias.
- [ ] End-to-end test: run, kill after the first stage, rerun with `--resume`, assert only
      the missing units execute and the final survivors match an uninterrupted run; plus
      the two directional cases from [7.10 #3] (failed-before-crash still fails, partial
      resume keeps earlier passes).
- [ ] Housekeeping while in the file: the replay hook's "42-day lookback" comment is
      stale, the lookback is 90 days **[7.10 #10]**.

**Verify:** the e2e test above; `research inspect-checkpoints` lists a real run's store.

**R4 — Audit the remaining gates the same way (not started).** Regime, stress, overfitting
and parameter-stability stages (`pipeline/stages/regime_stage.py`, `stress_stage.py`,
`overfitting_stage.py`, `pipeline/gates/*`, the `ParameterSensitivityAnalyzer`) were not
read on 2026-11-07 and sit in the same funnel. Stress re-runs the simulation under cost
multipliers (`_gate_stage_support.py` jobs carry `cost_multiplier`), regime slices by the
classifier's labels, overfitting consumes the walk-forward evidence R2 found to be
survivorship-biased. Same method: trace the real input path, compare with what the tests
feed, look for fail-open branches and `None`-coercion.
- Observed 2026-11-14 (R1 verification, real bars, 60 sessions): the stress gate
  eliminated every strategy that reached it in all five runs: 2 of 2 in the hook as is
  on `main` and on the branch, 1 of 1 with the R2 small set, and with the upstream gates
  opened 2 of 2 on the branch and 3 of 3 on `main`. Regime passed 2 of 2, 2 of 2, 1 of
  1, 2 of 3 and 3 of 5.
  So on this window stress decides the outcome. Whether its thresholds are right or the
  gate is too strict for a 60-session window is not known; it has not been read. Audit it
  first.
- [ ] One audit note per stage under this heading (what it measures, where its inputs
      really come from, what the tests prove, fail-open paths); each confirmed problem
      becomes its own R-number with a fix list.
- [ ] First confirmed gap to carry in: nothing on the backtest trading path reads the 12%
      `max_drawdown_limit`, so a research run never experiences the kill that the live
      path would apply at the same drawdown **[9.8 #5]**. Research scores therefore
      overstate strategies that would have been halted. Wire it (observe-mode first, as
      a recorded event; then enforce) and rerun the two-year backtest with
      `ACTIVATE_KILL_SWITCH`. Read `docs/architecture/safety-doctrine.md` first; this is
      a parity item, so it goes through the rehearsal rule.

**Verify:** every stage has a note here, and each stage has at least one test on a real
runner where the gate fails.

**R5 — Composite score hides missing evidence (not started).** The validation framework
redistributes an absent component's weight to the present ones
(`docs/backend/research/advanced_validation_framework.md`, "When a component is absent"),
so a strategy with no Monte Carlo or regime evidence is never penalised for the gap. After
R1 that is exactly the hole that lets a thinly tested candidate outrank a well-tested one.
- [ ] Decide and implement one of: a missing-evidence penalty per absent component, or a
      required minimum evidence set before a composite score is produced at all (score
      `None` + reason otherwise). Record the choice here.
- [ ] Test: identical metrics with and without the Monte Carlo component must not produce
      the same (or a higher) composite score for the one missing it.

**R6 — Experiment-config and holdout validation (not started).** R2's range guard belongs
in a general validator so the rules are enforced, not written in docs: no stage may read
past a declared holdout date; walk-forward `test_start` after every earlier selection
stage's `end_date`; `step_days >= test_days`; `min_folds_passed <= n_folds`; vacuous
thresholds (e.g. `min_sharpe <= 0` with `min_trades == 0`) rejected unless the YAML says
`smoke: true`. Home: `research/config/stage_configs.py` validation plus the existing
`atp research validate-config` / `plan-experiment` commands. This is also what makes the
suite `v1` rule "holdout never tuned on" (`pre-launch-deployment-plan.md` §5) enforceable.
- [ ] Validator + tests; the current `experiments/staged/ma_sweep_staged.yaml` must fail
      until fixed, `exp_mc_smoke_test.yaml` must carry `smoke: true`.
- [ ] A repo-level holdout declaration (one date, one place) that every experiment YAML is
      checked against.

**R7 — Test hardening per stage (not started).** Every stage audited so far is tested with
`MagicMock` results and permissive filters, so gating logic is never exercised.
- [ ] One integration test per stage on synthetic bars with a real `SimulationRunner` and
      realistic thresholds, asserting the gate can fail and that survivors change when the
      inputs change. These are the regression net for every other item in this sprint.
- [ ] A cross-process replay test: the same experiment run twice in separate processes
      produces artifacts with identical checksums **[7.4]**. This is the determinism
      guarantee R8 and the cache work depend on; without it "same seed, same result" is
      a hope.

**R8 — Simulator RNG is positional, not keyed (not started).** The fill model draws from
one RNG stream in intent order, so adding or removing one symbol changes every other
symbol's fills in the same run **[7.5 #1]**; a config that sets any stochastic
probability without a seeded RNG runs unseeded instead of failing, and
`reset_for_run(rng=None)` keeps the previous RNG **[7.5 #2]**. Inert today only because
the probabilities are `None` (R1); the moment stochastic fills become an opt-in knob
this is live.
- [ ] Keyed RNG per decision derived from (run seed, `intent_id`, bar timestamp, decision
      kind), so one symbol's fills do not depend on which other symbols are in the run.
- [ ] Raise when any stochastic probability is set and no seeded RNG was passed; make
      `reset_for_run(rng=None)` either reset to a seed-derived RNG or raise.
- [ ] `settlement_days` is applied as a count of bars: rename to `settlement_bars` or
      convert with `BARS_PER_DAY` **[7.10 #8]**. Check which the existing fixtures assume
      before changing the number.
- [ ] Tests: same run with and without an extra symbol gives identical fills for the
      shared symbols; the unseeded-stochastic case raises.

**Verify:** R7's cross-process checksum test passes before and after; the 5c parity
check is re-run because this touches `research/simulation/`.

**R9 — Generator has no selection (folds into Step 2).** `evolutionary_generator.py`
mutates without fitness, so today it is a mutation search, not evolution **[7.10 #7]**.
Step 2 is the fix; if Step 2 slips, the fallback is to rename the method honestly. No
separate checklist.

**Definition of done for any change under `research/simulation/`:** the research ↔
trading-cycle parity check from `portfolio-rotation-plan.md` 5c (re-sim vs platform
correlation ≥ 0.99 on the shared fixture) stays green. R1's bootstrap does not touch the
simulator; stochastic fills or any execution-model change would, and must re-run it.

**Step 0 follow-ups (trail the fixes above; do before Step 5):**
- Cache and identity hygiene: `SimulationResultCache` keys and the checkpoint identity
  disagree about what makes a run unique (seed in one, hardcoded `universe_version="v1"` in
  the other; neither knows a bootstrap spec). Fix in one place before R3 wiring and before
  the monthly job reuses cached runs. Add the git SHA or an engine version string to the
  cache key so a code change invalidates cached results **[7.10 #5]**.
- Research observability (pre-launch plan 2.3, unchecked): `run.mode`, `backtest.run_id`,
  `fixture`, `sim_timestamp` span/log attributes; heavy trace sampling for backtests; a
  worker/research-runs dashboard. The checkpoint metrics
  (`observability/metrics.py:1314-1321`) start firing once R3 lands. Known breakage to
  fix on the way: `research_pipeline.json` never loads (wrong folder and a
  `${DS_PROMETHEUS}` import variable) **[1.8 #1]**; research stage survivor counts are
  recorded as histograms and should be gauges **[1.8 #4, 7.10 #9]**; every duration
  histogram uses ms-scale default buckets for second-scale values, so research p95s read
  ~4.75 s regardless **[1.8 #7]**.
- Worker resource budget: one measured full pipeline run on B with peak memory and
  wall-clock recorded in section 5, and a memory cap in `infra/ops/worker_job.sh` before
  Step 5's timer (the runbook already warns research jobs are memory-hungry).

**D1 — SIP bars: one feed everywhere, switched together (check now, switch later).**
The window B holds is IEX: the backfill cycle takes its feed from the same
`ALPACA_DATA_FEED` setting as live ingestion (`scheduler/cycles/run_market_backfill_cycle.py:270`,
`get_data_feed()`), A leaves it at the default `iex`, and `atp ingestion run-backfill` has no
feed flag. The 2026-11-06 backfill itself had no problems; the bars are just IEX-thin.
Measured over the 21 live sessions since 10/09 (audit `BAR_MISSING` rows): coverage is
94-100% for AAPL / NVDA / GOOGL / SPY / TSLA / META / AMZN / MSFT / AVGO / IWM / QQQ, 92%
for V / BKNG, 66-86% for AMD / UNH / MA / HD / ADBE / TLT, and 39-61% for CRM / COST / DIA.
It is a price-per-share effect (fewer prints on IEX for $500-$5,000 names), not a liquidity
effect, and the gaps cluster 11:00-15:00 ET. The universe machinery reads IEX too: the
screener hardcodes `feed="iex"` (`universe/providers/alpaca_screener_provider.py:205`) and
the daily bar source uses `get_data_feed()` (`alpaca_daily_bar_source.py:116`), so the
pool's dollar-volume scores are IEX volume (the 11/30 dry run removed CRM and DIA, the two
worst-coverage names).

**Rule (2026-11-08):** research, universe and live must read the same tape. Validating a
strategy on SIP bars and trading it on IEX bars scores signals it will never see live; a
SIP-ranked pool promotes names the live loop cannot follow; a SIP backfill under IEX daily
versions puts a seam in the rolling window on 10/09. So no half-measures:
- [ ] Now, free, no data change: confirm the free tier serves SIP *historical* bars older
      than 15 minutes with a one-symbol, two-day backfill run with `ALPACA_DATA_FEED=sip`
      set on that command only (success = a validated version; a subscription error means
      the research side of a future switch is not free). Do **not** re-pull the window or
      touch the screener yet. Delete the test version afterwards.
- [ ] Through the paper soak: everything stays IEX. Live cannot be SIP without the paid
      plan (real-time), and the live feed decides the others. Step 0 (R1-R7) is
      feed-independent and proceeds now; Steps 1-2 run on IEX with every results-log row
      labelled `feed=iex` (they are relative comparisons on identical bars, so the
      conclusion survives a switch; only the absolute scores need a re-run).
- [ ] **Trigger (agreed 2026-11-08): the feed switch happens when a go-live date is set,
      at least six weeks before the first real dollar**, so the switch gets one full
      universe rotation, several weekly reviews and a re-baselined bench on SIP before real
      money, and the soak sessions after it are the ones that count as the clean record.
      Put the switch weekend on the deployment plan's calendar the day the date is set.
- [ ] If the go-live decision is the paid real-time SIP plan
      (`pre-launch-deployment-plan.md` §6, Algo Trader Plus, ~$99/mo as of 2026-11), flip
      in one weekend, all together: `ALPACA_DATA_FEED=sip` for the scheduler; make the
      screener's feed configurable and set it to SIP; re-pull the three backfill chunks
      (2026-08-01..08-31, 09-01..09-30, 10-01..10-08) on SIP and retire the IEX versions
      (confirm `bar_id`s do not collide); record `feed` in each version's `metadata_json`
      (add the field to `run_market_backfill_cycle` if missing). Then re-baseline: rerun
      Step 1 and the rotation dry run, and mark every results-log row with the feed, because
      coverage, volume levels (20-50× on SIP) and ranges all move for feed reasons alone.
- [ ] **Switch-weekend checklist (do all, in this order; by then the plumbing bugs are
      closed and these are plain to-dos).** The switch is a break in the *inputs*, not in
      the strategies: fills before it are real outcomes and keep their P&L; signals and
      evidence change. Price-driven signals barely move (closes differ by cents, gaps
      vanish); volume-driven rules move a lot (SIP volume is 20-50× IEX and smooth, so
      ratio rules fire at a different rate and absolute volume thresholds are simply wrong);
      every scorecard / re-sim / health reading before the switch was made on IEX inputs.
      1. [ ] **Tag everything with the feed** before the switch: `feed` on dataset
             versions (`metadata_json`), fills (`fills.metadata` or a column), portfolio
             scorecards, bench evaluations, worker result submissions. Nothing is compared
             across the seam by accident afterwards.
      2. [ ] **Re-pull the whole research window on SIP**, the live period since
             2026-10-09 included, not only the Aug-Oct backfill chunks (historical SIP is
             free). Re-sims and the bench then read one tape from day one. Keep the IEX
             versions (tagged) as the audit trail of what live actually saw.
      3. [ ] Flip `ALPACA_DATA_FEED=sip` for the scheduler; make the screener's feed
             configurable (`alpaca_screener_provider.py:205` hardcodes `iex`) and set it.
      4. [ ] **Re-baseline the same weekend:** rerun the bench re-sim on the SIP window and
             the rotation dry run; those become the backtest evidence of record. Forward
             evidence from the switch date onward is the record that counts for the Phase 5
             gate.
      5. [ ] **Freeze demotions / restart health windows** for the first two weeks after
             the switch (operator setting or a dated exemption), so no incumbent is demoted
             for a behaviour change that is really an input change. The bucket skew is
             expected: incumbents were partly selected for coping with IEX gaps; candidates
             rejected for trading COST / DIA into empty windows may come back on the first
             post-switch monthly run. That is the switch working, provided SIP-era
             candidates are never ranked against IEX-era incumbents on mixed evidence.
      6. [ ] **Re-derive volume thresholds** in the composite generator from the SIP
             distribution, or make them quantile-based so the feed level cannot matter
             (`_THRESHOLD_VARIANTS["ratio"]` and `_volume_confirm` in
             `composite_generation.py`). Rerun Step 1 so the results log has a `feed=sip`
             baseline next to the `feed=iex` one.
      7. [ ] Record the switch date here and in `pre-launch-deployment-plan.md` Phase 5;
             the scorecard's Data row gets a new SIP `SLA_BREACH` baseline (expected ≈ 0).
- [ ] If the go-live decision is to stay IEX: give the rotation scorer IEX coverage as a
      weighted input (not a hard gate), so thin names must earn a seat on merit, and accept
      the tilt toward lower-priced names and index ETFs that this implies.

**Verify:** the free-tier check is recorded here with the result; after any switch, no
rolling version mixes feeds (`metadata_json.feed` uniform across `source_versions`).

**D2 — Parquet integrity for research reads (not started).** The research engine, the
bench re-sim and the dataset pull all read the Parquet layer, and the study found four
ways it can hand them the wrong rows while every registry row still says `validated`
(the same shape as Findings 26 and 27 on the deployment side):
- [ ] Compaction in `platform_replay/ingestion_hooks.py` deletes fragments it failed to
      read: delete only what was read, raise or log on read errors **[6.5 #2]**. Data
      loss, do first.
- [ ] The reader ignores fragments once `data.parquet` exists: read each file, normalise
      with `_with_partition_columns`, then concat **[6.5 #1]**. Live appends after a
      compaction are invisible until this is fixed.
- [ ] The table checksum depends on chunk layout (confirmed): `sort_by` the key columns
      and `combine_chunks()` before hashing in `compute_checksum.py` **[6.5 #5]**. Until
      then a pull-side checksum comparison can fail on identical data, or pass on
      reordered data.
- [ ] Partition dates are UTC dates, not exchange dates **[6.5 #8]**: a 5-minute bar at
      19:55 ET lands on the next UTC day. Any window built from partition dates (not
      bar timestamps) is off by the after-hours tail. Decide whether the rolling window
      and the bench window read by timestamp (then this is cosmetic) or by partition.
- Not research-blocking, tracked in the backlog only: in-place `data.parquet` rewrite
  [6.5 #4], `_metadata.json` overwritten per cycle [6.5 #3], checksum verification never
  called on A [6.5 #6], small-file compaction [6.4], stale storage doc [6.5 #9].

**Verify:** a fixture with one unreadable fragment keeps every readable row after
compaction; a fixture with `data.parquet` plus a later fragment returns both; the same
table written in two chunk layouts hashes identically.

### Step 1 — Baseline measurement (not started)

Run the existing pipeline on the window B now holds and record the numbers, so the
generator change has a "before". Run it **after R1, R2 and the D1 check land** (and after the SIP re-pull if it
turns out free), or the baseline is scored by a vacuous Monte Carlo and leaky folds on thin
bars and has to be redone.

Also wait for the Finding 27 re-pull (`pre-launch-deployment-plan.md`): the 2026-11-12
bench dry run on B showed the rolling window is missing 8/31, 9/30 and 10/08 (each
backfill chunk's last session, dropped by a midnight-UTC `--end`), 10/09 (an empty daily
version) and runs thin on 10/12–10/14. Pin the baseline to the first rolling version
built after those sessions are back.

- [ ] Experiment YAML `experiments/research/baseline_composite_grid.yaml`: `composite_rule`
      via `grid` (all 652 skeletons), the 22-symbol A universe, dataset pinned to the rolling
      version B pulled (`raw_bars_20261107T005243Z_8097b17d` or newer), **search window**
      2026-08-03 → 2026-10-17 (~54 sessions), **holdout** 2026-10-20 → 2026-11-06
      (~14 sessions), `random_seed: 42`.
- [ ] Run on B with `infra/ops/worker_job.sh research run-pipeline --config …` (one job at a
      time, lock respected) or on a laptop after `atp datasets pull --version <id>`.
      (2026-11-14: the CLI command that exists is `atp research run-experiment --config`;
      there is no `run-pipeline`.)
- Entry points (2026-11-14): the six-stage layout exists only as Python in
  `research_hooks.py`, which runs inside a platform backtest (the backtest creates its own
  universe version first, `platform_backtest_service.py:618-673`). This YAML therefore has
  to declare the six stages itself, or it will measure the older four-stage layout. B's
  `universe_versions` table is empty at rest (the worker reset clears it before every
  run), which is fine here: a YAML with a `symbols` list resolves in CUSTOM mode and needs
  no universe version.
- [ ] Record in section 5: candidates generated / accepted / rejected / duplicates
      (`GenerationSummary`), survivors per gate, cluster count after dedup, best and
      median walk-forward score, best and median **holdout** Sharpe / return / drawdown of
      the top 10 by search score, wall-clock and peak memory.
- [ ] Same run with `random` (`n_samples: 50`) so we know how much the pool alone buys.

**Verify:** the run completes end to end on B against pulled Parquet (no Alpaca call), the
summary JSON lands under `artifacts/worker/`, and the numbers are in section 5.

### Step 2 — A real evolutionary search for composites (not started)

Replace the aggregator flip with a search that uses fitness (R9, **[7.10 #7]**). Branch
`feat/composite-evolution`.

Design (minimal, additive):

- **Population** seeded from the skeleton pool (`population_size`, default 40).
- **Fitness** = the pipeline's search-window score from the simulation stage (reuse
  `scoring.py` weights; never the holdout).
- **Operators:** mutate a threshold (within the ±10 schema cap), a lookback, a filter
  (add / drop / swap regime ↔ volatility), an aggregator parameter; crossover = take the
  rule list of one parent and the aggregator + filters of another; keep every config valid
  through the existing `make_config` + `_validate_template_components` path (invalid →
  `summary.reject`, counted).
- **Selection:** tournament, `elitism` top-k carried over, `generations` default 5,
  `mutation_rate` default 0.25 (the option already exists).
- **Determinism:** everything from `options.seed`; same seed → same configs (test).
- **Lineage:** `metadata.generation_template` keeps the ancestry chain
  (`…_mut3_x_cross7`), so a candidate's origin is readable in the bench later.
- **Bounds:** a hard cap on evaluations per run (`max_evaluations`) so a monthly job has a
  known cost; skeletons already simulated in the run are cached
  (`research/cache/strategy_generation_cache.py` exists).

Files: `research/strategy_generation/composite_generation.py` (operators),
`generators/evolutionary_generator.py` (loop), `generation_result.py` (new options),
`strategy_generation_engine.py` (wire fitness callback), plus the pipeline call site in
`experiments/services/experiment_orchestration_service.py` if generation needs the
simulation stage as a fitness function.

- [ ] Operators + loop behind `method: evolutionary`; `grid` and `random` unchanged.
- [ ] Unit tests: fixed seed is reproducible; the output contains configs that **no
      skeleton in the pool can produce** (that is the "is it changing" test without a
      backtest); invalid mutations are rejected and counted, never raised; evaluation cap
      honoured.
- [ ] Rerun Step 1's experiment with `method: evolutionary` on the same dataset version,
      seed and windows; record the same numbers in section 5 next to the baseline.

**Verify:** on the holdout, the top-10-by-search-score set from `evolutionary` beats
`grid`'s top 10 on median Sharpe with no worse median drawdown, and at least a third of the
survivors are off-pool configs. If it does not, the search is not yet worth a timer; iterate
on operators, not on the gates.

### Step 3 — Candidate hand-off to A's bench (not started)

Copy the bench re-sim hand-off (`POST /api/v1/worker/...`, `worker` role, idempotent by
`run_id`, dataset version + git SHA checked, A rejects stale or mismatched results).

- [ ] Contract `WorkerCandidateBatch`: run id, dataset version, git SHA, window, per
      candidate the `StrategyConfig`, search score, holdout metrics, lineage, cluster id.
- [ ] Admission gate on A (the rotation plan's "novel or better"): correlation vs each
      group champion from the bench re-sim results; admit if novel or if it beats its
      group's champion, else record the rejection with the reason. Cap respected
      (weakest bench member retired when full).
- [ ] Audit every admission / rejection (`governance_audit_events`, worker run id).

**Verify:** a hand-made batch posted with a stale dataset version is rejected; a fresh batch
of two candidates (one duplicate of an ACTIVE strategy, one novel) admits exactly one, and
the bench shows it with `worker_run_id`.

### Step 4 — Recorded-dataset source for scheduled replays (not started)

`pre-launch-deployment-plan.md` 3.4 verbatim; it is the prerequisite for any *scheduled*
replay or research run on B, so it is listed here for ordering only. Additive source class
behind a fixture flag; default stays `alpaca`.

### Step 5 — Monthly research job + timer (not started)

- [ ] `atp worker research-campaign [--as-of] [--run-id] [--no-pull] [--no-publish]`:
      pull pinned datasets → Step 1's experiment with `evolutionary` → clustering → post
      the admitted set via Step 3 → summary to S3. Same shape as `bench-resim`.
- [ ] `ratp-worker-research.timer` in `infra/ops/install_worker_timers.sh`, first Saturday
      of the month after the bench re-sim; `worker-box.md` table row and runbook section.
- Spreading the campaign over several days (user, 2026-11-14: the long windows are what
  the scheduled worker is for, "maybe a way to split those long hours over the last week
  or last 2 weeks of month"; not written down before in either plan, which has one job
  on one Saturday). Why it is needed, a linear scaling from the laptop and not a
  measurement on B: 30 strategies over 60 sessions took 17 minutes on 2026-11-14; 652
  skeletons over two years is tens of hours. B runs one job at a time and must yield to
  the nightly pull (20:30 ET) and the Saturday bench re-sim (06:00 ET).
  - [ ] Measure first: one full pipeline run on B at the intended window, wall time and
        peak memory (the worker resource budget item above).
  - [ ] Split by piece of work: the month is a list of independent jobs (selection on
        the long window; one regime window per job; holdout and hand-off last), each
        finishing and recording its own result. This is the "campaign YAML" of
        `pre-launch-deployment-plan.md` ("which fixtures and research jobs run when").
  - [ ] R3 (checkpoint and resume) for any single job longer than one night. This moves
        R3 from "before Step 5" to "before the first long run".
  - [ ] The window comes from a rule fixed in advance and rolls forward each month (for
        example the most recent 24 months with the last 6 held out). Choosing it after
        seeing results is one more way to fit.
- Window design (proposed 2026-11-14 in answer to the user's "is 90 days the correct
  timing ... does extending the time overall help or adding the market condition
  timespans"; not agreed, and not to be fixed before the history depth is known):

  | Question | Data that answers it |
  |---|---|
  | Is the edge real? | One long continuous stretch. Sharpe uncertainty is about 1/√years: ±2.0 on 60 sessions, ±1.0 on one year, ±0.7 on two, ±0.5 on four. |
  | Does it break in bad conditions? | Regime windows of 2 to 6 months, chosen by classifier coverage (`suite_v1`, deployment plan section 5, not built). Each is as noisy as today's window, so the pass rule is "does not blow up", not "is profitable". |
  | Is it still working now? | The recent 60 to 90 days. This is what the hook's 90-day window is good for; it is not enough to admit a strategy on. |
  | Did we fool ourselves? | A holdout, read once at the end. Six months still has an uncertainty near ±1.4: it can reject, it cannot prove. |

  Proposed ranges: about two years at five-minute bars, split near 12 months selection,
  6 evaluation, 6 holdout; 3 or 4 regime windows as a robustness gate beside the
  synthetic stress scenarios; the recent 90 days kept as the "still working" check. Named
  periods are legitimate for robustness and become training data the moment strategies
  are adjusted until they pass them, hence a frozen suite and an untouched holdout. The
  90 days itself was chosen so walk-forward's 75 days always fit (`research_hooks.py:486`
  comment), not for statistical reasons.
- Research-history dataset (noted 2026-11-14 for when work returns to deployment
  logistics; user: "our rolling dataset currently on B will still be useful ... just
  fetch historical to fill in the past?"). Yes to both, as two datasets:
  - The rolling version keeps its job: the weekly bench re-sims and the "still working
    now" check. Its length is `bench_resim_window_days` and it slides, so making it
    longer would slow every bench re-sim and move bench scores for a reason unrelated to
    the strategies.
  - The long history is a second dataset built the same way the bench window was filled
    on 2026-11-04/06: `atp ingestion run-backfill` chunks (each a validated
    `historical_backfill` version) for the past, live daily versions for the recent end.
  - [ ] Decide where it is built and how it reaches B (the rolling version is built on A
        and pulled from S3; a multi-year build on A is a write on the trading box).
  - [ ] Check the dataset after building and before scoring anything on it: the same
        number of bars per session throughout, no missing sessions, no unexplained
        bar-to-bar price jumps. Known seams, all from `pre-launch-deployment-plan.md`
        3.3(a): backfill chunks used to skip their last day (fixed, PR #175; 8/31, 9/30,
        10/08 and 10/09 re-fetched 2026-11-14 02:10 UTC); live days ended at the 15:50
        bar where backfilled days end at 15:55 (fixed going forward, PR #178; whether the
        live days recorded before the fix were repaired beyond 10/09 and 10/12 to 10/14
        is not checked here).
  - [ ] Splits: none in the 60 sessions used so far; a multi-year window will cross
        some, and the hook reads raw prices (`PriceBasis.RAW`).
- Verifying runs as they get longer (noted 2026-11-14, user's question). Rule: a long
  run is never where a code defect is first found. Keep "is the code right" apart from
  "is the conclusion right" and answer each with the cheapest check that can.
  - Already planned: per-stage tests on a real runner and the cross-process checksum
    (R7); before/after on identical bars (ground rules); a rehearsed dry run before the
    timer (this step).
  - [ ] Planted controls in every campaign: a strategy built to have no edge and a
        synthetic one with a strong edge. A no-edge control that passes more often than
        the gate's design rate means something leaks.
  - [ ] One frozen reference window (medium length, fixed dataset version) for
        before/after comparisons of code changes, so a change is never judged on a full
        campaign.
  - [ ] Step the window up (60 sessions, six months, two years) and check that time and
        memory grow as expected before scheduling the long one.
  - [ ] In-run checks written into the summary: stage counts add up, every failure has a
        reason, nothing passes with a failure attached (checked by hand on 2026-11-14).
  - How the 2026-11-14 window was chosen: it was not. It was the data on B cut by the
    hook's 90-day lookback; the 17 to 60 minute run times followed from that.

**Verify:** one full dry run (`--no-publish`) on B from the timer's unit; the plan's 3.3(b)
box ticks only after the first scheduled run has posted and A has admitted or rejected
every candidate with a recorded reason.

## 4. Later (not scheduled)

- Deeper structure: three-rule composites; regime-conditional branches (one rule set per
  regime bucket from the classifier).
- ML as a **ranker**, not a signal: predict holdout performance from
  `research_feature_vector_builder` output to decide which candidates get the expensive
  stages. Only after Step 2 shows the search finds things the pool cannot.
- Suite `v1` historical windows (`pre-launch-deployment-plan.md` section 5) once D1 has
  answered the SIP question and R6's holdout declaration exists.

## 5. Results log

| Date | Dataset version | Method | Generated / accepted | Survivors (gates) | Clusters | WF best / median | Holdout top-10 Sharpe median | Holdout drawdown median | Off-pool share | Wall-clock |
|---|---|---|---|---|---|---|---|---|---|---|
| | | grid | | | | | | | 0% | |
| | | random n=50 | | | | | | | 0% | |
| | | evolutionary | | | | | | | | |
