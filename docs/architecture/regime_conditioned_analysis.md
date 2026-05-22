# Regime-Conditioned Analysis

## Purpose

TASK-2.3 adds the first strategy intelligence layer to the platform. Given a completed simulation run, the regime analysis infrastructure answers:

- Which market conditions produce alpha for a given strategy?
- Which conditions cause losses?
- Which strategies are regime-sensitive vs. regime-robust?
- How does performance change around regime transitions?

This is **analysis infrastructure only**. It does not adapt live strategies, disable strategies, or make trading decisions. That is deferred to the adaptive strategy system (TASK-3.x+).

---

## Architecture

```
research/analysis/
└── regimes/
    ├── regime_bucket.py           # RegimeBucket — (dimension, label) identifier
    ├── regime_metrics.py          # RegimeConditionedMetrics dataclass + computation
    ├── regime_join_service.py     # RegimeJoinService — exact-timestamp join
    ├── regime_transition_analysis.py  # RegimeTransitionAnalyzer + result types
    ├── strategy_regime_profile.py     # StrategyRegimeProfile + sensitivity scores
    ├── regime_analysis_result.py      # RegimeAnalysisResult container
    ├── regime_analysis_repository.py  # Parquet persistence
    └── regime_analysis_service.py    # RegimeAnalysisService — main orchestrator
```

---

## Core Concepts

### Regime Dimensions

Five independent regime dimensions are available (from TASK-2.2):

| Dimension | Column | Labels |
|-----------|--------|--------|
| trend | `regime_trend` | `bull`, `bear`, `sideways` |
| volatility | `regime_volatility` | `high_volatility`, `normal_volatility`, `low_volatility` |
| liquidity | `regime_liquidity` | `high_liquidity`, `normal_liquidity`, `low_liquidity` |
| mean_reversion | `regime_mean_reversion` | `trending`, `mean_reverting`, `undefined` |
| risk | `regime_risk` | `risk_on`, `risk_off`, `neutral` |

### RegimeBucket

Identifies a specific (dimension, label) pair:
```python
RegimeBucket(dimension="trend", label="bull")
```

### RegimeConditionedMetrics

Per-bucket performance metrics:
- **Exposure**: `bar_count`, `exposure_fraction` (fraction of simulation in this regime)
- **Return**: `total_return`, `cagr`
- **Risk**: `sharpe`, `sortino`, `volatility`, `max_drawdown`
- **Trade**: `trade_count`, `win_rate`, `expectancy`, `profit_factor`, `avg_win`, `avg_loss`, `trade_frequency`
- **Bar**: `avg_bar_return`

All metrics are `None` when insufficient data exists (< 2 bars for return/risk metrics).

---

## Regime Join

### Join Strategy

The `RegimeJoinService` uses **exact timestamp joins** only. No forward-fill, no backward-fill, no look-ahead.

- **Equity curve** (portfolio-level): aggregates regime labels across all symbols at each timestamp by modal value (most common label).
- **Trade logs and per-bar metrics** (symbol-level): exact join on `(symbol, timestamp)`.

Bars without a matching regime row receive `NaN` regime columns and are excluded from computations.

### Why Exact Join

Regime labels are classified from historical data up to bar timestamp t. Using the label at t for analysis at t is look-ahead-safe. Forward-filling would attribute a future regime label to a past bar (incorrect). The regime dataset and simulation bar dataset are derived from the same underlying bars, so timestamps align exactly when both use the same dataset version.

---

## Regime-Conditioned Metrics Methodology

### Bar Returns

Bar returns are computed from the **full portfolio equity curve** first, then filtered to regime bars:

```
bar_return[t] = (equity[t] - equity[t-1]) / equity[t-1]
```

This is critical: do **not** slice the equity curve and re-compute bar-to-bar returns across non-contiguous bars. That would incorrectly compute returns spanning multiple time periods.

### Sharpe in a Regime

Computed from filtered bar returns `{r_i : regime at t_i == label}`:

```
sharpe = mean(returns) / std(returns) * sqrt(bars_per_year)
```

This is "regime-conditioned Sharpe" — average annualized return-per-unit-of-risk specifically within this regime.

### CAGR in a Regime

Geometric compound of all bar returns in the regime:

```
cagr = (product(1 + r_i))^(bars_per_year / n) - 1
```

### Max Drawdown in a Regime

Computed on the synthetic equity curve built from compounding regime bar returns in sequence (not the full equity curve sliced at non-contiguous bars).

### Trade Metrics in a Regime

Trade fills are joined with their entry-bar regime label. Only trades executed during regime X bars are included in regime X's trade metrics.

---

## Strategy Regime Profile

`StrategyRegimeProfile` summarizes per-dimension sensitivity and robustness.

### Sensitivity Score (per dimension)

- **sharpe_std**: standard deviation of Sharpe across regime labels with sufficient data (≥ 10 bars). High = regime-sensitive.
- **sharpe_range**: max Sharpe - min Sharpe. Range of performance across conditions.
- **best_regime**: label with highest Sharpe.
- **worst_regime**: label with lowest Sharpe.
- **regime_robustness**: min Sharpe across all labelled regimes. Higher = more robust.

### Overall Sensitivity

Mean of `sharpe_std` across all 5 dimensions.

### is_regime_robust

`True` if `min_sharpe >= 0` across all regimes with sufficient data in all dimensions. A regime-robust strategy generates non-negative returns in every regime it encounters with meaningful exposure.

### Example Interpretations

```
momentum strategy:
  trend sensitivity:
    best_regime:      bull (Sharpe 2.4)
    worst_regime:     bear (Sharpe -1.1)
    regime_robustness: -1.1  ← fragile

factor strategy:
  trend sensitivity:
    best_regime:      bull (Sharpe 1.2)
    worst_regime:     bear (Sharpe 0.8)
    regime_robustness: 0.8  ← robust
```

---

## Regime Transition Analysis

`RegimeTransitionAnalyzer` detects regime changes and measures performance around them.

### Transition Detection

Scans the sorted regime label sequence, detecting index positions where the label changes. Each `RegimeTransition` records:
- Timestamp of the change
- From-regime and to-regime
- Duration of the preceding regime (in bars)

### Transition Matrix

`dict[from_regime, dict[to_regime, count]]` — frequency of each regime shift pair.

### Duration Statistics

Per-label `RegimeDurationStats`:
- episode_count: number of distinct contiguous periods
- mean/median/max/min duration (bars)

### Transition Windows

For each transition, computes performance in the `window_bars` period before and after:
- `pre_return`: mean bar return in the window before the transition
- `post_return`: mean bar return in the window after the transition
- `pre_max_drawdown`, `post_max_drawdown`: max drawdown in each window

`avg_pre_transition_return` and `avg_post_transition_return` aggregate these across all transitions for the dimension.

---

## Artifact Persistence

Three Parquet datasets are written per analysis run, partitioned by `(experiment_id, run_id, stage_name, window_role)`:

| Dataset | Path | One row per |
|---------|------|-------------|
| `REGIME_ANALYSIS_METRICS_DATASET` | `simulations/regime_analysis/metrics/` | (run, dimension, label) |
| `REGIME_TRANSITION_SUMMARY_DATASET` | `simulations/regime_analysis/transitions/` | (run, dimension, from_regime, to_regime) |
| `STRATEGY_REGIME_PROFILE_DATASET` | `simulations/regime_analysis/profiles/` | (run, dimension) |

All artifacts carry `run_id`, `experiment_id`, `strategy_id`, `dataset_version`, `stage_name`, `window_role` for lineage traceability.

Persistence is **optional** (controlled by `persist=True/False` on `RegimeAnalysisService.analyze()`). Analysis runs without persistence for testing and ad-hoc exploration.

---

## Integration with SimulationRunner

Regime analysis runs **post-simulation** and is **optional**. The simulation runner produces its normal artifacts first (equity_curve, trade_logs, etc.). Regime analysis then consumes those artifacts plus persisted regime classification data.

To run regime analysis after a simulation:

```python
from autonomous_trading_platform.research.analysis.regimes.regime_analysis_service import (
    RegimeAnalysisService,
    RegimeAnalysisRequest,
)

service = RegimeAnalysisService(repository=repo)
result = service.analyze(
    RegimeAnalysisRequest(
        equity_curve=sim_result.equity_curve,
        trade_logs=trade_logs_df,
        regime_data=regime_classification_df,
        identity=artifact_identity,
    )
)
print(result.summary())
```

The `regime_data` DataFrame must have `symbol`, `timestamp`, and regime columns matching the simulation's bar timestamps.

---

## CLI

```bash
python scripts/analyze_regimes.py \
    --run-id <uuid> \
    --experiment-id <experiment_id> \
    --strategy-id <strategy_id> \
    --dataset-version v1 \
    --output human \
    --dimension trend
```

Output formats: `human` (default), `json`, `yaml`.

---

## Current Limitations

1. **Composite strategy explainability by regime**: `CompositeRuleStrategy` emits `last_explainability` dicts, but this data is not currently persisted to Parquet. Component-level regime analysis (which rules fail in which regimes) requires a separate explainability persistence layer — deferred.

2. **Regime data must align with simulation timestamps**: The join is exact. If regime data uses a different bar interval or dataset version than the simulation, many bars will have NaN regime labels. Ensure both use the same underlying dataset.

3. **Portfolio-level regime aggregation**: For multi-symbol simulations, the regime at each equity curve bar is the modal label across all active symbols. This may not capture per-symbol regime heterogeneity. Symbol-level regime slicing is available through `join_symbol_frame()` on per-bar metrics.

4. **Non-contiguous regime episodes**: Metrics like Sharpe are computed on all bars in a regime, whether contiguous or not. The Sharpe is an approximation for strategies where regime exposures are intermittent rather than sustained.

---

## Future Directions

- **Adaptive strategy gating by regime** (TASK-3.x): use regime profiles to gate strategy activation/deactivation.
- **Meta-allocation by regime** (TASK-4.x): weight strategies differently per current regime.
- **Composite explainability by regime**: persist CompositeRuleStrategy explainability metadata and join with regime labels.
- **Cross-strategy regime comparison**: compare profiles across multiple strategies to identify regime-specialised vs. regime-robust strategy sets.
- **Regime-conditioned walk-forward analysis**: analyse regime performance across walk-forward folds for out-of-sample regime validation.
