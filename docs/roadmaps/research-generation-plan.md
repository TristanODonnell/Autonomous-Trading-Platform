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

Last updated: 2026-11-07 (Step 0 audit done; nothing built yet).

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
- [ ] Resample something real: block bootstrap of per-bar (or per-trade) returns from the
      single full-window simulation (`n_runs` resamples, block length ≈ one session), gate
      on tail statistics (P5 Sharpe, P95 max drawdown, probability of breaching the
      drawdown limit) instead of a per-run pass rate. Keep `base_seed + i` for
      reproducibility. Stochastic fills (`SimulatedFillModelConfig` probabilities) stay a
      separate, opt-in knob: they measure execution noise, not strategy fragility.
- [ ] Fail closed: `None` trials and missing metrics count as failures; warn when
      `n_runs_completed < n_runs`.
- [ ] Tests with a real runner on synthetic bars and realistic thresholds, asserting
      `sharpe_dist.std > 0` and that the verdict can differ from the simulation stage's.
      Fix the misleading comment in `experiments/staged/ma_sweep_staged.yaml` (~line 134).

**Verify:** on the 63-session window, rerun `ma_sweep_staged.yaml`'s Monte Carlo stage
before and after; before, every strategy's `sharpe_dist.std` is 0.0; after, it is not, and
at least one strategy that passed the simulation stage fails the tail gate.

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
      `SimulationRunResult` from the SoR) so a resumed stage keeps its survivors.
- [ ] Identity: thread the real `universe_version`, `random_seed` and `pipeline_id`.
- [ ] Instantiate in `experiment_orchestration_service.py` (where
      `plan.staged_pipeline_config.stages` is built) with
      `persist_path=<artifacts>/research/<experiment_id>.checkpoints.json`, pass
      `checkpoint_service`, `resume_mode`, `force_rerun`, `dry_run` to the three stages;
      same in `research_hooks.py` if replay research should resume.
- [ ] CLI: `--resume`, `--checkpoint-store`, `--force-rerun` on the full-pipeline command;
      retire the deprecated `resume-experiment` alias.
- [ ] End-to-end test: run, kill after the first stage, rerun with `--resume`, assert only
      the missing units execute and the final survivors match an uninterrupted run.

**Verify:** the e2e test above; `research inspect-checkpoints` lists a real run's store.

### Step 1 — Baseline measurement (not started)

Run the existing pipeline on the window B now holds and record the numbers, so the
generator change has a "before". Run it **after R1 and R2 land**, or the baseline is
scored by a vacuous Monte Carlo and leaky folds and has to be redone.

- [ ] Experiment YAML `experiments/research/baseline_composite_grid.yaml`: `composite_rule`
      via `grid` (all 652 skeletons), the 22-symbol A universe, dataset pinned to the rolling
      version B pulled (`raw_bars_20261107T005243Z_8097b17d` or newer), **search window**
      2026-08-03 → 2026-10-17 (~54 sessions), **holdout** 2026-10-20 → 2026-11-06
      (~14 sessions), `random_seed: 42`.
- [ ] Run on B with `infra/ops/worker_job.sh research run-pipeline --config …` (one job at a
      time, lock respected) or on a laptop after `atp datasets pull --version <id>`.
- [ ] Record in section 5: candidates generated / accepted / rejected / duplicates
      (`GenerationSummary`), survivors per gate, cluster count after dedup, best and
      median walk-forward score, best and median **holdout** Sharpe / return / drawdown of
      the top 10 by search score, wall-clock and peak memory.
- [ ] Same run with `random` (`n_samples: 50`) so we know how much the pool alone buys.

**Verify:** the run completes end to end on B against pulled Parquet (no Alpaca call), the
summary JSON lands under `artifacts/worker/`, and the numbers are in section 5.

### Step 2 — A real evolutionary search for composites (not started)

Replace the aggregator flip with a search that uses fitness. Branch `feat/composite-evolution`.

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

**Verify:** one full dry run (`--no-publish`) on B from the timer's unit; the plan's 3.3(b)
box ticks only after the first scheduled run has posted and A has admitted or rejected
every candidate with a recorded reason.

## 4. Later (not scheduled)

- Deeper structure: three-rule composites; regime-conditional branches (one rule set per
  regime bucket from the classifier).
- ML as a **ranker**, not a signal: predict holdout performance from
  `research_feature_vector_builder` output to decide which candidates get the expensive
  stages. Only after Step 2 shows the search finds things the pool cannot.
- Suite `v1` historical windows (`pre-launch-deployment-plan.md` section 5) once SIP
  historical access is confirmed; IEX 5-minute history distorts slippage.

## 5. Results log

| Date | Dataset version | Method | Generated / accepted | Survivors (gates) | Clusters | WF best / median | Holdout top-10 Sharpe median | Holdout drawdown median | Off-pool share | Wall-clock |
|---|---|---|---|---|---|---|---|---|---|---|
| | | grid | | | | | | | 0% | |
| | | random n=50 | | | | | | | 0% | |
| | | evolutionary | | | | | | | | |
