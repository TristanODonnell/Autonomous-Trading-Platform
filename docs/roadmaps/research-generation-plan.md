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

Last updated: 2026-11-07 (Step 0 audit done, R4–R7 and D1 scoped; nothing built yet).

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

**R4 — Audit the remaining gates the same way (not started).** Regime, stress, overfitting
and parameter-stability stages (`pipeline/stages/regime_stage.py`, `stress_stage.py`,
`overfitting_stage.py`, `pipeline/gates/*`, the `ParameterSensitivityAnalyzer`) were not
read on 2026-11-07 and sit in the same funnel. Stress re-runs the simulation under cost
multipliers (`_gate_stage_support.py` jobs carry `cost_multiplier`), regime slices by the
classifier's labels, overfitting consumes the walk-forward evidence R2 found to be
survivorship-biased. Same method: trace the real input path, compare with what the tests
feed, look for fail-open branches and `None`-coercion.
- [ ] One audit note per stage under this heading (what it measures, where its inputs
      really come from, what the tests prove, fail-open paths); each confirmed problem
      becomes its own R-number with a fix list.

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

**Definition of done for any change under `research/simulation/`:** the research ↔
trading-cycle parity check from `portfolio-rotation-plan.md` 5c (re-sim vs platform
correlation ≥ 0.99 on the shared fixture) stays green. R1's bootstrap does not touch the
simulator; stochastic fills or any execution-model change would, and must re-run it.

**Step 0 follow-ups (trail the fixes above; do before Step 5):**
- Cache and identity hygiene: `SimulationResultCache` keys and the checkpoint identity
  disagree about what makes a run unique (seed in one, hardcoded `universe_version="v1"` in
  the other; neither knows a bootstrap spec). Fix in one place before R3 wiring and before
  the monthly job reuses cached runs.
- Research observability (pre-launch plan 2.3, unchecked): `run.mode`, `backtest.run_id`,
  `fixture`, `sim_timestamp` span/log attributes; heavy trace sampling for backtests; a
  worker/research-runs dashboard. The checkpoint metrics
  (`observability/metrics.py:1314-1321`) start firing once R3 lands.
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

### Step 1 — Baseline measurement (not started)

Run the existing pipeline on the window B now holds and record the numbers, so the
generator change has a "before". Run it **after R1, R2 and the D1 check land** (and after the SIP re-pull if it
turns out free), or the baseline is scored by a vacuous Monte Carlo and leaky folds on thin
bars and has to be redone.

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
- Suite `v1` historical windows (`pre-launch-deployment-plan.md` section 5) once D1 has
  answered the SIP question and R6's holdout declaration exists.

## 5. Results log

| Date | Dataset version | Method | Generated / accepted | Survivors (gates) | Clusters | WF best / median | Holdout top-10 Sharpe median | Holdout drawdown median | Off-pool share | Wall-clock |
|---|---|---|---|---|---|---|---|---|---|---|
| | | grid | | | | | | | 0% | |
| | | random n=50 | | | | | | | 0% | |
| | | evolutionary | | | | | | | | |
