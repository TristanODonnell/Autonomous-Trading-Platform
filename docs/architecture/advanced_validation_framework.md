# Advanced Validation Framework

## Overview

TASK-2.4 builds a comprehensive research quality validation layer on top of the
established simulation, regime analysis, and strategy generation infrastructure
from earlier tasks. The goal is to answer one decisive question before any
strategy is promoted or allocated capital:

> **Is this strategy's performance robust, or is it a statistical artifact?**

The framework provides six orthogonal validation dimensions:

| Dimension | Question answered |
|-----------|-------------------|
| Walk-forward consistency | Does the strategy generalise across time? |
| Regime validation | Does it work across all market conditions? |
| Stress resilience | Does it survive adverse scenarios? |
| Overfitting analysis | Are there structural overfit signals? |
| Parameter sensitivity | Is performance stable across parameter values? |
| Survivorship safety | Was the universe free of look-ahead bias? |

A unified **RobustnessScore** aggregates all dimensions into a single
deployability signal.

---

## Architecture

```
research/validation/
├── validation_result.py           ValidationStageResult, ValidationSummary
├── robustness_score.py            RobustnessScore, RobustnessScoreBuilder
├── walk_forward_validation.py     WalkForwardValidationService, FoldValidationInput
├── stress_test_service.py         StressTestService, StressScenario, StressTestSummary
├── survivorship_validation.py     SurvivorshipValidationService
├── overfitting_analysis.py        OverfittingAnalyzer, OverfittingAnalysisResult
├── parameter_sensitivity_analysis.py  ParameterSensitivityAnalyzer, SensitivityProfile
├── validation_orchestrator.py     ValidationOrchestrator (top-level entry point)
└── validation_artifact_repository.py  Parquet persistence
```

The orchestrator accepts pre-computed simulation artifacts (equity curves,
metrics, fold data) and runs each enabled stage purely in memory. No re-simulation
is required unless parameter sensitivity analysis is enabled.

---

## Robustness Scoring

The overall robustness score is a weighted composite of six components, each
normalised to [0, 1]:

```
robustness = Σ (effective_weight_i × component_score_i)
```

Default weights:

| Component | Default weight | Rationale |
|-----------|---------------|-----------|
| walk_forward_consistency | 0.30 | Most important: temporal generalisation |
| mc_stability | 0.20 | Structural robustness across random orderings |
| regime_robustness | 0.20 | Conditions-agnostic performance |
| parameter_stability | 0.15 | Not a knife-edge fit |
| stress_resilience | 0.10 | Survives adverse market conditions |
| overfitting_resistance | 0.05 | Heuristic overfit indicators |

When a component is absent (no data supplied), its weight is redistributed to
active components so the overall score remains comparable across different run
configurations.

### Walk-forward consistency

```
fold_consistency = n_folds_passed / total_folds
degradation_penalty = clamp(train_test_degradation / 2)
walk_forward_score = clamp(fold_consistency − degradation_penalty)
```

A strategy that passes all folds with minimal degradation scores 1.0.

### Monte Carlo stability

```
mc_stability = 1 / (1 + CoV(Sharpe across N MC runs))
```

CoV = 0 → stability = 1.0 (all runs are identical). CoV = 1 → stability = 0.5.

### Regime robustness

```
base = 0.5 if regime_robust else 0.25
sharpe_bonus = clamp(worst_regime_sharpe / 3) × 0.3
sensitivity_penalty = clamp(overall_sensitivity / 5) × 0.2
regime_score = clamp(base + sharpe_bonus − sensitivity_penalty)
```

### Stress resilience

```
stress_resilience = survival_rate  (fraction of scenarios survived)
```

### Overfitting resistance

```
overfitting_resistance = 1 − overfitting_probability
```

### Parameter stability

```
parameter_stability = overall_stability_score from ParameterSensitivityAnalyzer
```

---

## Walk-Forward Validation

### Methodology

The `WalkForwardValidationService` operates on a list of `FoldValidationInput`
objects. Each fold contains:

- `train_sharpe`, `test_sharpe` — Sharpe ratios on train and test windows
- `fold_passed` — True iff the strategy met filters on **both** windows

Computed metrics:
- **fold_consistency**: fraction of folds passed (≥ 0.6 is "consistent")
- **train_test_degradation**: `(train_sharpe − test_sharpe) / |train_sharpe|`
  — positive values indicate the strategy fits training data better than it
  generalises to new periods
- **fold_sharpe_cv**: coefficient of variation of test-window Sharpes
- **fold_sharpe_stability**: `1 / (1 + CoV)` — higher is more stable
- **expanding_test_sharpes**: cumulative mean of test Sharpes over folds

### Window types

The `WalkForwardStage` (Stage 3 pipeline) already implements rolling windows.
The validation service additionally documents three patterns:

| Type | Description |
|------|-------------|
| Rolling | Fixed-width train window slides forward by step_days |
| Expanding | Train window grows from a fixed anchor |
| Anchored | Test window advances; train always starts from experiment start |

### Fold identity

Each fold in `WalkForwardStage` is assigned a deterministic `window_role` of
the form `fold_{i}_train` / `fold_{i}_test`. This ensures artifact identities
never collide across folds or stages.

---

## Stress Testing

### Methodology

Stress tests apply deterministic return transformations to an existing equity
curve and recompute metrics from the modified series. No re-simulation is
performed.

A strategy "survives" a scenario when:
```
stressed_sharpe >= min_sharpe_threshold
AND stressed_drawdown >= max_drawdown_threshold
```

### Built-in scenarios

| Scenario | Type | Description |
|----------|------|-------------|
| `volatility_spike_2x` | `vol_multiplier` | Multiply all bar returns by 2 |
| `volatility_spike_3x` | `vol_multiplier` | Multiply all bar returns by 3 |
| `return_shock_neg5pct` | `one_time_shock` | −5% one-time return shock at midpoint |
| `return_shock_neg10pct` | `one_time_shock` | −10% one-time return shock at midpoint |
| `drawdown_amplification` | `downside_amplification` | 2× downside bar-return magnitude |
| `trend_reversal` | `sign_flip` | All bar returns sign-flipped |
| `liquidity_collapse` | `slippage_penalty` | +50 bps per-bar cost |

### Extensibility

Custom scenarios can be passed via `StressTestService(scenarios=[...])` or
`ValidationConfig(stress_scenarios=[...])`. The `shock_type` field is
dispatched to the relevant transform function.

---

## Survivorship-Bias-Safe Validation

### Checks performed

1. **Universe config check** (via `SurvivorshipGuard`): verifies the experiment
   universe scope is point-in-time anchored.
2. **Symbol future-leak check**: any symbol in `known_future_symbols` that also
   appears in the experiment universe is flagged.
3. **Fold-level membership check**: for each walk-forward fold, verifies all
   symbols were in the universe as of that fold's start date (requires
   `UniverseHistoryService`).

The existing `SurvivorshipGuard` and `UniverseHistoryService` provide the
underlying checks. This service composes them into a validation-layer result.

---

## Overfitting Detection

Seven heuristic indicators, each in [0, 1]:

| Indicator | Threshold | Description |
|-----------|-----------|-------------|
| `train_test_degradation` | > 0.5 suspicious | Normalised `(train − test) / |train|` |
| `fold_instability` | > 0.5 suspicious | Normalised CoV of test Sharpe across folds |
| `mc_instability` | > 0.5 suspicious | Normalised CoV of Sharpe across MC runs |
| `regime_concentration` | < 0.4 suspicious | Fraction of regimes with positive Sharpe |
| `low_trade_count` | < 30 trades | Binary flag |
| `narrow_period_alpha` | > 0.7 suspicious | Fraction of return from top-10% of bars |
| `parameter_fragility` | > 0.6 suspicious | Sensitivity score from parameter analysis |

```
overfitting_probability = Σ (weight_i / active_weight_sum × indicator_i)
```

A probability ≥ 0.6 triggers a warning. The `most_suspicious` list names the
top-3 indicators by score.

---

## Parameter Sensitivity Analysis

### Methodology

For each tunable `ParameterSpec`:

1. Generate `n_steps` evenly-spaced values across `[min_value, max_value]`
2. Evaluate `run_fn(params)` at each value to get `(sharpe, drawdown, return)`
3. Compute:
   ```
   sensitivity_score = std(sharpe) / (range(sharpe) + ε)  ← clamped to [0, 1]
   stability_score   = 1 − sensitivity_score
   ```
4. Identify the stability region: contiguous range of parameter values where
   `sharpe >= min_acceptable_sharpe`

The `run_fn` callable is supplied by the caller, making this service
simulation-agnostic and independently testable.

`overall_stability_score = mean(stability_score per parameter)`

---

## Validation Artifact Persistence

Validation artifacts are persisted to hive-partitioned Parquet datasets under
`{base_path}/validation/`:

| Dataset | Path | Partition |
|---------|------|-----------|
| Robustness scores | `validation/robustness_scores/` | experiment_id / strategy_id |
| Walk-forward results | `validation/walk_forward_results/` | experiment_id / strategy_id |
| Stress test results | `validation/stress_test_results/` | experiment_id / strategy_id |
| Overfitting analysis | `validation/overfitting_analysis/` | experiment_id / strategy_id |
| Sensitivity profiles | `validation/sensitivity_profiles/` | experiment_id / strategy_id |

Artifact persistence is optional and non-blocking — failures are logged and
ignored so that validation results are always returned.

---

## CLI

```bash
# Full validation from persisted equity curve
python scripts/analyze_validation.py \
    --experiment-id my-exp \
    --strategy-id strat_001 \
    --dataset-version v1 \
    --run-id <uuid>

# Stress-test mode only
python scripts/analyze_validation.py \
    --experiment-id my-exp \
    --strategy-id strat_001 \
    --dataset-version v1 \
    --run-id <uuid> \
    --mode stress --output json

# Overfitting analysis only
python scripts/analyze_validation.py \
    --experiment-id my-exp \
    --strategy-id strat_001 \
    --dataset-version v1 \
    --run-id <uuid> \
    --mode overfitting
```

Exit code: **0** if validation passes, **1** if it fails.

---

## Current Limitations

- Parameter sensitivity requires N simulation runs and is therefore **disabled
  by default**. Enable via `ValidationConfig(enable_parameter_sensitivity=True)`
  and supply a `sensitivity_run_fn`.
- Stress tests operate on the equity curve level, not bar-by-bar re-simulation.
  Microstructure effects (order execution, fill quality) are not modelled.
- Fold-level survivorship checks require a `UniverseHistoryService` instance to
  be injected.
- The robustness score heuristics and weights are configurable but start as
  reasonable defaults. Calibration against historical promotions is a future work item.

---

## Future ML-Assisted Research Direction

The current implementation uses explainable heuristics. Future enhancements may:

- Train a gradient boosted model on historical strategy promotion outcomes to
  predict deployability from raw validation signals
- Replace fixed sensitivity thresholds with distribution-aware anomaly detection
- Add Bayesian overfitting probability using prior distributions over known
  good and bad strategies

Any ML layer should wrap (not replace) the existing heuristic layer so that
explainability is preserved for audit purposes.
