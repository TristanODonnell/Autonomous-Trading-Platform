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

Last updated: 2026-11-16 (08:30 UTC). Status is in "Where we are" directly below; the detail
and the evidence for every line are in section 3.

## Where we are (2026-11-16)

Every unticked box was checked against the code or the recorded evidence on 2026-11-15.

> **Now.** Package A (validators and evidence) is **merged as PR #184** (`5202ddd61`)
> and **deployed to A and B on 2026-11-16 at 06:23 UTC**. Checked on the boxes: both on
> the merge commit; A's app and scheduler restarted and import the evidence policy; B's
> image rebuilt. Five commits, one per finding: R14 overfitting gate report-only
> (`59d2079a5`); R6 + R2 pipeline rules checked when a pipeline is built
> (`515db54b7`); R2 train-failed folds kept as evidence (`77fbe69b6`); R5 + R16
> deciding scores rest on walk-forward and Monte Carlo only (`d964b96a2`); the plan.
>
> **What it changed in one line each.** The overfitting gate reports and eliminates
> nobody. A pipeline that breaks a fold or window rule is rejected when it is built,
> from YAML or from Python. Walk-forward records every fold, including ones whose
> train run failed. A strategy is scored only on walk-forward and Monte Carlo, and not
> at all without both. On real bars no outcome changed: one survivor, not deployable.
>
> **Next (not chosen yet).** Either package B (longer history on IEX, which unblocks
> most of what is still open) or package C (checkpointing, R3). Open decisions for the
> user: which of B or C goes first, and R13. Nothing is running.
>
> **Update, same day.** The user chose package B (longer history). Its read-only
> investigation is done and recorded under Step 5, "Package B investigation". R7's
> tests ride along with B; R13's decision waits until B gives more survivors; R8 stays
> parked (all of it is dormant, the settlement box included).
>
> **Agreed with the user the same day** ("yes lets do it your way"): rehearse on the
> current box before choosing between a larger box and a smaller way of holding
> bars; safety nets and a bar cache first. Committed, PR pending: a memory ceiling per
> worker job, a swap installer for B (written, not run on B), bars parsed once per
> pipeline run. New finding R17 (short warmups on Monday starts). The next decision
> was the stock list (answered: the universe generator as of the window's first day,
> then a backfill of the names it picks), then where it is built (answered: on Box B,
> with a market-data key). Next: how the two years are split.

Key: 🟢 done (shipped; anything left is a follow-up or waits on something else) · 🟡 in progress or partly done · 🔴 not started · ⚪ no work of its own, or deferred.

Keep this board in step with the boxes: when a box changes in section 3, change its row.
"Done / open" counts checkboxes.

**Step 0, pipeline integrity (the current work)**

| Item | What it is | Status | Done / open | What is left |
|---|---|---|---|---|
| 🟢 R1 | Monte Carlo resamples for real | **Done.** Merged PR #181, deployed to A and B | 4 / 0 | Nothing |
| 🟡 R2 | Walk-forward folds out of sample | **Partial.** The small set merged in #181. Package A's code-only boxes merged in #184, deployed 2026-11-16 | 7 / 13 | Code only, still open: pooled or median fold gate, daily resample, the walk-forward "entered" count (found 2026-11-15). The rest waits on longer history (package B): selection / evaluation / holdout split, fold geometry, folds in sessions, minimum test span. The two box lists overlap: one was written for the old four-stage file, one for the six-stage hook |
| 🔴 R3 | Checkpoint and resume | Not started (one housekeeping box done) | 1 / 5 | All of the wiring. Scheduled before Step 5 |
| 🟡 R4 | Audit of the remaining gates | **Audit done**: notes for stress, overfitting, parameter stability, regime; problems are R11 to R15 | 1 / 1 | The 12% drawdown kill is missing from the backtest path. R4's "Verify" (a real-runner test per stage where the gate fails) is not met; that work is R7 |
| 🟢 R5 | Composite score hides missing evidence | **Done.** Merged PR #184, deployed 2026-11-16. No score without walk-forward and Monte Carlo; report-only measures carry weight 0 | 2 / 0 | Nothing |
| 🟢 R6 | Experiment-config and holdout validator | **Validator shipped.** Merged PR #184, deployed 2026-11-16 | 1 / 1 | Repo-level holdout date (package B) |
| 🔴 R7 | Test hardening per stage | Not started as its own item. Realistic-curve tests now exist at gate level for Monte Carlo, stress and regime | 0 / 2 | A real-runner test per stage, the cross-process checksum test |
| 🔴 R8 | Simulator RNG keyed per decision | Not started | 0 / 4 | Inert until stochastic fills are switched on |
| ⚪ R9 | Generator has no selection | Folds into Step 2 | no boxes | Step 2 |
| 🟢 R10 | Stale marks when a bar is missing | **Done.** Merged PR #182, deployed to A and B | 6 / 2 | The re-sim vs platform correlation was not re-measured; more sparse-bar fixtures (R7) |
| 🟢 R11 | Stress gate: shocks report-only, costs decide | **Done.** Merged PR #183, deployed 2026-11-16. Rerun done: funnel 30 → 30 → 4 → 2 → 2 → 2, the first run with final survivors | 5 / 0 | Nothing. "Verify" met by the all-30 audit pass: 7 pass, 23 fail, for causes that differ. Stress at the new cost is R12's box |
| 🟢 R12 | Research trading cost: 1 basis point per trade | **Done except two boxes.** Merged PR #183, deployed 2026-11-16. Measured on real bars (funnel 30 → 30 → 2 → 2 → 2 → 2, stress passes 3 of 30) and on B's bench re-sim (order unchanged; momentum -1.8% → -7.8%) | 4 / 3 | Slippage written to fill rows. Two midpoint guards wait for a `LIMIT` / `TWAP` policy |
| 🔴 R13 | No parameter-stability check | Not decided | 0 / 2 | Decide whether to build the neighbour check |
| 🟢 R14 | Overfitting gate never exercised | **Report-only shipped.** Merged PR #184, deployed 2026-11-16 (agreed 2026-11-15). Measured first: on all 30 strategies it passed 26, including 16 of 20 losing ones, and changed no outcome | 3 / 1 | The prerequisites before `enforce` is switched on; they wait on longer history |
| 🟢 R15 | Regime gate report-only on a short window | **Report-only shipped.** Merged PR #183, deployed 2026-11-16 | 1 / 4 | The four prerequisites before `enforce` is switched on; all wait on longer history |
| 🔴 R17 | Short warmups get no bars when a window starts on a Monday | Found 2026-11-16, checked on the real loader; not fixed | 0 / 2 | Measure the effect, then fix with a before / after |
| 🟢 R16 | The deployable decision rests on the three report-only measures | **Decided and shipped.** Merged PR #184, deployed 2026-11-16 (user: "Trusted evidence only"). Deployable is now the average of walk-forward and Monte Carlo. Measured first: nothing was flagged deployable, the regime input was always 0.5, shocks near-constant | 2 / 2 | Calibrate the 0.55 threshold (needs longer history); pass the regime profile to the scoring request so the reported regime fields are real |

**Parked follow-ups on green items** (shipped, but these boxes are still open; none is forgotten)

- R6: the repo-level holdout date (package B).
- R10: re-measure the re-sim vs platform correlation; more sparse-bar test fixtures (package D).
- R12: write the charged slippage into the fill rows (package E); the two quote-midpoint guards, before any `LIMIT` / `TWAP` policy is switched on.
- R14: the prerequisites before the overfitting gate's `enforce` is switched on (package B).
- R15: the four prerequisites before the regime gate's `enforce` is switched on (package B).
- R16: calibrate the 0.55 deployable threshold (after package B gives more survivors); pass
  the regime profile to the scoring request so the reported regime fields are real (small,
  any time, must be done before the regime gate is switched on).

**Data**

| Item | What it is | Status | Done / open | What is left |
|---|---|---|---|---|
| ⚪ D1 | One feed everywhere (IEX now, SIP at go-live) | Rule and trigger agreed. The free-tier probe has not been run; deferred by the user 2026-11-15 until the other updates are done | 0 / 13 | Nothing now. The probe and the rest come with the switch |
| 🔴 D2 | Parquet integrity for research reads | Not started (the four defects re-confirmed in the code 2026-11-15) | 0 / 4 | Compaction deleting unread fragments comes first |

🔴 **Steps 1 to 5 (generator and plumbing): none started.** Step 1 baseline 0 / 4, Step 1b generation system review 0 / 7 (added 2026-11-15), Step 2
evolutionary search 0 / 3, Step 3 hand-off to A's bench 0 / 3, Step 4 no boxes, Step 5
monthly job 0 / 13. Section 5's results log is empty. Step 1 waits on R2.

**Work packages (the open boxes grouped by what gets done together)**

The boxes sit under the finding that produced them, so one piece of work is spread over
several R-numbers. This is the same list grouped by piece of work, in the proposed order.
Package A is merged and deployed. Nothing is running.

| # | Package | Boxes it closes | Waits on | Size |
|---|---|---|---|---|
| 🟢 A | **Validators and evidence** (done: merged PR #184, deployed 2026-11-16) | Done: R14's decision and its two-fold test; R6's validator; R2's range guard, `step_days >= test_days`, `min_folds_passed <= n_folds`, validators cover Python-built configs, `WindowType` deleted, hook lookback as a setting, train-failed folds kept as evidence. R5 and R16's decision (trusted evidence only: `research/validation/evidence_policy.py`). Left: nothing in code. Moved to B: folds in sessions, minimum test span, R6's holdout date | Nothing | One PR, no new data (#184) |
| 🟡 B | **Longer history on B** (on IEX; the feed does not change). Chosen by the user 2026-11-16; investigation done; first code committed the same day, PR pending (job memory ceiling, swap installer, bars parsed once per pipeline run, point-in-time pool option on the rotation command); B has a data key; decisions being taken | R2's selection / evaluation / holdout split and fold geometry (about 8 boxes), folds in sessions, minimum test span, R6's holdout date, removing the hook's `allow_overlapping_test_folds` exception, R15's four prerequisites, R14's prerequisites | The user's decisions (universe for old dates first). Investigated 2026-11-16: history is documented back to 2016; one run over 12 months needs about 1.7 GB and over 24 months about 3.2 GB, against about 3.0 GB usable on B | Investigation done, then one PR or two |
| 🔴 C | **Checkpoint and resume (R3)** | R3's five boxes | Nothing. Pays off once B runs take hours, which is after package B | One PR |
| 🔴 D | **Test hardening (R7)** | R7 both boxes, and the R7 boxes under R10, R14, R15 | Nothing | Can ride along with A and B |
| 🔴 E | **Parity and integrity** | R4's drawdown kill on the backtest path, R8, R12's slippage in fill rows, D2 | The drawdown kill goes through the safety doctrine and the rehearsal rule | Separate PRs, none blocks A to C |
| 🔴 F | **R13** | Decide whether to build the neighbour check | The user | A decision, then maybe one PR |

After these: Step 1 (baseline, waits on R2), then Steps 2 to 5.

**Open questions for the user** (package A, investigated 2026-11-15; all four decisions
and the score-composition answer implemented the same day, merged as PR #184; nothing is
open)

What the read-only investigation found:

- Rules are checked in two places that have drifted apart: the YAML models
  (`stage_configs.py`) and each stage's own settings class. Settings built in Python (the
  replay hook) only meet the second. Both paths build a `StagedPipelineConfig`
  (`research_hooks.py:726`, `cli/commands/research.py:1914`,
  `run_experiment_pipeline_cycle.py:419`), which today checks only that the list is not
  empty. That is the one place a rule can cover both.
- `step_days >= test_days` rejects the hook's own layout (45 / 30 / 15 on 90 days: two
  folds whose test windows share 15 days). A non-overlapping layout on 90 days leaves one
  fold, or two with a 30-day train window that the 45-day selection window would overlap.
- `ma_sweep_staged.yaml` fails the range guard: its first test fold starts 2023-01-01 and
  the two selection stages end 2024-12-31 and 2025-03-31. It passes the other two rules
  (9 folds, `min_folds_passed` 6, step equal to test).
- `exp_mc_smoke_test.yaml` has thresholds that pass everything (`min_sharpe: -99`,
  `min_trades: 0`) and needs `smoke: true` under the vacuous-threshold rule.
- A minimum test span worth having (about 60 sessions for a Sharpe standard error near 2)
  would also reject the hook, whose test folds hold about 22 sessions. It cannot be met
  before longer history.
- Train-failed folds: `FoldValidationInput` already has `train_passed`, `test_passed` and
  `fold_passed`; what a train-failed fold lacks is test numbers, because the test window
  is never simulated for it. How often this happens on real bars is not measured (the
  harness runs open filters).
- `WindowType` is used nowhere in `src/` or `tests/`. An expanding window would not add
  test folds: the number of folds is set by the span left after the first train window
  and the step, in both layouts.
- R5 has two layers. The composite score spreads a missing component's weight over the
  present ones (same metrics: 0.341 with all five, 0.394 without regime, 0.364 without
  walk-forward, 0.550 with only the overfitting component). Then the ranking fills any
  missing input with 0.5. See R16 for what that does to the deployable decision.

Decided by the user 2026-11-15 (asked one at a time):

1. Overlapping test folds: the rule is on; the replay hook carries a named exception until
   package B extends the window, then the exception is deleted. (User: the real answer is
   a longer window. Agreed; that is package B. Longer train or test stretches inside the
   same 90 days would leave less room to slide, not more.)
2. Train-failed folds: simulate the test window anyway, as evidence only; the fold still
   fails.
3. R5: a minimum evidence set (walk-forward and Monte Carlo) before a composite score or
   a deployable flag. Implemented 2026-11-15 together with the answer to the user's
   follow-up question, next item.
4. Defaults, stated and not objected to: one validator at `StagedPipelineConfig`; fix the
   dates in `ma_sweep_staged.yaml`; delete `WindowType`; the hook's 90-day lookback
   becomes a setting with the same default; folds in sessions, the minimum test span and
   the repo-level holdout date move to package B.

Open, raised by the user 2026-11-15: "if we are doing combined scores and a few of these
are report only now should we rethink this robustness score's composition overall?"
Proposed, not decided: while regime, shock stress and overfitting are report-only, the
scores that decide anything use walk-forward, Monte Carlo and the cost re-simulation
only; the three report-only numbers are shown with zero weight and get weight back when
their gate's `enforce` is switched on. With five components supplied, the three carry
about 41% of the robustness score's weight today (0.35 of 0.85) and are three of the four
inputs to the deployable flag. Measure first (R16's first box), then bring the numbers to
the user as its own decision. R5's change waits for that. Measured 2026-11-15, see R16.

**Answered by the user 2026-11-15: "Trusted evidence only".** The deciding scores use
walk-forward and Monte Carlo only; regime, shocks and overfitting are computed and shown
with zero weight and get weight back when their gate is switched on; a strategy missing
walk-forward or Monte Carlo gets no score. One difference from the proposal above: the
cost re-simulation is not in the scores. It is a pass / fail gate with no 0-to-1
component of its own (the "stress" component is the shock survival rate), and a
strategy that fails it never reaches scoring. Implemented the same day, see R5 and R16.

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

### Step 0 — Pipeline integrity (audited 2026-11-07; R1 and R10 merged, R11 / R12 / R15 merged as PR #183 and deployed 2026-11-16, package A merged as PR #184 and deployed 2026-11-16, the rest open: see "Where we are")

Three suspicions the user raised while studying the backtesting funnel, each confirmed by
reading the code (three parallel audits, key claims re-verified by hand). Fix these before
trusting any before/after number from Steps 1–2.

Order from here (agreed with the user 2026-11-15; the 2026-11-14 proposal had longer
history second, the user moved it after the audits):
1. The low-spread question under "R1 verification" (user: "we need to solve any obvious
   unknown"). Resolved 2026-11-15: the resampling is correct; the cause is a simulator
   defect, recorded as R10. Not an R1 defect. R10 is fixed on its own branch
   (`fix/r10-sim-stale-marks`) and goes before R4 (user, 2026-11-15: "i imagine this
   needs fixing now before anything else?", then chose the fix), because every gate R4
   audits reads the curves R10 distorted.
2. One PR from `research/r1-monte-carlo-bootstrap`: R1, the R1 follow-up (kept, user
   2026-11-15), the small R2 set with these notes. R2 stays open; the PR is "R2,
   partial". Merged as PR #181; deployed to A 2026-11-15 07:02 UTC (`15b4d4b9d`), B on
   the same commit.
3. R4, starting with the stress gate: in every real-bars run on 2026-11-14 it eliminated
   every strategy that reached it. Audit done 2026-11-15 for all four gates; the fixes
   it produced are R11, R12 and R15 (implemented) and R13, R14 (open).
4. R5 and R6 (placed here on the same reasoning as item 5; the user did not name them).
   Done 2026-11-15 as package A, together with R14, R16 and the code-only half of R2;
   merged PR #184.
5. Longer history, then the R2 tasks that wait on it (selection / evaluation / holdout
   split). User's reasoning: running on a longer window is mostly the same code over more
   days, so audits and fixes come first and the window is extended once the gates are
   trusted. Step 1 still waits on R2.
6. R3, before Step 5 (it changes cost, not what a score means).

**R1 — Monte Carlo is not a Monte Carlo (done: merged as PR #181, deployed 2026-11-15).**
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
`4dd0671ae`; merged in PR #181).**
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
  2026-11-14, reverting is a small change if the old leniency is wanted. Decided
  2026-11-15 with the corrected framing: keep it (user: "lets keep").
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
  - Explained 2026-11-15 (R10): the four low spreads, and the 1.54, come from one-bar
    equity spikes the simulator creates when a held symbol has no bar. The spikes cancel
    inside the session, so they add to bar variance and not to session returns. With the
    marks corrected all five spreads are 1.97 to 2.40. The reasoning as written on
    2026-11-14 follows. Independent session returns give a spread
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

**R2 — Walk-forward test folds are not out of sample (partial: the small set merged in
PR #181; package A's code-only boxes done, merged PR #184, deployed 2026-11-16; the rest open).**
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
- [x] Range guard: `StagedPipelineConfig` validation rejects a walk-forward stage whose
      first `test_start` is not after every earlier selection stage's `end_date` (or the
      YAML declares a `holdout_from` date that earlier stages may not cross). Fix
      `ma_sweep_staged.yaml` accordingly.
      Done 2026-11-15 (package A, merged PR #184): `research/pipeline/pipeline_validation.py`, called from
      `StagedPipelineConfig.__post_init__`. `ma_sweep_staged.yaml`'s two selection stages
      now read 2022 only; its thresholds were not re-run on the shorter windows (said in
      the file). The `holdout_from` date is R6's second box (package B).
- [x] Feed all folds into the overfitting evidence (train-failed folds with test metrics
      null and `test_passed=False`), and report `folds_skipped_at_train`.
      Done 2026-11-15 (package A, merged PR #184), built differently from the text above (user's decision 2): the test
      window is simulated for train-failed strategies too, so the fold carries real
      test numbers with `train_passed=False`, `fold_passed=False`. No fold is skipped
      any more, so there is no skipped count; the per-fold log line reports how many
      failed train. Gate decisions are unchanged (a fold counts only if both pass).
      What changes: `fold_consistency` and the train/test degradation now cover every
      fold, so a strategy that failed train on one of two folds reads 0.5, not 1.0.
      Cost: one extra test-window run per train-failed strategy per fold. Tests:
      `TestWalkForwardTrainFailedFolds` in `tests/research/pipeline/test_robustness_stages.py`.
- [ ] Found while doing the box above (2026-11-15), not fixed: a walk-forward stage's
      "entered" count in the funnel is `len(filter_outputs)`, which is one verdict per
      strategy per fold per window (now strategies x folds x 2), not the number of
      strategies that entered. `StageResult.n_entered` and `n_failed` are wrong for
      this stage; `n_passed` is right. Shown in the hook's `stage_funnel`, the CLI and
      the stage metrics.
- [ ] Gate on a pooled or median test Sharpe across folds in addition to per-fold pass
      counts.
- [x] Validate `step_days >= test_days` and `min_folds_passed <= n_folds` (split from the
      box above). Done 2026-11-15 (package A, merged PR #184): both in `WalkForwardStageConfig.__post_init__`, so YAML and
      Python-built configs meet them. Overlap needs `allow_overlapping_test_folds: true`,
      which the replay hook sets (user's decision 1) until package B lengthens its window.
- [x] Either implement EXPANDING/ANCHORED windows or delete `WindowType` and fix the docs
      (`docs/backend/research/advanced_validation_framework.md`).
      Done 2026-11-15 (package A, merged PR #184): deleted (nothing used it); the doc says folds are rolling only.
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
    per run by default (`--max-days`) and follows `ALPACA_DATA_FEED`. (All three open points
    in the next sentences were investigated 2026-11-16: see "Package B investigation"
    under Step 5.) Unchecked: how far
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
    - [x] The hook's lookback is a hard-coded 90 days (`research_hooks.py:486`). Loading
          more history changes nothing for the hook until this is a setting, or until
          long-window research runs from an experiment file.
          Done 2026-11-15 (package A, merged PR #184): `research.options.lookback_days`, default 90
          (`DEFAULT_RESEARCH_LOOKBACK_DAYS`). The fold lengths (45 / 30 / 15) are still
          fixed numbers, so a longer lookback alone adds folds but not longer ones; that
          is package B's fold-geometry box.
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
          survivors and stored with the candidate. Decided 2026-11-15: report only, it
          eliminates nobody. Whether it becomes a final pass/fail is a question to come
          back to (section 4, "Holdout as a gate").
    - [ ] Fold geometry in the hook: test folds that do not overlap (`step_days >=
          test_days`), no train window covering another fold's test days, and a pooled or
          median pass rule in place of "any one fold" (`min_folds_passed=1`).
    - [x] The R2 and R6 validators cover stage configs built in Python, not only YAML;
          otherwise the hook can drift from its own rules the way the four-stage file did.
          Done 2026-11-15 (package A, merged PR #184): single-stage rules sit in the stage's config class, cross-stage
          rules in `StagedPipelineConfig`; the hook builds both.
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

**R3 — Research checkpointing is built but never instantiated (cost/convenience; not
started).**
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
- [x] Housekeeping while in the file: the replay hook's "42-day lookback" comment is
      stale, the lookback is 90 days **[7.10 #10]**. Already fixed in `e5bbab498` (the R2
      small set); ticked 2026-11-15: the comment at `research_hooks.py:487` now says 90
      days.

**Verify:** the e2e test above; `research inspect-checkpoints` lists a real run's store.

**R4 — Audit the remaining gates the same way (started 2026-11-15: notes below for
stress, overfitting, parameter stability and regime, problems recorded as R11 to R15).**
Regime, stress, overfitting and parameter-stability stages (`pipeline/stages/regime_stage.py`, `stress_stage.py`,
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
- **Stress gate, audit note (2026-11-15, code at `b3b82ee67`).**
  - What it measures. Two checks, both must pass (`gates/stress_gate.py:98-146`).
    Shock: seven fixed changes applied to the bar returns of a run already made
    (`validation/stress_test_service.py:83-126`, `:241-280`), no re-simulation, the
    trades stay as they were. A scenario is survived when the changed curve has Sharpe
    ≥ 0 and drawdown no worse than -40%. At least half must be survived, so 4 of 7.
    Cost: the strategy is re-simulated with slippage and commission at 2× and 3×; a run
    is survived on the same two thresholds; at least half, so 1 of 2.
  - Real inputs. The hook's thresholds (`research_hooks.py:675-683`) are the defaults.
    The baseline is the Monte Carlo base run, reused when window and symbols match
    (`_gate_stage_support.py:60-83`), so the curve is five-minute bars over the whole
    window: 4,438 bars in the run below.
  - Result on real bars: the production `StressTestService` with the hook's thresholds,
    run on the 28 traded base curves saved from the post-R10 run. Survivors per scenario:

    | Scenario | What it does to the curve | Sharpe > 0 (8 strategies) | Sharpe ≤ 0 (20) |
    |---|---|---|---|
    | `volatility_spike_2x`, `_3x` | every bar return × 2, × 3 | 8, 8 | 0, 0 |
    | `return_shock_neg5pct`, `neg10pct` | one bar at the midpoint loses 5%, 10% | 0, 0 | 0, 0 |
    | `drawdown_amplification` | every losing bar loses twice as much | 0 | 0 |
    | `trend_reversal` | every bar return changes sign | 0 | 20 |
    | `liquidity_collapse` | every bar pays 0.50% | 0 | 0 |

    Every strategy with a positive Sharpe survives the same 2 of 7, every other one the
    same 1 of 7. The best is 2 of 7 and the gate needs 4. The shock check alone fails
    every strategy, so the cost check never decides anything. Recorded as R11.
  - Why, on the best curve (composite rule, Sharpe 2.34, return 2.95%, drawdown 1.75%):
    - × 2 and × 3: Sharpe stays 2.34 (mean and volatility scale together), drawdown
      becomes 3.5% and 5.2%. Passes for any positive Sharpe unless three times the
      drawdown exceeds 40%.
    - One bar of -5% / -10%: return becomes -2.19% / -7.34%, Sharpe -0.77 / -1.44. The
      sign of Sharpe is the sign of the average bar return, so surviving needs more than
      about 5% / 10% earned inside the window. Over 57 sessions no curve here has that.
      The same strategy over a year would pass, so this scenario measures the length of
      the window.
    - Losing bars doubled: return becomes -28.97%, Sharpe -18. Surviving needs gains per
      bar of twice the losses per bar. A five-minute curve is close to half up bars and
      half down bars of similar size, whatever the strategy.
    - Sign change: Sharpe becomes -2.34. A profitable strategy always fails; a losing
      one always passes.
    - 0.50% per bar: 4,437 bars of it, return -100%. The penalty is per bar and there are
      78 bars a day.
  - Cost check, measured on the same run (R12): research charged 0.011 to 0.062 basis
    points per dollar traded. Estimated with the trades held fixed (the cost
    re-simulations were not re-run): 7 of the 8 positive strategies keep a positive
    return at 2× and at 3×; the one that does not traded 684 times its capital.
  - A strategy that never trades passes. A flat curve has Sharpe 0.0
    (`risk_metrics.py:91-92`) and the test is `>=` 0.0: it survives 4 of 7 (measured on a
    flat 2,000-bar curve). A cost re-simulation with no trades also reports Sharpe 0.0
    (read, not run). What stops such a strategy today is the earlier stages' trade
    minimums, not this gate.
  - Passes with no evidence. No usable baseline curve (`stress_stage.py:262-263`) or no
    cost results (a runner returning `None`, `_gate_stage_support.py:117-118`) each add
    a warning and no failure (`stress_gate.py:117-118`, `:134-135`); with both missing
    the verdict is a pass. `test_missing_evidence_is_warned_not_failed`
    (`tests/research/pipeline/gates/test_stress_gate.py:140-145`) asserts that pass.
  - What the tests prove. The gate test's "steady" strategy
    (`test_stress_gate.py:33-35`) earns 0.04% per five-minute bar: measured Sharpe 109.5,
    +119.5% in 2,000 bars (about 26 sessions), survives 5 of 7. Its "fragile" one
    (`:38-40`) measured Sharpe -6.5, -17.3%. No test puts a curve of realistic size
    through the gate. The stage tests use a scripted fake runner
    (`tests/research/pipeline/test_robustness_stages.py`), and the full-pipeline test
    sets `min_shock_survival_rate=0.0`.
  - Not done: the per-strategy failure reasons of the 2026-11-14 runs were not saved, so
    the table is a re-computation on the saved curves, not a read-back of those runs.
    The cost re-simulations were not re-run.
- **Overfitting gate, audit note (2026-11-15; first read by a Sonnet agent, every line
  below re-read by hand unless it says otherwise).**
  - It has never run on real data: stress has eliminated every strategy before it.
  - What it measures. A weighted average of up to seven indicators, each between 0 and
    1 (`validation/overfitting_analysis.py:168-176`, `:304-317`). The code calls the
    result a probability; it is a score. A strategy fails above 0.6
    (`gates/overfitting_gate.py:177-178`; hook `research_hooks.py:694-699`). Weights:
    drop from train Sharpe to test Sharpe 0.30, spread of test Sharpe across folds 0.20,
    share of Monte Carlo paths with Sharpe not above zero 0.20, share of regime buckets
    without a positive Sharpe 0.15, fewer than 10 trades 0.05, share of absolute return
    in the top 10% of bars 0.05, parameter fragility 0.05. A missing indicator drops out
    and the others are re-weighted.
  - Nothing in it depends on how many strategies were tried. The usual defence against
    overfitting, a stricter bar the more candidates are tested, is absent.
  - Folds. Train 45, test 30, step 15 calendar days (`research_hooks.py:560`,
    `:605-607`). `_generate_folds` (`walk_forward_stage.py:388-411`) gives two folds on a
    90-day window, whose test periods share 15 days, and one fold on 75 to 89 days. A
    fold reaches the gate only if its train run passed the train filter
    (`walk_forward_stage.py:156-164`), so the train Sharpe is already a selected one.
    - Drop = (mean train Sharpe − mean test Sharpe) / |mean train Sharpe|
      (`walk_forward_validation.py:114`), halved and clipped to 0..1. With two folds it
      is a ratio of two averages of two numbers, each a Sharpe over 30 or 45 days.
    - Spread = standard deviation of the test Sharpes / |their mean| (`:117-120`), with
      two folds the spread of two numbers. It grows without limit as the mean nears
      zero, and two equally negative test Sharpes read as perfectly stable.
    - With one fold the spread is left out (`overfitting_analysis.py:211-214`, the R2
      change).
  - Monte Carlo share. The same resampled paths of the same base run that already passed
    the Monte Carlo gate, so the same evidence is counted a second time (agent's reading
    of `monte_carlo_aggregator.py:238`; not re-read).
  - Regime share. Every bucket that has any Sharpe counts, with no minimum number of
    bars (`overfitting_analysis.py:249-255`), across five regime dimensions. The regime
    gate uses two dimensions and needs 390 bars per bucket. A bucket of one day weighs
    the same here as one of thirty.
  - Passes with thin evidence. Fewer than two of the four main indicators gives a pass
    with a warning (`overfitting_gate.py:162-175`, hook `on_insufficient_evidence="pass"`).
  - Parameter fragility is never supplied: the stage passes no sensitivity score
    (`overfitting_gate.py:152-159`), so that indicator is always missing.
  - Tests (agent's reading, not re-read): hand-set Sharpes of 1 to 3 with three folds,
    and a scripted runner with one strongly overfit strategy. Nothing with two folds or
    with Sharpes the size of real ones.
  - Not measured: what the gate does on real folds. The agent's rough simulation
    suggested most strategies with no edge would pass; it fixed two of the indicators by
    hand and is not a result. The first real reading needs R11 done. Recorded as R14.
- **Parameter stability, audit note (2026-11-15; same method).** It is not in the funnel.
  `ParameterSensitivityAnalyzer` has one caller, `ValidationOrchestrator`
  (`validation/validation_orchestrator.py:426-438`), behind
  `enable_parameter_sensitivity`, default off (`:82`), and three request fields that no
  code in `src/` sets (searched for `sensitivity_run_fn`: no assignment outside the
  orchestrator). The validation layer runs on final survivors only. So no stage checks
  whether a strategy still works with its parameters moved slightly, which is the plain
  meaning of parameter stability. If it were switched on: the score is the standard
  deviation of Sharpe over its range across the sweep (agent's reading of
  `parameter_sensitivity_analysis.py:192`, not re-read), which does not depend on whether
  the Sharpes are good or bad. Recorded as R13.
- **Regime gate, audit note (2026-11-15; first read by a Sonnet agent, the pass rule and
  the hook's settings re-read by hand, then measured).**
  - What it does: each session gets a trend label and a volatility label from the bars
    (`regime_labels.py`, 10/20-day trend windows, volatility percentiles 80/20). The
    strategy's five-minute returns are grouped by label. A label is judged only with at
    least 390 bars, about 5 sessions (`research_hooks.py:649-658`), and a dimension only
    with two such labels. The strategy fails if, in either dimension, the worst label's
    Sharpe is under -1.0, its drawdown is past -20%, or fewer than half the labels made
    money (`regime_gate.py`). With too little coverage it passes (`on_insufficient_
    coverage="pass"`).
  - Measured on the 28 traded strategies of the post-R10 run, with the production
    classifier, analysis service and gate (`regime_on_real_curves.py` in the session
    scratchpad). The 60 sessions label as: trend bear 30, bull 19, sideways 2, no label
    9; volatility normal 42, high 18, low 0. So each dimension is judged on two labels,
    and the smaller one is 18 or 19 sessions. The 9 unlabelled sessions (the classifier's
    warm-up, with the history loaded on B) drop out of the trend dimension.
  - Result with the real labels: 4 of 28 pass. All 20 with Sharpe at or below zero fail;
    4 of the 8 above zero pass.
  - Control: the same labels dealt to random days, 100 times per strategy, so that a
    label says nothing about the market that day. The gate still fails the best strategy
    (Sharpe 2.34) in 48 of 100, the second (1.53) in 55, and the eight profitable ones in
    73 of 100 on average (48 to 91). Losing strategies fail 92 to 100.
  - Why: an annualised Sharpe measured over 19 sessions has a standard error of about
    3.6 (√(252 ÷ 19)). A floor of -1.0 is under one standard error below a true Sharpe
    of 2, and there are four such buckets. The per-label numbers show the scale: the best
    strategy's labels range 1.07 to 2.67, the fourth's 3.87 in bear and -5.37 in bull.
  - So on this window the gate fails every losing strategy, which the earlier stages
    already do, and among profitable ones its verdict is not distinguishable from chance:
    4 of 8 passed with the real labels where random labels pass 2.2 of 8 on average, one
    draw. Three strategies of one family (c25, c27, c20) earn in bear and lose in bull
    sessions; whether that is a real dependence cannot be told from 19 sessions.
  - The -20% drawdown rule never fired: no failure reason of the 28 names it, and the
    worst per-label drawdown among the top eight is -3.2%. Inside one label over 60
    sessions it is out of reach.
  - Not confirmed, from the agent's read only: several branches pass on thin coverage
    (no label provider, fewer than two judged labels); none was hit on this run, all 28
    were judged in both dimensions. The tests were not re-read by hand.
  - Recorded as R15.
- [x] One audit note per stage under this heading (what it measures, where its inputs
      really come from, what the tests prove, fail-open paths); each confirmed problem
      becomes its own R-number with a fix list. Done 2026-11-15: stress (R11, R12),
      overfitting (R14), parameter stability (R13), regime (R15). The "Verify" line
      below is not met: no stage has a test on a real runner where the gate fails (R7).
- [ ] First confirmed gap to carry in: nothing on the backtest trading path reads the 12%
      `max_drawdown_limit`, so a research run never experiences the kill that the live
      path would apply at the same drawdown **[9.8 #5]**. Research scores therefore
      overstate strategies that would have been halted. Wire it (observe-mode first, as
      a recorded event; then enforce) and rerun the two-year backtest with
      `ACTIVATE_KILL_SWITCH`. Read `docs/architecture/safety-doctrine.md` first; this is
      a parity item, so it goes through the rehearsal rule.

**Verify:** every stage has a note here, and each stage has at least one test on a real
runner where the gate fails.

**R5 — Composite score hides missing evidence (implemented, merged PR #184, deployed 2026-11-16).** The validation framework
redistributes an absent component's weight to the present ones
(`docs/backend/research/advanced_validation_framework.md`, "When a component is absent"),
so a strategy with no Monte Carlo or regime evidence is never penalised for the gap. After
R1 that is exactly the hole that lets a thinly tested candidate outrank a well-tested one.
- [x] Decide and implement one of: a missing-evidence penalty per absent component, or a
      required minimum evidence set before a composite score is produced at all (score
      `None` + reason otherwise). Record the choice here.
      Done 2026-11-15 (package A, merged PR #184). Choice (the user's): a required minimum
      set, walk-forward and Monte Carlo. One object says which components count,
      `EvidencePolicy` in `research/validation/evidence_policy.py` (`deciding` and
      `required`), and the three scores read it: the robustness score
      (`RobustnessScoreBuilder`), the ranking composite and the deployability score
      (`CandidateRankingService`). Built differently from the text above in one
      respect: a strategy missing required evidence gets 0.0 with
      `evidence_complete=False` and `missing_evidence=(...)`, not `None`. The scores
      are stored in Parquet columns that do not allow nulls, and a `None` would have
      changed three schemas and every reader; the flag says the same thing. Such a
      strategy fails `overall_passed`, is never deployable (even at a threshold of 0)
      and ranks below every scored one. `EvidencePolicy.unrestricted()` gives the old
      arithmetic for analysis. Docs: `advanced_validation_framework.md`, "Evidence
      policy".
- [x] Test: identical metrics with and without the Monte Carlo component must not produce
      the same (or a higher) composite score for the one missing it.
      Done 2026-11-15: `tests/research/validation/test_robustness_scoring.py`
      (`TestDefaultEvidencePolicy`: missing walk-forward, missing Monte Carlo, only the
      overfitting component, nothing at all; "less evidence never scores higher than
      more") and `tests/research/intelligence/test_candidate_ranking.py` (missing
      evidence is not scored, ranks last, not deployable at threshold 0). The
      end-to-end smoke test now also asserts that every survivor the hook scores
      arrives with both pieces of evidence, since a hook that stopped passing one
      would otherwise silently seed nothing.

**R6 — Experiment-config and holdout validation (validator done, merged PR #184, deployed 2026-11-16;
holdout date moved to package B).** R2's range guard belongs
in a general validator so the rules are enforced, not written in docs: no stage may read
past a declared holdout date; walk-forward `test_start` after every earlier selection
stage's `end_date`; `step_days >= test_days`; `min_folds_passed <= n_folds`; vacuous
thresholds (e.g. `min_sharpe <= 0` with `min_trades == 0`) rejected unless the YAML says
`smoke: true`. Home: `research/config/stage_configs.py` validation plus the existing
`atp research validate-config` / `plan-experiment` commands. This is also what makes the
suite `v1` rule "holdout never tuned on" (`pre-launch-deployment-plan.md` §5) enforceable.
- [x] Validator + tests; the current `experiments/staged/ma_sweep_staged.yaml` must fail
      until fixed, `exp_mc_smoke_test.yaml` must carry `smoke: true`.
      Done 2026-11-15 (package A, merged PR #184). Home changed from the text above: the rules live in the stage config
      classes and in `StagedPipelineConfig`, not in `stage_configs.py`, because the
      replay hook never goes through the YAML models. `smoke: true` waives the two
      cross-stage rules only. Tests: `tests/research/pipeline/test_pipeline_validation.py`
      (16, including the old `ma_sweep_staged.yaml` layout failing and both experiment
      files loading). Rules table: `docs/backend/research/research_robustness_stages.md`.
- [ ] A repo-level holdout declaration (one date, one place) that every experiment YAML is
      checked against. Moved to package B (2026-11-15): a holdout needs history long
      enough to set one aside.

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

**R10 — Simulator marks a held position at its last-fill price when its bar is missing
(found and fixed 2026-11-15; merged as PR #182, deployed to A 2026-11-15 20:31 UTC
(`b3b82ee67`), B on the same commit; it distorted Sharpe and volatility on every run).
Line numbers below are the engine before the fix.**
- What happens. `Position.market_price` is written only when a fill is applied
  (`simulation_execution_engine.py:1038-1048`) and on a split; the bar loop never
  refreshes it. `_calculate_positions_value` (`:1160`) and `_build_position_rows`
  (`:1200`) use it as the price of any held symbol that has no bar at the timestamp. So
  for that bar the position is valued at the close of the bar of its last fill, and at
  the symbol's next bar it returns to the market. Equity moves by quantity × (latest
  close − price at last fill) and back.
- How often. In the 60-session window (2026-08-15 to 2026-11-13, IEX five-minute bars,
  22 symbols) 96.6% of timestamps have at least one symbol with no bar. Share of
  timestamps with no bar: DIA 83%, COST 71%, MA 50%, TLT 42%, ADBE 39%, CRM 38%, HD 38%,
  UNH 34%, V 21%, AMD 20%, the other twelve under 10%.
- Proof (the 30-strategy real-bars run of "R1 verification" repeated on the laptop with
  each base run's equity curve and fills saved; 28 strategies traded). Equity rebuilt
  outside the simulator from its own cash column, its fills and the bars, with missing
  bars marked at the close of the last fill's bar, matches the simulator's curve on every
  bar of all 28 runs (largest difference 3e-11 dollars). Rebuilt with the latest close
  instead, it differs by up to $65 to $1,446 on a $100,000 account, depending on the
  strategy.
- Effect on Sharpe (bar returns; "corrected" = latest-close marks, same fills and cash):

  | Strategy | Trades | Sharpe, simulator | Sharpe, corrected | Spread, simulator | Spread, corrected |
  |---|---|---|---|---|---|
  | composite rule (passed the hook's Monte Carlo) | 4,139 | 1.61 | 2.35 | 1.56 | 2.32 |
  | mean reversion (passed the hook's Monte Carlo) | 203 | 0.70 | 1.53 | 0.94 | 2.03 |
  | mean reversion | 155 | -0.14 | -0.66 | 0.52 | 1.97 |
  | moving-average crossover | 729 | -0.04 | -0.42 | 0.60 | 2.27 |
  | moving-average crossover | 448 | -0.07 | -0.84 | 0.49 | 2.40 |

  The spikes cancel within the session, so total return is unchanged and bar variance is
  inflated: Sharpe is pulled toward zero, good and bad alike. For the other 23 strategies
  the Sharpe moves by a median of 0.015 (largest 0.06). Max drawdown moves by under 0.4
  percentage points everywhere (these curves draw down 0.2% to 3.5%).
- The bootstrap is not at fault: its 2,000-path spread equals √(bars per year / bars) ×
  √(sum of squared session sums / sum of squared bar returns) for every strategy, and a
  plain re-computation of 200 paths per strategy (concatenate the bar returns, mean / std)
  matches it to rounding.
- Not measured: the corrected curve keeps the simulator's fills. Order sizes read the
  previous bar's equity (`capital_scale`, `:303`), so a fixed simulator will size some
  orders differently and its numbers will not equal the "corrected" column exactly. The
  gates were not re-run on corrected curves, so no verdict change is claimed. Which
  positions drive the five large cases (holding time, symbol) was not broken down.
- Reach. Every user of `SimulationExecutionEngine`: all research stages, the bench hooks,
  the rotation hooks, and the worker's bench re-sim that A's portfolio review reads
  (`portfolio_review_mode` is `off`, so no decision has used it). Live and paper trading
  do not use it; the broker sync takes the broker's `current_price`
  (`broker_runtime_sync_service.py:415-426`, seen, not traced further).
  `simulated_broker_client._marked_equity` is a separate marking path and was not read.
- Second stale value, same cause (read 2026-11-15, not measured). `Position.unrealized_pnl`
  and `market_value` are also computed only at a fill (`position_ledger_service.py:109-126`).
  The engine writes `position.unrealized_pnl` into the per-symbol position rows (`:1214`)
  and the per-bar metric rows (`:1078`) on every bar, so those columns hold the value from
  the last fill on every bar, whether or not the symbol has a bar. Equity does not use
  them. Files that mention these rows: `result_recorder_service.py`, `regime_join_service.py`,
  `regime_analysis_service.py`; whether any gate reads the stale column was not traced.
- The platform backtest already does it the other way: `simulated_broker_client._marked_equity`
  (`:268-285`) marks each position "at the close of the last bar stamped before the tick",
  and its docstring says research "marks every bar". So the fix moves research toward the
  platform side (parity 5c), not away from it.
- Orders for a symbol with no bar (read 2026-11-15, not measured). `_construct_orders`
  creates no order for a symbol with no price on the bar (`:822`). An order already
  scheduled for a later bar is dropped if the symbol has no bar there:
  `simulated_execution_service.fill` skips it with no fill and no carry-forward
  (`simulated_execution_service.py:98-100`), and the engine releases its cash
  reservation. So a fill never happens without a bar (the `fill.price` fallback at
  `:1038` cannot be reached), and with delayed fills an order for a thin symbol can
  vanish where a broker would leave it open. How many orders that is on real bars was
  not counted; see the unchecked box below.
- Strategies and their positions (checked 2026-11-15, no defect). In the simulator
  `StrategyContextBuilder.build_from_window` takes `positions` and does not use it, so
  `context.position` is always the empty default. The same is true everywhere: nothing
  in `src/` reads `context.position`, and `StrategyPositionSnapshot` is defined and never
  constructed, the live path included. Research and live agree (a strategy is never told
  what it holds), so there is no parity gap and nothing to fix. It is a dead field; it
  starts to matter only if a strategy is written to read it, and then both paths need it
  filled the same way.
- The platform backtest's broker keeps an order open when the symbol has no bar
  (`simulated_broker_client.py:209`, "leave as open/pending"); the research fill service
  drops it. The fill settings the research runs really use (recorded from the runs, box
  below): fill at the current bar's close, no fill delay (`latency_bars=0`), at most 5%
  of the bar's volume (`max_volume_participation_rate=0.05`), no execution policy, no
  settlement delay. So an order is created only on a bar the symbol has and fills on
  that bar; the only orders that wait for a later bar are the remainders the 5% cap
  leaves over.
- Fix options:
  - (a) The engine keeps the latest close per symbol, updated from each bar, and uses it
    as the fallback in `_calculate_positions_value` and `_build_position_rows`. Fixes
    equity and the recorded price. Leaves `unrealized_pnl` in the rows stale unless it is
    also computed there from the same mark.
  - (b) After a bar's fills, refresh each held position that has a bar: `market_price`,
    `market_value`, `unrealized_pnl` from that close. The existing fallback then already
    reads the latest close, and every reader of the position sees current values.
    Recommended 2026-11-15 after the reading above (the first recommendation was (a),
    before the stale `unrealized_pnl` was seen). Cost: one position object rebuilt per
    held symbol per bar, negligible beside the strategy evaluation. A name-based search
    found no strategy or sizer reading these three fields in the research path (not
    exhaustive), so the trades change only through sizing, the same as (a).
  - (c) Forward-fill the bars when the window is loaded. Rejected: strategies would see
    bars that never printed.
- [x] Decided (b) (user, 2026-11-15: "B seems to make the most sense"). Fix:
      `SimulationExecutionEngine._mark_positions`, called once per live bar right after
      the bar's prices are extracted. That is after the strategy was evaluated, so this
      bar's close reaches nothing a strategy could see, and before fills, sizing, the
      position rows and the equity row. Each held position with a bar is rebuilt with
      that close (price, value, unrealized P&L), the way the split path in the same file
      builds a position; a symbol with no bar is skipped and keeps its latest close. The
      shared `PositionLedgerService` was left alone (it is under `execution/`).
- [x] Tests: `tests/research/simulation/test_missing_bar_marking.py`, six tests on a
      two-symbol window where the held symbol has no bar at one timestamp. Before the fix
      five failed: equity fell $394.40 on the gap bar (493 shares × (101.40 − 102.20)) and
      unrealized P&L was 0.0 on every bar after the fill. All six pass after it. The
      other simulator tests (477 passed, 3 skipped) and the full suite without
      integration / external / alpaca pass; ruff clean.
- [x] Neighbours read: `_apply_fills` and orders with no bar (bullets above);
      `simulated_broker_client._marked_equity` already marks at the latest close.
- [x] The 30-strategy run repeated on the fixed engine (same bars, same harness):
  - The simulator's curve now equals the latest-close rebuild on every bar of all 28
    runs (largest difference 3e-11 dollars).
  - Sum of squared session sums / sum of squared bar returns is 0.87 to 1.33 for all 28
    (before: 0.05, 0.06, 0.07, 0.20 and 0.57 for the five in the table). Sharpe spread
    is 1.97 to 2.44 for all 28.
  - Sharpe, simulator after the fix, for the five: 2.34, 1.53, -0.66, -0.42, -0.83. The
    largest difference from the "corrected" estimate over all 28 is 0.017.
  - Trade counts changed for 19 of 28 strategies, by 1 to 16 trades (sizing reads the
    previous bar's equity). Max drawdown moves by under 0.4 percentage points (largest:
    2.3% → 1.9%, the 448-trade crossover).
  - Monte Carlo gate: 3 pass before, 4 after. The one change is the 2026-11-14 near miss
    (composite rule, 1,196 of 2,000 paths): base Sharpe 0.76 → 0.82, pass rate 0.598 →
    0.601. The other three pass on both. After the gate: regime 2 of 3 → 2 of 4, stress
    0 of 2 on both. No survivors on either.
- [ ] Parity 5c. Done: `tests/platform/test_research_trading_parity.py` (same signals,
      same trades) passes, and passed again 2026-11-15 with the 1 basis point cost and
      the regime change in. Not done: the re-sim vs platform daily-return correlation
      (0.993 to 0.998 on 2026-10-02). It is a measurement on the recorded six-month
      platform backtest, not a test, and was not re-run. Expected direction only: the
      platform side already marks at the latest close.
- [x] After merge and deploy to A and B (same SHA): re-run the bench re-sim on B and
      compare with `bench_resims_2026-11-14` (macd 0.87, mean reversion -0.16, momentum
      -1.21) before `portfolio_review_mode` goes to `advisory`. Done 2026-11-15 20:37 UTC
      on B at `b3b82ee67`: `worker bench-resim --as-of 2026-11-14 --run-id
      bench_resims_2026-11-14_r10 --no-pull --no-publish` (nothing posted to A; payload
      `artifacts/worker/bench_resims/bench_resims_2026-11-14_r10.json`, log
      `~/rehearsals/bench-resim-r10-2026-11-15.log`). Same window (2026-08-17 to
      2026-11-14), same dataset (`raw_bars_20261114T041002Z_c2faf2e7`), same 63 daily
      returns, market series identical.

  | Strategy | Sharpe, Saturday | Sharpe, rerun | Score, Saturday | Score, rerun | Trades | Daily returns, Saturday vs rerun |
  |---|---|---|---|---|---|---|
  | `macd_crossover_v1` | 0.87 | 1.21 | 1.727 | 1.863 | 2,704 → 2,704 | correlation 0.99997, largest daily difference 0.016% |
  | `mean_reversion_v1` | -0.16 | -0.19 | 1.313 | 1.303 | 2,643 → 2,646 | correlation 0.9998, largest 0.038% |
  | `momentum_v1` | -1.21 | -1.23 | 0.943 | 0.943 | 20,088 → 20,097 | correlation 0.999998, largest 0.003% |

  - Order of the three is unchanged on Sharpe and on score. Total return moves by at most
    0.008 percentage points, max drawdown by at most 0.07 points.
  - The Sharpe in the payload is computed on five-minute bar returns, which is where the
    spikes were. Computed from the 63 daily returns instead it barely moves (macd 1.144 →
    1.141, mean reversion -0.19 → -0.19, momentum -1.04 → -1.04), because the spikes
    cancelled inside the session. macd's bar Sharpe now sits next to its daily Sharpe
    (1.21 vs 1.14); on Saturday it was 0.87 vs 1.14.
  - Pairwise correlations of the daily series, which the redundancy check reads, move by
    0.001 or less (macd ~ mean reversion 0.54, macd ~ momentum 0.78, mean reversion ~
    momentum 0.62).
  - A still holds the Saturday submission (`bench_resims_2026-11-14`, pre-fix numbers,
    not applied because review mode is `off`). The rerun was not posted. The timed run on
    Sat 2026-11-21 posts the first post-fix submission. Whether a review started before
    then would read the Saturday one was not checked.
- [x] Dropped orders counted on real bars (2026-11-15, the 30-strategy run on the fixed
      engine with a counter around `SimulatedExecutionService.fill` and
      `SimulationExecutionEngine.execute`): 184 simulations, 883,119 orders handed to the
      fill service, 883,111 fills, 36 remainders carried to the next bar by the 5% volume
      cap, 8 orders with no bar when they came due, all TLT (42% of timestamps without a
      bar). 8 in 883,119 is not material: no code change, no R-number. It does become
      material if a fill delay is ever switched on (`latency_bars=1`, which
      `fill_model.py` calls the realistic setting): every order would then wait one
      timestamp, and for DIA 83% of timestamps have no bar. Before that switch, the fill
      service has to keep such an order open the way the platform broker does.
      The same run repeated the first post-fix run exactly (all 30 strategies: verdict,
      Sharpe, trades, pass rate, spread, return, drawdown; same funnel).
- [ ] R7: more sparse-bars fixtures (the new test file is the first). The synthetic bars
      of the R1 harness have a bar for every symbol at every timestamp, so they could not
      show this; whether any other simulator test has a gap was not checked.

**Verify (met 2026-11-15 on the laptop run above):** on the 30-strategy run, sum of
squared session sums / sum of squared bar returns is between 0.85 and 1.35 for every
strategy that traded, and the rebuild with latest-close marks matches the simulator's
curve to the cent.

**R11 — The stress gate's shock check cannot be passed by a strategy that trades (found
2026-11-15 under R4; option (a) chosen and implemented 2026-11-15, commit `b6860b6b7`; merged as PR #183, deployed to A and B 2026-11-16 03:38 UTC (`7cf1c0ba1`)).**
Correction to the title, found while writing the tests: a strategy that earned more than
about 10% inside the window would have survived both one-bar shocks and reached 4 of 7.
The best real strategy earned 2.95%. A synthetic Sharpe-3 curve with 5% earned reaches
3 of 7. Evidence and per-scenario reasons are in the R4 stress note. In short: the pass rule counts seven scenarios and needs four; two of them
(× 2, × 3) cannot change Sharpe, one (sign change) fails every profitable strategy, two
(per-bar penalty, doubled losing bars) are written per bar and applied to five-minute
bars, and two (one-bar -5% / -10%) pass or fail on how long the window is. On real bars
the result is fixed by the sign of Sharpe: 2 of 7 or 1 of 7. Nothing downstream of
stress has ever received a strategy.
- None of the seven uses anything the earlier stages do not already have: each is a
  fixed function of the same bar returns (drawdown, return over the window, gains
  against losses, the sign of the mean). The trades do not change, so no scenario shows
  how the strategy would have acted in the shock.
- Fix options ((a) chosen 2026-11-15, see the first box below):
  - (a) Take the shock check out of the pass rule and keep its numbers in the report.
    The gate is then the cost re-simulations alone, which only means something after
    R12. Smallest change.
  - (b) Keep a shock check but rewrite it in units that do not depend on bar size or
    window length: a one-day crash judged against a loss or drawdown limit and not the
    sign of Sharpe; drop the sign change and the × 2 / × 3 from the count; leave the
    liquidity case to the cost re-simulations.
  - (c) Lower the bar to 2 of 7. Not recommended: every positive Sharpe then passes and
    every negative one fails, which the earlier stages already decided.
  - Later, either way: the stress test the gate's own docstring describes and defers
    (`stress_gate.py:24-26`), which changes the bars and re-simulates, so the strategy
    trades through the shock.
  - Recommended, and agreed 2026-11-15: (a) now, with the zero-trade and no-evidence
    cases below closed, then R12, then the bar-changing re-simulation as its own item
    (not scheduled).
- [x] Decide (a) / (b). **(a), agreed 2026-11-15.** What changed:
  - The pass rule is the cost re-simulations alone (`stress_gate.py`,
    `evaluate_stress_gate`). The shock scenarios still run on every strategy and their
    results stay on the verdict (`shock_summary`, `shock_survival_rate`) and in the
    stage's debug log, marked report only.
  - `min_shock_survival_rate` is removed from `StressGateConfig`, the YAML model and
    the hook. A YAML file that still sets it is rejected with a message; without that
    the key would have been ignored silently.
  - `cost_multipliers` may no longer be empty. With the shocks out of the rule, an empty
    list would be a gate that passes everything.
- [x] A strategy with no trades in a stress run fails the gate (or is reported as not
      evaluated), not passed on Sharpe 0.0. A cost run with zero trades does not count
      as survived; the reason reads "2x costs: no trades". A zero Sharpe with trades
      still meets a threshold of 0.0.
- [x] Missing evidence fails, or the stage raises, when the config asked for that
      evidence; the test that asserts the pass changes with it. Any requested multiplier
      without a result fails the strategy, even when the others survive. A missing shock
      curve stays a warning, because the shocks no longer decide anything.
- [x] Tests on curves of realistic size (five-minute bars, a 60-session window, Sharpe
      between -2 and 3): the gate passes some and fails some, and the survivors change
      when the inputs change (R7). `tests/research/pipeline/gates/test_stress_gate.py`:
      curves at Sharpe 0.5 / 1.5 / 2.3 all survive the same two scenarios, -0.5 / -2.0
      only the sign change, 3.0 adds the -5% bar; same curve with different cost results
      gives different verdicts. Stage-level cases (zero trades, a cost run that returns
      nothing, the removed key) are in `test_robustness_stages.py`. The old "steady"
      (Sharpe 110) and "fragile" fixtures are gone.
  - Checked: the two stress test files pass (43 tests), `tests/research/pipeline`,
    `validation` and `config` pass (337), ruff clean, mypy error count on the touched
    files unchanged (all in untouched lines of `research_hooks.py`). The full suite
    passed later the same day on R11 + R12 together (5,396 passed, 5 skipped).
- [x] Rerun the 30-strategy real-bars run and record what reaches overfitting. That stage
      has never run on real data. Done 2026-11-15 on the R11 code (before the 1 basis
      point cost and before the regime gate went report-only; same bars and harness as
      the post-R10 run; 2,505 s, slower because tests ran beside it).
  - Funnel: 30 → 30 → 4 → 2 → 2 → 2 (quality, walk-forward, Monte Carlo, regime, stress,
    overfitting). Post-R10 it was 30 → 30 → 4 → 2 → 0. First run with final survivors:
    c24 composite rule (Sharpe 2.34, 4,139 trades) and c06 mean reversion (1.53, 203
    trades).
  - Stress: both pass both cost re-simulations. Shocks: 2 of 7 each, reported only.

    | Strategy | Sharpe at 1× / 2× / 3× cost | Return at 1× / 2× / 3× |
    |---|---|---|
    | c24 composite rule | 2.34 / 2.28 / 2.23 | 2.95% / 2.88% / 2.82% |
    | c06 mean reversion | 1.527 / 1.525 / 1.522 | 0.23% / 0.23% / 0.23% |

    At the old cost, tripling it takes 0.11 off the best Sharpe. The cost test cannot
    fail anything until the base cost is real, which is R12 (in the next run).
  - Regime, still enforcing in this run, removed the same two as before (c25, c27).
  - Overfitting: both pass, numbers under R14.
  - Audit pass, same run: all 30 strategies sent through regime, stress and overfitting
    whatever the funnel decided (901 s). Stress passes 7 and fails 23.
    - Pass: seven of the eight profitable strategies. c26, which trades 820 times its
      capital, only just: Sharpe 0.71 → 0.44 → 0.12 at 1× / 2× / 3×.
    - Fail, cost: c22 (684 times its capital), 0.10 → -0.16 → -0.43. The one strategy
      the gate removes that the sign of its Sharpe had not.
    - Fail, no trades: the two factor strategies that never traded, reason "2x costs: no
      trades". Before R11 these passed on a Sharpe of 0.0.
    - Fail, already losing: the other 20, Sharpe at or below zero before any multiplier.
  - "Verify" below is met by the audit pass: strategies pass and fail, and the reasons
    differ by cause.

**Verify:** on the 30-strategy run at least one strategy passes stress and at least one
fails it, and each failure reason names a cause that differs between strategies.

**R12 — Research trades almost for free (found 2026-11-15 under R4, measured; 1 basis
point per trade agreed and implemented 2026-11-15, commit `bb2f09329`; merged as PR #183, deployed to A and B 2026-11-16 03:38 UTC (`7cf1c0ba1`); re-baseline
done).** Every research and bench simulation is built by
`build_platform_execution_service` (`execution/clients/simulated_broker_client.py:47-59`):
zero commission, and slippage of 25 basis points × the order's share of that bar's
volume (`volume_share_slippage_model.py:23`, `:58-62`). Orders are capped at 5% of the
bar, so the most any fill pays is 1.25 basis points. There is no bid-ask spread on this
path: the spread estimate at `:64-75` is used only when a bar has no volume.
- Measured on the 28 traded strategies of the post-R10 run (each fill's price against its
  bar's close; every fill matched a bar): average cost per dollar traded 0.011 to 0.062
  basis points, largest single fill 1.24. The median order is 0.01% to 0.12% of the
  bar's volume. Total paid over the window: 0.000% to 0.505% of capital.

  | Strategy | Sharpe | Return | Traded (× capital) | Cost paid | Cost at 1 basis point per dollar traded |
  |---|---|---|---|---|---|
  | composite rule (best) | 2.34 | 2.95% | 126.9 | 0.075% | 1.27% |
  | composite rule | 0.71 | 1.15% | 820.2 | 0.505% | 8.20% |
  | momentum | 0.29 | 0.30% | 133.6 | 0.061% | 1.34% |
  | mean reversion | 1.53 | 0.23% | 2.4 | 0.000% | 0.02% |

  The last column is arithmetic for scale. What a realistic cost per trade is for these
  22 symbols was not established.
- What follows from it:
  - The stress gate's 2× and 3× multiply close to nothing (R4 stress note), so the cost
    check passes almost everything.
  - Sharpe, return and the bench score are all before any spread. Strategies that trade
    most are flattered most: `momentum_v1` on the bench made 20,097 trades in 63 sessions
    (`daily_turnover` 9.73 in the payload; its unit was not checked).
  - The share of volume is taken against IEX bars, which count IEX trades only, so it is
    larger than the share of the whole market. That pushes the other way; its size was
    not measured.
- The recorded slippage is always zero. `simulation_execution_engine.py:1219` writes
  `"slippage": 0.0` into every fill row; the cost is inside the fill price and nowhere
  else. All 4,139 fills of the best strategy show 0.0. Who reads that column was not
  traced.
- Calibration path, read 2026-11-15. `SlippageCalibrationService`
  (`research/calibration/services/slippage_calibration_service.py`) reads A's
  `fill_quality_metrics` rows and returns a snapshot that
  `VolumeShareSlippageModel.from_calibration_snapshot` (`:87-103`) turns into a model.
  Nothing outside `research/calibration/` calls either (searched `src/`), so research
  has never used a measured cost. As written it would not fix this: it divides the mean
  cost by an assumed order size of 2% of the bar (`calibration_snapshot.py:21`) to get
  the volume coefficient, and research orders are 0.01% to 0.12% of the bar, so the
  result still charges well under 1 basis point (arithmetic from its formula; the service
  was not run). The model has no cost that a small order pays, which is what a spread is.
- What A's paper fills say (read-only queries on A, 2026-11-15). Each row compares the
  fill price with the quote midpoint taken at submission (`execution_policy_engine.py:212`,
  `:247`), positive = cost. Two live strategies, 2026-10-19 to 2026-11-13, 2,628 fills,
  $4.08M traded, 2,628 of 2,634 submitted orders filled.
  - All fills: mean 5.65 basis points (standard error 1.57), median 0.62. The mean is
    not usable: 346 fills are more than 100 basis points from their reference and carry
    $2,790 of the $3,033 net.
  - Those 346 are a wrong reference price, not expensive fills. Eight symbols (UNH, HD,
    MA, V, ADBE, CRM, BKNG, COST) hold 311 of them, and the quote at submission was very
    wide on these fills (median half-spread 254 basis points against 4 for the rest). Example,
    HD on 2026-11-04: fills at 350.88 to 351.68 all afternoon, our own five-minute bars
    close at 350.5 to 351.9, and the reference alternates between about 340 and about
    351.5 from one order to the next. They split evenly four ways (buys above 109, buys
    below 78, sells above 73, sells below 86), so they add noise, not cost.
  - Fills whose quote was tight (half-spread under 10 basis points, where the midpoint
    can be trusted): 1,513 fills, $2.36M, all 22 symbols.

    | | Fills | Mean (bps) | Standard error | Median | Dollar-weighted |
    |---|---|---|---|---|---|
    | Half-spread under 2 bps | 845 | 0.47 | 0.05 | 0.49 | 0.87 |
    | 2 to 10 bps | 668 | 1.56 | 0.29 | 1.25 | 1.01 |
    | Both | 1,513 | 0.95 | 0.13 | 0.59 | 0.93 |

    Buys 0.97, sells 0.92. **About 1 basis point per trade**, against the 0.011 to 0.062
    research charges.
  - Limits of that number: paper fills (whether the broker's paper engine models impact
    was not checked; orders average $883 and $4,486); a median 99 seconds between
    submission and recorded fill, so each number includes price drift (which timestamp
    that is was not checked); four weeks, mostly the last two; picking tight-quote fills
    leans toward the liquid moments. Left out: `macd_crossover_v1` (29,717 replay rows
    from 2023 to 2024 with no fill, and 63 filled rows in October 2026 of untraced origin).
  - Side observation, traced 2026-11-15, not urgent: for those symbols the live quote
    midpoint on A is at times about 3% from the traded price. `get_latest_quotes` has one
    live caller, `ExecutionPolicyEngine._fetch_quote` (`execution_policy_engine.py:228`).
    The policy defaults to `passthrough` unless an intent carries its own
    (`run_order_submission_job.py:75-79`), and all 32,357 recorded rows are passthrough,
    where the midpoint only feeds recorded numbers (reference price, expected fill,
    adverse-fill flag: 37% of fills flagged). Order sizing reads last trade prices
    (`run_trading_evaluation_job.py:79-93`), a different source; our bars come from the
    same trades and matched the fills. So no order has been sized or priced from it.
    - [ ] Before any `LIMIT` or `TWAP` policy is switched on: the limit price is the
          midpoint ± an offset (`order_type_resolver.py:105-107`), so a midpoint 3% off
          would price the order 3% off. Guard it (for example fall back to the last
          trade when the quote is wider than a set number of basis points).
    - [ ] The fill-quality statistics for these symbols are not usable as recorded
          (adverse-fill rate, anything the simulation-vs-paper comparison or a future
          calibration reads). Fix the reference or filter on quote width where read.
- Break-even cost of the eight profitable strategies of the post-R10 run (return ÷
  dollars traded, trades held fixed; a re-simulation would differ):

  | Strategy | Sharpe | Return | Traded (× capital) | Break-even (bps per dollar traded) |
  |---|---|---|---|---|
  | c24 composite rule | 2.34 | 2.95% | 126.9 | 2.32 |
  | c06 mean reversion | 1.53 | 0.23% | 2.4 | 9.58 |
  | c25 composite rule | 0.99 | 1.02% | 50.8 | 2.01 |
  | c27 composite rule | 0.82 | 0.68% | 34.5 | 1.97 |
  | c26 composite rule | 0.71 | 1.15% | 820.2 | 0.14 |
  | c01 momentum | 0.29 | 0.30% | 133.6 | 0.22 |
  | c20 composite rule | 0.12 | 0.10% | 59.0 | 0.17 |
  | c22 composite rule | 0.10 | 0.10% | 684.3 | 0.01 |

  At 1 basis point four of the eight keep a profit. At 2, c24 keeps 0.4%, c25 is at
  zero, c27 is just under and c06 is untouched. At 3 only c06 is left. With a base of 1,
  the stress gate's 2× and 3× would separate these strategies, which they do not today.
- Reach: the same factory fills the platform backtest ("so both sides always fill
  identically"), so a change here goes through parity 5c and moves every score, the bench
  re-sims included. Real paper orders go to the broker. The on-deck shadow tier uses it
  too (traced, see "Reach" in the decision box below).
- [x] Read the calibration path and report what cost it would set from A's fills. Done
      2026-11-15, above.
- [x] Decide the cost assumption: a spread per trade (flat, or per symbol) on top of the
      volume term, and where the number comes from. **1 basis point per trade, flat,
      agreed 2026-11-15**, from the tight-quote fills above. Implemented (commit
      `bb2f09329`):
  - `VolumeShareSlippageConfig.half_spread_bps`, default 1
    (`research/simulation/models/volume_share_slippage_model.py`). A fill now costs
    1 basis point plus the volume term. The no-volume fallback (floor 5) is unchanged.
  - The number is the config's default, not an argument at the factory, so the three
    places that build the model (the factory, `cache_key_builder.py:38`, the calibration
    path) agree, and a new one cannot go back to free trading by omission.
  - It is in `config_summary()`, which the result cache hashes, so results simulated at
    the old cost are not reused.
  - The stress gate needs no change: its multipliers scale the whole distance from the
    bar close, so 2× and 3× now test 2 and 3 basis points plus the volume term (tested).
  - Reach, confirmed by reading: `build_platform_execution_service` is called by
    research (`build_simulation_context.py:124`), platform replay
    (`runtime_hooks.py:141`) and the on-deck shadow tier (`shadow_fill_service.py:73`,
    `:74`, `:94`). **On deploy, `macd_crossover_v1`'s shadow fills on A cost 1 basis
    point more per trade from that day**, so its on-deck record has a step there. The
    bench re-sims on B change with it. Real paper orders are untouched.
- [ ] Write the slippage actually charged into the fill rows. Not done with the 1 bp
      change. `Fill` has no slippage field; its `metadata` is copied onward at
      `broker_order_mapper.py:97` (`meta=fill.metadata`), so carrying it there may also
      change what A stores for fills; that path was not traced. Decide where it should
      live first.
- [x] Re-run the stress cost re-simulations on the 30-strategy run with the new costs and
      record how many survive 2× and 3×. Done 2026-11-15 (code as committed in `1ca8c33e1`: 1 basis
      point, regime report-only; same bars and harness as the R11 rerun; funnel 1,289 s,
      audit pass 752 s). Of all 30: 3 survive 2× (c24, c06, c27), 1 survives 3× (c06).
      The gate passes 3 of 30 (old cost: 7 of 30).
  - Funnel: 30 → 30 → 2 → 2 → 2 → 2 (old cost: 30 → 30 → 4 → 2 → 2 → 2). Same two final
    survivors. c25 and c27 no longer pass Monte Carlo (pass rates 0.61 → 0.51 and
    0.60 → 0.54 against 0.60).
  - The eight strategies that were profitable at the old cost; four still are:

    | Strategy | Sharpe, old → 1 bp | Return, old → 1 bp | Sharpe at 2× / 3× | Stress |
    |---|---|---|---|---|
    | c24 composite rule | 2.34 → 1.34 | 2.95% → 1.67% | 0.28 / -0.82 | pass (1 of 2) |
    | c06 mean reversion | 1.53 → 1.37 | 0.23% → 0.21% | 1.21 / 1.05 | pass (2 of 2) |
    | c25 composite rule | 0.99 → 0.45 | 1.02% → 0.45% | -0.06 / -0.53 | fail |
    | c27 composite rule | 0.82 → 0.40 | 0.68% → 0.32% | 0.01 / -0.39 | pass (1 of 2, by 0.01) |
    | c26 composite rule | 0.71 → -4.12 | 1.15% → -6.83% | -9.26 / -14.32 | fail |
    | c01 momentum | 0.29 → -0.85 | 0.30% → -0.98% | -2.10 / -3.31 | fail |
    | c20 composite rule | 0.12 → -0.50 | 0.10% → -0.53% | -1.10 / -1.69 | fail |
    | c22 composite rule | 0.10 → -3.98 | 0.10% → -6.58% | -8.24 / -12.56 | fail |

  - The break-even table above predicted this from the old run's trades: c24 at 2.32
    basis points (re-simulated: still positive at 2, negative at 3), c25 at 2.01 and c27
    at 1.97 (re-simulated at 2: -0.06 and 0.01), c26 / c01 / c20 / c22 under 0.25 (all
    negative at 1). c24's return fell by 1.28 points against 1.27 predicted (126.9 times
    capital at 1 basis point), which is the "Verify" line's second half.
  - c06 trades 203 times and loses 0.02 points of return; c24 trades 4,139 times and
    loses 1.28. At the old cost the stress gate could not tell these two apart.
  - Regime, report-only for the first time in a real run: nobody eliminated, findings
    recorded for 28 of 30 (none for c24 and c06). Overfitting: under R14.
- [x] Re-baseline: the 30-strategy run and the bench re-sim, before / after, in the
      results log. The 30-strategy half is the box above (section 5's table is laid out
      for the generator baseline, so the numbers stay here). Bench re-sim, done
      2026-11-16 03:40 UTC on B at `7cf1c0ba1`: `worker bench-resim --as-of 2026-11-14
      --run-id bench_resims_2026-11-14_r12 --no-pull --no-publish` (nothing posted to A;
      payload `artifacts/worker/bench_resims/bench_resims_2026-11-14_r12.json`, log
      `~/rehearsals/bench-resim-r12-2026-11-15.log`). Before is
      `bench_resims_2026-11-14_r10` at `b3b82ee67`. Same window (2026-08-17 to
      2026-11-14), same dataset (`raw_bars_20261114T041002Z_c2faf2e7`), 63 daily returns.

  | Strategy | Sharpe | Return | Max drawdown | Score | Trades | Traded per day (× capital) |
  |---|---|---|---|---|---|---|
  | `macd_crossover_v1` | 1.21 → 0.51 | 1.70% → 0.70% | -2.24% → -2.50% | 1.863 → 1.559 | 2,704 → 2,704 | 1.64 |
  | `mean_reversion_v1` | -0.19 → -0.66 | -0.32% → -1.01% | -2.71% → -2.87% | 1.303 → 1.096 | 2,646 → 2,648 | 1.14 |
  | `momentum_v1` | -1.23 → -5.54 | -1.82% → -7.79% | -3.55% → -7.82% | 0.943 → 0.750 | 20,097 → 20,015 | 9.73 |

  - The falls match the cost: traded per day × 63 sessions × 1 basis point is 1.03, 0.72
    and 6.13 points; returns fell 1.00, 0.70 and 5.97.
  - Order of the three is unchanged on Sharpe and on score. Daily returns before and
    after correlate at 0.9992 or more: the same trades, each a little worse.
  - `macd_crossover_v1`, on deck, is the only one still positive. `momentum_v1` and
    `mean_reversion_v1` are the two trading on A's paper account.
  - Not checked: how the re-sim's turnover for `momentum_v1` (9.7 times capital a day)
    compares with what it trades on A. A's fills for both live strategies together were
    $4.08M over four weeks.
  - A still holds the Saturday submission (pre-R10, old cost). The timed run on Sat
    2026-11-21 posts the first one at the new cost.

**Verify:** the fill rows' slippage sums to the difference between fill prices and bar
closes, and the cost of the best strategy in the table is within the range the chosen
assumption implies.

**R13 — No stage checks parameter stability (found 2026-11-15 under R4, not decided).**
The analyzer exists and is unreachable from the funnel (R4 note). A strategy tuned to
one exact setting, the commonest form of overfitting in a parameter sweep, is not
tested for it anywhere.
- [ ] Decide: a neighbour check in the funnel (re-simulate each survivor with each
      parameter moved one step down and one step up, and require the result to hold),
      or leave it out and say so in the funnel's description. Cost of the first: two
      simulations per parameter per survivor, on survivors only.
- [ ] If built: judge on the level of the neighbours' Sharpe against the strategy's own,
      not on the analyzer's current score, which ignores the level.

**R14 — Overfitting gate: read, never exercised (found 2026-11-15 under R4; measured,
then made report-only, merged PR #184, deployed 2026-11-16).** Confirmed by reading: two overlapping folds at most on today's window,
so both walk-forward indicators are statistics of two numbers; no term for the number of
strategies tried; the Monte Carlo evidence counted twice; a pass when evidence is thin;
regime buckets of any size counted. Not known: whether on real folds it separates
anything.
- [x] After R11, on the 30-strategy run: record each indicator and the score for every
      strategy that reaches the gate. First reading, 2026-11-15 (R11 rerun, two
      strategies reached it, both pass; a strategy fails above 0.6):

  | Indicator (weight) | c06 mean reversion | c24 composite rule |
  |---|---|---|
  | Score | 0.13 | 0.24 |
  | Drop from train to test Sharpe (0.30) | 0.00 | 0.23 |
  | Spread of test Sharpe across folds (0.20) | 0.09 | 0.39 |
  | Monte Carlo paths with Sharpe not above zero (0.20) | 0.22 | 0.15 |
  | Regime buckets without a positive Sharpe (0.15) | 0.27 | 0.20 |
  | Fewer than 10 trades (0.05) | no | no |
  | Return concentrated in the top 10% of bars (0.05) | 0.50 | 0.46 |
  | Parameter fragility (0.05) | missing | missing |

  - The scores reproduce by hand from the weights (the stored `regime_concentration`
    is the share of buckets *with* a positive Sharpe, 0.73 and 0.80; the score uses one
    minus it, `overfitting_analysis.py:71-74`).
  - What those two rows do not show: whether the gate can fail a bad strategy. Both had
    already passed five gates.
  - Audit pass, all 30 strategies (same run; walk-forward thresholds opened, so every
    strategy has fold evidence): 26 pass, 4 fail.

    | Group | Strategies | Pass | Scores |
    |---|---|---|---|
    | Sharpe above zero | 8 | 8 | 0.13 to 0.57 |
    | Never traded | 2 | 2 | 0.42 |
    | Sharpe below zero | 20 | 16 | 0.29 to 0.59 passing, 0.61 to 0.79 failing |

    - It passes 16 of 20 losing strategies, the worst of the run included (Sharpe -1.79,
      score 0.54), and both that never traded.
    - It fails no profitable strategy. c27 comes closest at 0.57: train Sharpe 2.69, test
      -0.22, the one case that looks like what the gate is for.
    - The four it fails are all losing strategies with the train-to-test drop at its
      maximum, and the drop is large because the train Sharpe in the denominator is near
      zero or negative: train 0.10 and test -0.98 reads as a drop of 11.18, train -0.24
      and test -1.78 as 6.36.
    - The drop is 0.00 for 22 of 30 and the fold spread is at its cap of 1.0 for 13 of
      30. Both are ratios over a mean of two fold Sharpes.
    - Reading: on this run the gate changes no outcome. Everything it fails had already
      failed earlier stages, and everything profitable passes. The score measures whether
      train and test agree, not whether the strategy is good, so one that loses steadily
      in both scores as not overfit. The two walk-forward indicators carry half the
      weight and are unstable near zero.
  - Same audit at 1 basis point (R12 run, 2026-11-15): 27 pass, 3 fail. It now fails
    one profitable strategy, c27 (Sharpe 0.40, score 0.57 → 0.61), the case named above
    as what the gate is for. It still passes 22 of the 24 losing strategies and both
    that never traded. c24's score rose from 0.24 to 0.43. In the funnel both survivors
    pass (0.15 and 0.43).
- [x] From that, decide what the gate should be. **Report only until longer history,
      agreed 2026-11-15** (user: "lets do your rec"). Implemented the same day on
      `research/r6-r2-validators`, not yet committed:
  - `OverfittingGateConfig.enforce`, default `False` (`overfitting_gate.py`). The score
    is computed as before; what the rule found is on the verdict as `findings`. With
    `enforce` off the strategy passes and `failure_reasons` is empty; with it on,
    behaviour is as before. Same shape as the regime gate (R15).
  - The verdict is now stored per strategy (`StageDiagnostics.overfitting_verdict`);
    before, only the score was kept, so a "would have failed" reason had nowhere to go.
  - The hook sets `enforce=False` explicitly; the YAML key is `enforce`. The stage's
    summary log line counts strategies with findings and says which mode.
  - The funnel that eliminates is now: quality filter, walk-forward, Monte Carlo, stress
    costs. Regime and overfitting report.
  - Checked: `tests/research/pipeline`, `config`, `validation` and the smoke test pass,
    ruff clean. Full suite not yet run with it.
- [x] R7: a test with two folds and Sharpes between -2 and 3. Done with the change:
      `tests/research/pipeline/gates/test_overfitting_gate.py`,
      `TestTwoFoldsRealisticSharpes`. A steady strategy is not flagged (score 0.14); a
      train-to-test collapse (2.9 → 0.1, 2.5 → -0.5) is (0.60); test better than train
      gives a drop of zero. Two tests pin the known weaknesses: a strategy that loses
      steadily in train and test is not flagged (0.25), and a train Sharpe near zero
      sends the drop to its maximum.
- [ ] Before `enforce` is switched on (package B, waits on longer history): a minimum
      number of folds before the two walk-forward indicators count; the train-to-test
      drop made safe for a train Sharpe near zero; a bar that rises with the number of
      candidates tried; the thin-evidence case failing; then re-measure on all strategies
      as on 2026-11-15.

**R15 — On a 60-session window the regime gate's verdict on a profitable strategy is
close to chance (found 2026-11-15 under R4, measured; option (a) agreed and implemented
2026-11-15, commit `1ca8c33e1`; merged as PR #183, deployed to A and B 2026-11-16 03:38 UTC (`7cf1c0ba1`)).** Evidence in the
R4 regime note: with labels dealt to random days the gate fails a Sharpe-2.34 strategy
48 times in 100. The cause is the window, not the rule's logic: a bucket of 18 or 19
sessions cannot support a Sharpe floor of -1.0. In the open-upstream runs so far regime
removed 2 of the 4 Monte Carlo survivors (post-R10 run) and 1 of 3 and 2 of 5 before
that.
- Fix options ((a) chosen 2026-11-15, see the first box below):
  - (a) Report only until the history is long enough: keep the per-label numbers on the
    verdict, take them out of the pass rule, as done for the stress shocks. Smallest
    change. The funnel is then quality, walk-forward, Monte Carlo, stress costs,
    overfitting.
  - (b) Keep it as a gate but judge against noise: fail only when the worst label is
    worse than the random-label control would give (for example worse than 95 of 100
    shuffles), or when the Sharpe is under the floor by more than its standard error.
    Answers the question the gate is for. Needs a fast version of the per-label
    analysis; the current service took hours for 2,800 evaluations.
  - (c) Longer history (item 5 of the order). With two years a label holds 100 or more
    sessions and the standard error falls to about 1.5. This is the root cause; (a) or
    (b) is what to do until then.
  - Recommended, and agreed 2026-11-15: (a) now, (b) when the history is there.
- [x] Decide (a) / (b). **(a), agreed 2026-11-15.** What changed:
  - `RegimeGateConfig.enforce`, default `False` (`regime_gate.py`). The rules run as
    before; what they found is on the verdict as `findings`. With `enforce` off the
    strategy passes and `failure_reasons` is empty; with it on, behaviour is as before.
  - A switch here, where R11 removed its setting: the rule is sound and lacks data, so
    turning it on again is the planned path. The default is off so a stage built without
    the flag does not gate on a short window by omission. The hook sets it off
    explicitly; the YAML key is `enforce`.
  - The stage's summary log line counts strategies with findings and says which mode.
  - Checked: `tests/research/pipeline`, `config` and the cost-model tests pass (323),
    ruff clean. The full suite passed on R11 + R12 before this change (5,396 passed) and
    has not been re-run with it.
- Before `enforce` is switched on (all open):
- [ ] A label needs a minimum number of sessions stated in sessions (about 100), and the
      verdict says how many sessions each judged label had.
- [ ] The -20% drawdown rule: drop it or restate it against the window's size.
- [ ] R7: tests on five-minute curves over 60 sessions with Sharpe between -2 and 3,
      including one where random labels are the input.
- [ ] Option (b), judging against shuffled labels, once the per-label analysis is fast
      enough.

**Verify:** on the 30-strategy run, a strategy's verdict under the real labels differs
from its verdict under shuffled labels more often than chance, or the gate does not
decide.

**Definition of done for any change under `research/simulation/`:** the research ↔
trading-cycle parity check from `portfolio-rotation-plan.md` 5c (re-sim vs platform
correlation ≥ 0.99 on the shared fixture) stays green. R1's bootstrap does not touch the
simulator; stochastic fills or any execution-model change would, and must re-run it.

**R16 — The "deployable" decision still rests on the measures the funnel now only reports
(found 2026-11-15 while investigating R5; measured, decided and implemented the same day,
merged PR #184).** After the
pipeline, the replay hook scores each survivor and seeds governance records for the ones
marked deployable (`research_hooks.py:328-335`). Deployable means the plain average of
four numbers is at least 0.55 (`candidate_ranking_service.py`, `_score_vector`):
walk-forward fold consistency, overfitting resistance, "regime robust" (1 or 0) and the
shock survival rate. The last three are the measures R14, R15 and R11 took out of the
funnel's pass rules: the shock rate was the same 2 of 7 for every profitable strategy,
the regime verdict on about 19 sessions per regime is close to chance, and the
overfitting score does not track quality. A missing number counts as 0.5
(`research_feature_vector_builder.py:29`), which is higher than the 0.29 a profitable
strategy gets for shocks and the 0 it gets when "regime robust" is false. By the formula,
with shocks at 0.29 and regime at 0 the other two would have to average 0.96, so the
decision probably turns on the regime flag. That last step is an inference.
**The inference was wrong (measured below): the regime input is never 0 or 1, it is
always 0.5.**
- [x] Measure: record the four inputs, the average and the deployable flag for every
      survivor of the real-bars run (the harness does not record them today).
      Measured 2026-11-15 on real bars (window 2026-08-15 to 2026-11-13, 30
      strategies, 1 bp costs, tree before the train-failed-fold change). The harness
      repeats the hook's own scoring calls. Two runs: the pipeline as it is, and
      "open upstream" (stage 1 and walk-forward made permissive so all 30 carry full
      evidence; in that run every fold counts as passed, so its walk-forward input is
      inflated and its flags are not what production would give).

      | Run | Survivor | Walk-forward | Overfitting | Regime | Shocks | Average | Deployable | Robustness score |
      |---|---|---|---|---|---|---|---|---|
      | As it is (30 → 2 → 1 → 1 → 1 → 1) | composite rule `5a748d50` | 0.17 | 0.57 | 0.50 | 0.29 | 0.38 | no | 0.34 |
      | Open upstream (30 → 30 → 2 → 2 → 2 → 2) | mean reversion `55442143` | 1.00 | 0.85 | 0.50 | 0.29 | 0.66 | yes | 0.65 |
      | Open upstream | composite rule `5a748d50` | 0.67 | 0.57 | 0.50 | 0.29 | 0.51 | no | 0.51 |

      What the numbers show:
      - **As the pipeline stands, research flags nothing deployable**, so it seeds no
        governance record on this window. The one survivor scores 0.38 against 0.55.
      - **The regime input is 0.50 in all 60 rows.** The hook gives the regime profile
        to the validation step but not to the scoring request
        (`research_hooks.py:269`), so the scorer reads it as missing. It tells
        strategies apart by nothing and pulls every average toward 0.5.
      - **The shock input takes three values**: 0.14 or 0.29 for every strategy that
        trades, 0.57 for the two that never trade. It lowers every trading strategy by
        about the same amount and is highest for doing nothing.
      - So the flag is decided by the other two. With regime at 0.50 and shocks at
        0.14 to 0.29, walk-forward consistency plus overfitting resistance must reach
        1.41 to 1.56 out of 2.
      - **The formula never looks at whether the strategy makes money.** In the open
        run 15 of 30 are flagged, including strategies with a full-window Sharpe near
        -4 and the two that never traded (0.69, the highest averages in the table).
        Production does not reach those rows, because only funnel survivors are
        scored; the flag relies on the funnel for that entirely.
      - **Less evidence scores higher (R5, now measured).** In the as-it-is run the 28
        strategies that never reached walk-forward would score 0.46 to 0.52, above
        the real survivor's 0.38, and the two that never traded get the highest
        robustness score (0.55 against the survivor's 0.34).
      - Robustness score: with five components supplied the weights are walk-forward
        0.35, Monte Carlo 0.24, regime 0.24, shocks 0.12, overfitting 0.06. The three
        report-only ones carry 0.41 and, for the survivors, sit at 0.13 to 0.17
        (regime) and 0.29 (shocks). On walk-forward and Monte Carlo alone (0.6 / 0.4)
        the three rows above read 0.39, 0.90 and 0.69 in place of 0.34, 0.65 and 0.51.
      - The train-failed-fold change does not move these numbers: no strategy that
        reached walk-forward failed a train filter in either run (4 folds as it is,
        60 with permissive filters, which cannot fail). How often it happens on real
        bars with real filters is still a sample of 4.
      - Mean reversion `55442143` has a full-window Sharpe of 1.37 and never reaches
        walk-forward in the real pipeline: its Sharpe on stage 1's 45-day selection
        window is 0.04. Noted, not acted on; it is one strategy on one window.
      Outputs: session scratchpad `out_r16/r16_before_d2_real_{hook_as_is,open_upstream}.json`.
- [x] Decide with R5 what "deployable" should rest on while those three measures are
      report-only.
      Decided by the user 2026-11-15: **trusted evidence only**. Implemented the same
      day (merged PR #184) through the same `EvidencePolicy` as R5:
      - Deployability = plain average of walk-forward consistency and Monte Carlo
        stability (was: walk-forward, overfitting, regime, shocks). Threshold still
        0.55.
      - Robustness score = 0.6 x walk-forward + 0.4 x Monte Carlo (their old weights,
        0.30 and 0.20, rescaled). Ranking composite = 0.625 x robustness score + 0.375
        x walk-forward.
      - Regime, shocks, overfitting and parameter stability are still computed, stored
        and listed, with weight 0. Tests check that moving them from worst to best
        moves no deciding score.
      - To give a measure weight again, add it to `DEFAULT_EVIDENCE_POLICY.deciding`
        in the change that sets its gate's `enforce=True`. A smoke test
        (`test_report_only_gates_do_not_feed_the_deciding_scores`) fails if the hook's
        gates and the policy disagree.
      - On the measured window the three table rows above become: survivor as it is
        0.45 (was 0.38), not deployable; open-upstream mean reversion 0.88 (was 0.66),
        deployable; open-upstream composite rule 0.70 (was 0.51), deployable. The last
        one changes flag, in a run whose walk-forward input is inflated by permissive
        filters. In the real pipeline no outcome changes.
      - **Confirmed by a second real-bars run 2026-11-15** (same window and 30
        strategies, source frozen after the change; full suite passing). As it is:
        funnel unchanged (30 → 2 → 1 → 1 → 1 → 1); the survivor reads deployability
        0.45 and robustness 0.39, not deployable; the other 29 are not scored (before,
        28 of them would have read 0.46 to 0.52, above the survivor). Open upstream:
        funnel unchanged (30 → 30 → 2 → 2 → 2 → 2); the two survivors read 0.88 and
        0.70 (robustness 0.90 and 0.69), both deployable.
      - In the open run 17 of 30 rows are flagged (was 15), and it is a different set.
        No longer flagged: the two that never traded (0.50, were the highest at 0.69)
        and every row with a full-window Sharpe below -2. Flagged now: every row with
        Sharpe above -1.8 except three. It still includes losing strategies, because
        permissive filters put walk-forward at 1.00 for nearly every row, and with
        walk-forward at 1.00 a Monte Carlo share of 0.10 is enough to reach 0.55.
        Production does not reach those rows (only funnel survivors are scored, and
        the Monte Carlo gate has its own bar), but it is why the threshold box below
        matters. Outputs: session scratchpad
        `out_r16/r16_after_policy_real_{hook_as_is,open_upstream}.json`.
- [ ] Calibrate the 0.55 threshold. It was picked for the old four-input average and
      has never been checked against outcomes; with two inputs it means "walk-forward
      and Monte Carlo average at least 0.55" (for example 1 of 2 folds with no drop
      from train to test, which scores 0.50, plus a 0.60 Monte Carlo share lands
      exactly on it). Needs more survivors than one window gives: do it after
      package B.
- [ ] The hook does not give the regime profile to the scoring request
      (`research_hooks.py`, the `ResearchIntelligenceRequest` call), so the reported
      regime fields in the ranking are always the neutral 0.5. No effect on any
      deciding score while regime is report-only; fix before the regime gate is
      switched on, or sooner so the reports are true.

Consequence to know (follows from the rule, not a new decision): with under 75 days of
data only stage 1 runs, so no survivor has walk-forward or Monte Carlo evidence and
research seeds no governance record at all in that case. Before, such survivors were
scored on defaults. `scripts/analyze_validation.py`, which works from an equity curve
alone, likewise reports "not scored" and exits 1.

**R17 — Short warmups get no bars when a window starts on a Monday (found 2026-11-16
while testing the bar cache; not fixed, no decision yet).**

`SimulationWindowLoader` reaches back for warmup bars by calendar days: trading days
needed × 7/5, rounded up. The comment calls that conservative; it is not across a
weekend. A warmup of up to one session (78 bars) reaches back two calendar days, which
from a Monday is Saturday. Checked on generated sessions with the real loader:

| Window starts | Warmup 20 | 61 | 100 | 201 |
|---|---|---|---|---|
| Monday | 0 | 0 | 78 | 201 |
| Tuesday | 20 | 61 | 78 | 201 |
| Wednesday | 20 | 61 | 100 | 201 |

Effect: such a run starts with indicators not warmed up, so its first signals differ
from what the trading cycle would produce on the same bars (the runner's comment asks
for "same bars in, same signals out"). Walk-forward folds start every 15 days, so the
weekday varies from fold to fold. A day after a market holiday behaves the same way.
Size not measured on real runs. A fix changes results, so it gets a before / after on
identical bars like R10.

- [ ] Measure: how many of the hook's runs start short, and how far their results move
      when the warmup is complete.
- [ ] Fix: reach back by sessions found, not by a calendar multiplier (read further
      until enough earlier bars exist or the dataset starts).

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
  Step 5's timer (the runbook already warns research jobs are memory-hungry). The cap
  was built 2026-11-16 (committed, PR pending; see "Package B investigation"); the measured run
  on B is still open.

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
- The probe below has not been run (user, 2026-11-15), and SIP work waits until the
  other updates are done: the switch is mostly on the data provider's side, not in our
  code. Longer history for research (package B) is loaded on IEX and does not wait on it.
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

### Step 1b — Generation system review (not started; added 2026-11-15)

User, 2026-11-15: "the strategy generation and structure of the generation system needs
overhauling/update"; not urgent, later in this sprint. What the plan had before this
section: a description of the generator (section 1), R9 (the evolutionary generator has no
selection) and Step 2, which changes one thing (a real search for composites) with a
design written on 2026-11-06 as "minimal, additive", before anything was audited. Nothing
reviewed the generation system's structure the way Step 0 reviewed the pipeline. This
section is that review. It comes before Step 2, because its findings may change Step 2's
design.

What is already measured (R4 / R12 runs, 2026-11-15, 30 candidates from the replay hook on
real bars): 2 of 30 never traded; 20 of 30 lost money at the old cost and 24 of 30 at
1 basis point; 2 survive the funnel. So most of the simulation budget goes on candidates
that were never viable.

Read-only audit first, then findings become boxes here, as in Step 0:

- [ ] Map the system as built: `research/strategy_generation/` (engine, the three
      generators, `composite_generation.py`, the cache), who calls it (replay hook,
      `run-experiment`, the worker), and how many candidates each caller asks for and
      from which families.
- [ ] How the replay hook picks its candidates (30 on the full profile, 10 on smoke): which
      families, which parameters, whether the choice is fixed or varies run to run, and
      why two never trade.
- [ ] Parameter ranges per family against the bar size and window actually used
      (5-minute bars, about 60 sessions): which settings cannot produce a trade or can
      only produce very high turnover.
- [ ] What is known before simulating: can a cheap check (no trades possible, turnover
      above the research cap, duplicate of another candidate) reject a candidate without
      a simulation.
- [ ] Feedback: what a run leaves behind that the next run could use (scores, clusters,
      the bench's current members) and what uses it today (nothing, per section 1).
- [ ] Structure: where a new family, a new operator or a new search method would plug in,
      and what would have to change in three places to do it.
- [ ] Options for the user from the above, in plain terms, before any code. Then revise
      Step 2's design or confirm it.

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
  - **Package B investigation (2026-11-16, read-only; measured where it says so).**
    - *How far back the data goes.* The provider's documentation gives historical stock
      bars "since 2016" on the free plan, 200 requests a minute, and restricts only the
      latest 15 minutes. Documented, not yet probed with a request for our symbols.
      - Probed 2026-11-16 (read-only request from A's app, the laptop's key is
        rejected): one-minute IEX bars for one mid-November session in each of six
        years, seven names. Each cell is minutes present of 390 / five-minute buckets
        with all five minutes, of 78 (the rule ingestion uses to keep a bar):

        | Session | AAPL | NVDA | SPY | XOM | COST | CRM | DIA |
        |---|---|---|---|---|---|---|---|
        | 2026-11-12 | 390 / 78 | 390 / 78 | 390 / 78 | 351 / 46 | 283 / 23 | 236 / 11 | 181 / 4 |
        | 2025-11-12 | 390 / 78 | 390 / 78 | 389 / 77 | 380 / 70 | 188 / 6 | 296 / 24 | 333 / 39 |
        | 2024-11-18 | 386 / 74 | 390 / 78 | 380 / 69 | 372 / 64 | 107 / 1 | 340 / 43 | 80 / 2 |
        | 2023-11-15 | 387 / 75 | 353 / 54 | 390 / 78 | 359 / 52 | 160 / 6 | 278 / 22 | 115 / 1 |
        | 2020-11-16 | 390 / 78 | 374 / 66 | 385 / 73 | 380 / 68 | 208 / 13 | 293 / 24 | 74 / 1 |
        | 2017-11-15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

      - A two-year window is served. "Since 2016" does not hold for this feed's
        minute bars: nothing came back for 2017 (where between 2017 and 2020 it starts
        is not checked). Daily IEX bars for the 20 sessions before 2024-11-18 are
        complete for all seven, so the universe generator's ranking has its input.
      - Thinly traded names keep few five-minute bars, in every year, not only old
        ones. The bars stored on B agree with the probe to within one bar: on
        2026-11-12 COST has 22 of 78, CRM 10, DIA 3; over the last six stored sessions
        COST 5 to 52, CRM 10 to 57, DIA 3 to 25. All three are in today's universe.
        One session per year is a small sample.
    - [ ] Decide what the long window does about names with few five-minute bars: the
          generator ranks on daily dollar volume and cannot see minute coverage. Open
          choices: a minimum-coverage rule when picking, leave as is and report
          coverage per name, or the fuller feed (D1, deferred by the user).
    - *Size on disk is not a constraint.* The newest rolling version holds 22 symbols,
      mid-August to mid-November, in 13 MB (99,118 five-minute bars). Two years is on
      the order of 100 MB; B has 25 GB free, A 18 GB.
    - *Memory is the constraint (measured).* One simulation over the last 30 / 60 / 90
      days of the real bars, 22 symbols, each in its own process, on the laptop: peak
      above the pre-run level 175 / 288 / 409 MB for 1,688 / 3,034 / 4,438 bar
      timestamps. That is a straight line, 0.085 MB per bar timestamp (about 4 KB per
      symbol-bar), on a 222 MB baseline. Extrapolated peak for one run: 6 months about
      1.0 GB, 12 months about 1.7 GB, 24 months about 3.2 GB, 36 months about 4.7 GB.
      B has 3.8 GB, about 3.0 GB usable, no swap; A has about 1.9 GB usable.
      - Cause, read from the code: every run loads the whole window for every symbol
        as one Python object per bar with `Decimal` prices, builds one row dictionary
        per symbol per bar for the metrics, and reloads for the next run (no shared
        cache). As plain numeric arrays the same bars would be around 0.1 KB each.
      - Where it goes (profiled 2026-11-16, one 60-day run, 53,244 bars): at the
        point where the bars and the result tables are both alive, 128 of 135 MB
        traced is the bar objects (about 2.4 KB per bar: the pydantic model, its
        `Decimal` fields and timestamps); the result tables are small. The traced
        peak is 210 MB, so about 75 MB more is short-lived bookkeeping inside the run
        (row dictionaries, table copies). Roughly two thirds bars, one third
        bookkeeping. The same bars as numeric columns would be about 4 MB.
      - What a cache would and would not do: the bars are re-read and rebuilt for
        every run, so reusing one loaded window across runs on the same dates saves
        time and costs no extra memory if it holds one window at a time. It does not
        lower the peak of a run, and a cache holding every window at once would
        raise it (24 months of bar objects for 22 symbols is about 1.8 GB by itself).
        Lowering the peak needs a smaller way of holding bars.
      - Kept results add to it: three runs in one process at 90 days peaked 89 MB
        higher than one (about 45 MB per further run), and memory is not handed back to
        the system after results are released. The 30-strategy pipeline run of
        2026-11-15 was seen at about 1.07 GB, which puts the long-run figure nearer 15
        MB per kept 90-day run. Not measured at longer windows.
      - Consequence: no single run can span two years on B, and the proposed layout
        (12 months selection, 6 evaluation, 6 holdout) never needs one. Its longest
        run is 12 months, about 1.7 GB, which fits with a thin margin. Stage 1 with 30
        strategies each kept for 12 months is the part most likely to run out.
      - Nothing runs in parallel (every stage is serial, one worker). The hook
        computes `_sim_workers` and never uses it; wiring it in would double the peak.
      - Time: 13.5 s for one 90-day run on the laptop, so about a minute per 12-month
        run. Monte Carlo's resampling time grows with the square of the window; its
        memory is capped at about 64 MB by chunking.
    - *Universe for old dates.* The hook takes the universe version in effect on the
      window's first day and fails the tick when there is none (no fallback to today's
      list). The point-in-time S&P 500 file in the repo covers 1996-01 to 2026-08, so
      membership on any date in 2024 or 2025 is answerable (503 names on each date
      checked). Missing: only a platform replay that itself starts on that date writes
      universe versions; a standalone research run has none. The universe is frozen at
      the window's start for the whole window.
      - User's question, 2026-11-16: "cant we technically run the universe generator on
        the backfill BEFORE starting a sim?" Yes, read from the code the same day. The
        generator a replay runs (`run_universe_at_timestamp`) takes any date: with
        `screener_source="sp500_point_in_time"` its pool is the index members on that
        date ranked on the 20 days before it, it scores the pool, picks 20 and writes a
        universe version effective on that date. Scoring reads either a stored dataset
        (bars for the whole pool, up to 500 names, must already be backfilled) or daily
        bars fetched for the pool (`alpaca_daily`, what the live monthly rotation
        uses). With the second there is no chicken-and-egg: generate first, then
        backfill five-minute bars only for the names picked.
      - Two gaps for doing that outside a replay: the `atp universe monthly-rotation`
        command has no option for the point-in-time pool (it ranks today's active
        names), and B has no market-data key, so the generator runs where a key is and
        the version moves to B with `atp universe export-version` / `import-version`.
    - *Getting a long window to the hook.* A read uses exactly one dataset version.
      Each backfill run writes its own version, fetches its whole window into memory
      in one call, and cannot resume (a failure discards the run and leaves a failed
      directory that the publish step would still upload). The only merge of several
      versions is the 63-session rolling one. So a long history is monthly or
      quarterly chunks plus a merge step that does not exist yet.
    - *Traps found.* (1) Without an explicit version id the hook takes the newest
      validated `raw_bars` version of any kind, which would be a one-day live version,
      not the long one. (2) Inside a replay a longer `lookback_days` is silently cut
      back to the replay's start; a window under 15 days is reported as
      `no_validated_dataset`. (3) The backfill's holiday list covers 2022 to 2025 only,
      so holidays in 2026 show as missing days in the coverage table (noise, not a
      failure). (4) A five-minute bar needs all five one-minute prints, so thinly
      traded names lose more bars than their coverage percentage suggests; validation
      never fails on thin coverage.
    - *Splits.* Bars are stored unadjusted (no adjustment is requested from the
      provider) and adjusted when read, from the corporate-actions table, which a
      backfill does not fill. Not verified: that every research read path applies the
      adjustment. A two-year window across 22 or more names will cross splits.
    - [x] Decide the universe for the long window. User, 2026-11-16 ("okay that makes
          sense"): the existing generator, run as of the window's first day, before
          any simulation. Candidates are the index members on that date; it scores
          them on daily bars and picks 20; five-minute bars are then backfilled for
          those 20 only. Not today's 22 symbols.
    - [x] `atp universe monthly-rotation --screener-source sp500_point_in_time`
          (2026-11-16, committed, PR pending): the generator can run for an old `--timestamp`
          outside a replay. The default stays `alpaca_active`, so the scheduler's
          month-end step is unchanged. Tested at the command level; not yet run
          against the provider for an old date.
    - B's database holds no universe versions today (checked 2026-11-16), so a
      back-dated run there writes the first record. A rotation retires the version
      active on its date and activates the new one, so if several dates are ever
      generated they must be run oldest first.
    - [ ] Run it on B with `--dry-run` first, then `--enforce`, once B has a key.
    - [x] Decide where the long dataset is built. User, 2026-11-16: on Box B, with a
          market-data key added to `.env.worker` (read-only use; B never submits
          orders). The generator, the fetch and the research then all run on B: no
          copy between machines and no load on the trading box. Not A (least free
          memory, and it trades), not the laptop (no storage credentials or database
          set up). This ends the rule that B never calls the data provider.
    - [x] Market-data key on B (2026-11-16, on the user's word: "sure thats fine you
          go"). The same paper key pair as Box A, not a separate account: the user's
          point, a paid data plan belongs to the account, so every box using that
          account's key gets it and research reads the feed trading reads. Copied box
          to box through a pipe, never displayed; verified by fingerprint and by one
          price request from a throwaway container on B. `.env.worker` is now
          owner-only (was world-readable); backup `.env.worker.bak-20261116` beside it.
          The laptop's `.env` had two wrong characters in the key ID (the cause of its
          401); corrected the same way. A's `.env` has Windows line endings, which
          Docker tolerates; left as is. If A moves to a live key, B keeps the paper
          key. Unconfirmed: whether a paper key receives a paid feed.
    - [ ] Build the long-history dataset on B: chunked backfill, corporate actions for
          the same symbols and span, a merge into one validated version, the checks in
          the box above.
    - [ ] Seed a universe version for the window's start outside a replay.
    - [ ] Prove split adjustment on the research read path with a test that crosses a
          known split, before any multi-year score is read.
    - [ ] Hook: take the dataset version explicitly; fail loudly when the window is
          shorter than asked; delete the unused `_sim_workers`.
    - *Decision, 2026-11-16 (user: "yes lets do it your way", after asking whether to
      cache bar reads or enlarge B).* Rehearse on the current box first. Before it:
      a memory ceiling per job and swap on B as safety nets, and a bar cache for
      speed. Choose between a larger instance and a smaller way of holding bars on
      the rehearsal's numbers; if it is tight, resize (t4g.medium to t4g.large,
      roughly twice the instance cost) before re-engineering bars mid-sprint. The
      smaller bar format stays a separate, later item: strategies and the trading
      cycle consume the same bar objects, so it touches research-to-live parity.
    - [x] Memory ceiling per job (2026-11-16, committed, PR pending): `worker_job.sh` sets
          `RATP_JOB_MEMORY` to the box's memory minus a 768 MB reserve (about 3055 MB
          on B today; follows a resize) and `docker-compose.worker.yml` applies it to
          the job container. Exit 137 is reported as a memory kill. Checked: the
          script's branches with stand-in commands on the laptop; B's Docker parses
          the file and resolves the limit. Not yet exercised by a real job on B.
    - [x] Swap installer `infra/ops/install_worker_swap.sh` (2026-11-16,
          committed, PR pending; 4 GB, swappiness 10, safe to re-run). Written and
          syntax-checked only.
    - [ ] Run the swap installer on B after the deploy (one sudo command; B has no
          swap today).
    - [x] Bars parsed once per pipeline run (2026-11-16, committed, PR pending):
          `SimulationWindowLoader.reuse_bars`, entered by `run_staged_experiment`.
          Keeps one (dataset, version, end date) at a time and drops it before
          reading the next. Keyed below the warmup, because warmups differ per
          strategy (20, 61 and 201 bars in the measured runs) and a whole-window key
          would mostly miss. On the real bars, five 90-day runs, off against on:
          equity curves and trade logs identical; 65.2 s to 52.3 s; bar loading
          19.1 s to 7.7 s (two reads where there were five); peak 720 MB to 698 MB.
          Loading was about 30% of a run. It does not lower a run's peak.
    - [ ] Rehearse on B with memory recorded before trusting a run (needs the
          long dataset; sample the job container's memory during the run).
    - [ ] If 12-month runs do not fit: stop keeping trade logs for eliminated
          strategies, or hold bars as numeric arrays, or a larger instance. Decide on
          the rehearsal's numbers, not before.
    - [ ] The weekly bench re-sims also repeat one window per strategy and do not
          use the bar cache yet (they call the runner directly). Small; any time.
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
- Holdout as a gate (to reassess; user, 2026-11-15: report only for now). The holdout
  score is stored with each candidate and eliminates nobody (R2, "Tasks for the six-stage
  hook"). Reasons it is not a gate today: on about 15 sessions a Sharpe has a standard
  error near 4, so a pass/fail would mostly be chance; and research reruns monthly on
  overlapping days, so a holdout that eliminates is slowly selected on. Look again once
  longer history is loaded and the holdout is its own period: how long it is, how many
  candidates have been scored on it, and whether the reported holdout scores separated
  the strategies that later held up on the bench from the ones that did not.

## 5. Results log

| Date | Dataset version | Method | Generated / accepted | Survivors (gates) | Clusters | WF best / median | Holdout top-10 Sharpe median | Holdout drawdown median | Off-pool share | Wall-clock |
|---|---|---|---|---|---|---|---|---|---|---|
| | | grid | | | | | | | 0% | |
| | | random n=50 | | | | | | | 0% | |
| | | evolutionary | | | | | | | | |
