# Research Robustness Stages (Regime / Stress / Overfitting)

## Overview

The staged research pipeline (`research/pipeline/`) now continues past Monte
Carlo with three gates that **eliminate** strategies, not just score them:

```
initial_quality_filter → walk_forward → monte_carlo → regime → stress → overfitting
      (simulation)                                     ▲          ▲          ▲
                                                       new stages (this doc)
```

| Stage | YAML `type` | Question | Extra simulations per survivor |
|---|---|---|---|
| `RegimeStage` | `regime` | Does it only work in one kind of market? | 0 when Monte Carlo ran on the same window (reuses its representative run), else 1 |
| `StressStage` | `stress` | Does it break under shocks or higher execution costs? | 1 per cost multiplier (+1 baseline if none reusable) |
| `OverfittingStage` | `overfitting` | Do the combined fold / MC / regime signals say "curve-fit"? | 0 |

The pass/fail rules are pure functions in `research/pipeline/gates/` (no simulation
runner, unit-tested with synthetic data). The stages in `research/pipeline/stages/`
only handle pipeline plumbing.

## Design decisions

### Regimes are auto-classified, not hardcoded date windows

Named windows ("2020 crash", "2022 bear") can never fall inside the ~90-day
lookback the monthly platform replay uses, and they would need a re-simulation per
window. Instead `RegimeStage` labels **the research window itself**, day by day,
with the TASK-2.2 classifiers (`RegimeClassificationService`), then splits the
strategy's bar returns by label with the TASK-2.3 `RegimeAnalysisService`
(in-memory, `persist=False`).

Labels are computed on the fly (`gates/regime_labels.py`) instead of read from the
persisted `regime_classification` feature dataset, for two reasons:

1. The replay feature hook computes features one day at a time
   (`start_date == end_date`), so no persisted version ever covers a research
   window (`FeatureDatasetVersionsRepository.find_for_simulation` finds nothing).
2. The persisted defaults (50/200-bar trend MAs on 5-min bars) describe an
   intraday horizon and never warm up on one day of bars.

On-the-fly method: resample the window's bars (plus `label_warmup_calendar_days`,
default 45) to daily bars → classify with short windows (trend 10/20, vol / liquidity /
z-score 10) → portfolio label per date = modal label across symbols (ties broken
alphabetically) → attach to every equity-curve bar of that date. Labels are used
for attribution only, never as a trading signal, so labelling a day with its own
end-of-day classification does not leak information into the strategy's returns.

### Regime gate rules (`gates/regime_gate.py`)

Per configured dimension (default `trend`, `volatility`), over regimes with
`>= min_bars_per_regime` bars:

- worst-regime Sharpe `>= min_regime_sharpe`
- worst-regime drawdown `>= max_regime_drawdown`
- fraction of regimes with positive return `>= min_positive_regime_fraction`

A dimension with fewer than `min_evaluable_regimes` (default 2) evaluable regimes
cannot be judged; if no dimension can be judged, `on_insufficient_coverage`
(`pass` | `fail`, default `pass`) decides. A 90-day window that was bull
throughout is common, which is why the default is `pass` (recorded as a warning).

### Stress = equity-curve shocks + execution-cost re-simulations

- **Shocks** (existing `StressTestService`, no re-simulation): vol spike 2x/3x,
  −5 % / −10 % one-time shock, downside amplification, trend reversal, 50 bps
  per-bar drag. Held-fixed trades; cheap.
- **Costs** (new): the strategy is re-simulated with slippage + commission scaled
  by each `cost_multipliers` entry (default `[2.0, 3.0]`) via
  `SimulationRunRequest.cost_multiplier` →
  `SimulatedExecutionService.reset_for_run(cost_multiplier=…)` →
  `SimulationCostModelService.apply_costs(cost_multiplier=…)`. The multiplier is
  reset every run, so a stress run never leaks into the next one. Non-default
  multipliers extend the deterministic `run_id` key; default runs keep their IDs.

Gate: shock survival rate `>= min_shock_survival_rate` **and** cost survival rate
`>= min_cost_survival_rate` (a cost run survives when Sharpe `>= min_cost_sharpe`
and drawdown `>= max_cost_drawdown`).

### Overfitting = formal gate over earlier-stage evidence

Stages record per-strategy evidence in `StageResult.diagnostics`
(`StageDiagnostics`): walk-forward `fold_inputs`, Monte Carlo `mc_aggregation` +
`reference_result`, regime `regime_profile`. `PipelineRunner` calls
`stage.bind_prior_results(...)` before each stage, and `OverfittingStage`
combines the evidence through the existing `OverfittingAnalyzer`. Strategies with
`overfitting_probability > max_overfitting_probability` (default 0.6) are
eliminated.

`low_trade_count` and `narrow_period_alpha` are always available and weakly
weighted, so the evidence threshold counts only the **core** indicators
(train/test degradation, fold instability, MC instability, regime concentration):
below `min_core_indicators` (default 2), `on_insufficient_evidence` decides.

Place `overfitting` last: it can only see stages that ran before it.

## Platform replay integration

`run_scheduled_research_at_timestamp` builds all six stages whenever walk-forward
fires (>= 75 days of data). Replay thresholds live in
`_build_replay_experiment_definition` (`application/services/platform_replay/research_hooks.py`).
The research tick summary now includes `stage_funnel`
(`{stage_name: {entered, passed}}`) and `research_profile`.

The validation/intelligence layer after the pipeline now receives the same
evidence (`wf_fold_inputs`, `mc_aggregation`, `regime_profile`, and the MC
reference run's equity curve), so its RobustnessScore / deployability no longer
runs on the stage-1 curve alone.

### Cheap verification runs

`research.options.profile: smoke` in a replay fixture shrinks the research budget:
2 candidates per strategy family (10 vs 36), Monte Carlo `n_runs` 3 (vs 5), one
cost multiplier (2x). Stage logic and thresholds are unchanged.

```bash
atp platform backtest run \
  --fixture fixtures/platform/replays/medium/research_stages_smoke.yaml \
  --output artifacts/platform/backtests/research_stages_smoke.json
```

That fixture runs Jan 2 → Apr 5 2024 on 5 symbols with one tick per day; the Apr 1
research tick is the one that exercises all six stages.

## YAML examples

```yaml
- name: regime_robustness
  type: regime
  start_date: "2024-01-02"
  end_date: "2024-04-01"
  symbols: [SPY, QQQ, AAPL]
  dimensions: [trend, volatility]
  min_bars_per_regime: 390          # count in bars — ~5 trading days of 5-min bars
  min_regime_sharpe: -1.0
  max_regime_drawdown: -0.20
  min_positive_regime_fraction: 0.5
  on_insufficient_coverage: pass
  classifier_windows: { trend_short_window: 10, trend_long_window: 20 }

- name: stress_robustness
  type: stress
  start_date: "2024-01-02"
  end_date: "2024-04-01"
  symbols: [SPY, QQQ, AAPL]
  cost_multipliers: [2.0, 3.0]      # [] = shock scenarios only
  min_shock_survival_rate: 0.5
  min_cost_sharpe: 0.0
  min_cost_survival_rate: 0.5

- name: overfitting_gate
  type: overfitting
  max_overfitting_probability: 0.6
  min_core_indicators: 2
  min_trade_count: 30
  on_insufficient_evidence: pass
```

## Tests

| Scope | File |
|---|---|
| Regime labelling (resample, classify, modal, attach) | `tests/research/pipeline/gates/test_regime_labels.py` |
| Regime gate rules | `tests/research/pipeline/gates/test_regime_gate.py` |
| Shock transforms + stress gate rules | `tests/research/pipeline/gates/test_stress_gate.py` |
| Overfitting evidence + gate rules | `tests/research/pipeline/gates/test_overfitting_gate.py` |
| Cost multiplier (cost model, execution service reset, run_id) | `tests/research/simulation/test_cost_multiplier.py` |
| Stage orchestration with a scripted runner | `tests/research/pipeline/test_robustness_stages.py` |
| Real-engine smoke (all six stages, research hook, smoke profile) | `tests/research/test_research_stages_smoke.py` |

## Known gaps

- **Survivorship bias:** largely addressed; see
  `docs/backend/universe/survivorship_safe_replay.md`. The research tick now
  anchors its universe at the window start and fails if `SurvivorshipGuard`
  rejects the scope, and replays can use a point-in-time S&P 500 pool with
  delisting handling. Residual gaps are listed in that doc.
- **No bar-perturbation stress.** Shocks transform the realised equity curve with
  trades held fixed. Injecting gaps/shocks into the price bars so the strategy
  trades through them needs an in-memory perturbed-bar source for
  `SimulationWindowLoader` (bars currently only come from versioned Parquet).
- **Persisted regime labels are unused.** If the replay feature hook ever
  computes regime classification over a rolling window, a provider backed by
  `find_for_simulation` can replace the on-the-fly one (same
  `RegimeLabelProvider` protocol).
- **No checkpoint/resume for the new stages.** Walk-forward and Monte Carlo
  support `ResearchCheckpointService`; regime/stress runs are not checkpointed.
- **Annualisation.** Per-regime Sharpe uses 252 for daily-resampled equity curves
  and `BARS_PER_YEAR` (5-min) otherwise; upstream stage filters always use
  `BARS_PER_YEAR`, so regime Sharpe thresholds are not directly comparable to
  stage-1 `min_sharpe` on daily runs.
- **Thresholds are first-pass.** Replay defaults were sanity-checked on synthetic
  data only; tune them from the `stage_funnel` of real smoke/full runs.
