# Market Regime Classification

## Overview

The market regime classification system (TASK-2.2) adds deterministic, explainable
multi-dimensional market condition labels to the platform's persisted feature pipeline.
It is built on top of the TASK-2.1 feature dependency integration and produces a
versioned `regime_classification` dataset loadable by any simulation or research run.

---

## Design Philosophy

- **Deterministic**: same input bars → same regime labels, always.
- **Explainable**: every label ships with contributing signals and threshold metadata.
- **Reusable**: one computed dataset is shared across all simulations; no per-run
  recomputation.
- **Lineage-safe**: the dataset is versioned, validated, and linked to its source bar
  dataset via `FeatureDatasetVersion`.
- **No ML**: all classification is rule-based and threshold-driven.

---

## Regime Dimensions

Regimes are multi-dimensional: a market may simultaneously be bull + high_volatility +
high_liquidity.  Each dimension is classified independently.

### Trend Dimension

| Label | Condition |
|-------|-----------|
| `bull` | short_MA > long_MA **and** rolling_return_20d ≥ 0 |
| `bear` | short_MA < long_MA **and** rolling_return_20d < 0 |
| `sideways` | MA relationship and return disagree, or MA spread too small |

- Default windows: short=50, long=200 (configurable)
- Confidence = normalized MA spread, capped at 5% relative spread → 1.0

### Volatility Dimension

| Label | Condition |
|-------|-----------|
| `high_volatility` | expanding percentile of realized vol > 80th pct |
| `low_volatility` | expanding percentile of realized vol < 20th pct |
| `normal_volatility` | 20th–80th percentile |

- Realized vol = rolling std dev of daily returns (default window=20)
- Percentile is expanding (no look-ahead bias): each bar's vol is ranked against all
  prior bars for that symbol.
- Thresholds (the actual vol values at P20/P80) are stored per bar for explainability.

### Liquidity Dimension

| Label | Condition |
|-------|-----------|
| `high_liquidity` | expanding percentile of avg dollar volume > 80th pct |
| `low_liquidity` | expanding percentile of avg dollar volume < 20th pct |
| `normal_liquidity` | 20th–80th percentile |

- Dollar volume = close × volume, rolling average (default window=20)

### Mean-Reversion Dimension

| Label | Condition |
|-------|-----------|
| `trending` | zscore_std > expanding median **and** trend_strength > 0.5 |
| `mean_reverting` | zscore_std ≤ expanding median **and** trend_strength ≤ 0.5 |
| `undefined` | mixed signals (one indicator says trending, the other says reverting) |

- zscore_std = rolling std dev of the z-score series — high means the process drifts far
  from its mean (trending); low means it oscillates near zero (mean-reverting).
- trend_strength = |rolling_return_20d| / realized_vol — normalized directional movement.

### Risk Dimension (composite)

| Label | Condition |
|-------|-----------|
| `risk_on` | trend=bull **and** volatility ≠ high_volatility |
| `risk_off` | trend=bear **and** volatility = high_volatility |
| `neutral` | all other combinations |

---

## Persisted Dataset

**Feature name**: `regime_classification`
**Parquet key**: `feature_regime_classification`
**Storage path**: `data/features/regime_classification/dataset_version=<id>/symbol=.../year=.../month=...`

### Schema Columns

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `symbol` | string | No | Ticker symbol |
| `timestamp` | timestamp[us, UTC] | No | Bar timestamp |
| `date` | date32 | No | Date partition |
| `regime_trend` | string | Yes | bull / bear / sideways (null during warmup) |
| `regime_trend_confidence` | float64 | Yes | 0–1 confidence score |
| `trend_ma_short` | float64 | Yes | Short MA value |
| `trend_ma_long` | float64 | Yes | Long MA value |
| `trend_rolling_return_20d` | float64 | Yes | 20-bar return |
| `regime_volatility` | string | Yes | high/normal/low_volatility |
| `regime_volatility_confidence` | float64 | Yes | 0–1 |
| `volatility_realized` | float64 | Yes | Rolling realized vol |
| `volatility_percentile` | float64 | Yes | Expanding percentile (0–100) |
| `volatility_threshold_high` | float64 | Yes | Vol value at P80 for explainability |
| `volatility_threshold_low` | float64 | Yes | Vol value at P20 for explainability |
| `regime_liquidity` | string | Yes | high/normal/low_liquidity |
| `regime_liquidity_confidence` | float64 | Yes | 0–1 |
| `liquidity_avg_dollar_volume` | float64 | Yes | Rolling avg dollar volume |
| `liquidity_percentile` | float64 | Yes | Expanding percentile |
| `regime_mean_reversion` | string | Yes | trending / mean_reverting / undefined |
| `regime_mean_reversion_confidence` | float64 | Yes | 0–1 |
| `mean_reversion_zscore_std` | float64 | Yes | Z-score drift indicator |
| `mean_reversion_trend_strength` | float64 | Yes | Normalized directional strength |
| `regime_risk` | string | Yes | risk_on / risk_off / neutral |
| `underlying_dataset_version` | string | No | Source bar dataset lineage |
| `price_basis` | string | No | RAW or ADJUSTED |
| `year` | string | No | Partition year |
| `month` | string | No | Partition month |

Regime columns are nullable because warmup periods (e.g., first 200 bars for long_MA)
cannot be classified.

---

## Running the Pipeline

Via the feature pipeline CLI:

```bash
python -m autonomous_trading_platform.cli features run-pipeline \
  --dataset-version-id bars-v1 \
  --price-basis RAW \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --include-regime-classification
```

Or programmatically:

```python
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)

run_feature_pipeline_cycle(
    price_basis=PriceBasis.RAW,
    dataset_version_id="bars-v1",
    include_regime_classification=True,
)
```

Default parameters:
- `trend_short_window=50`, `trend_long_window=200`
- `vol_window=20`, `liquidity_avg_window=20`, `zscore_window=20`
- `high_percentile=80.0`, `low_percentile=20.0`

---

## Strategy Integration

Strategies declare the feature dependency in `StrategyDefinition.required_persisted_features`:

```python
StrategyDefinition(
    strategy_type="regime_aware_mean_reversion",
    required_persisted_features=("regime_classification",),
    warmup_bars_fn=lambda p: 200,
    ...
)
```

At simulation time, `FeatureDependencyResolverService` automatically resolves a
validated `regime_classification` dataset version that matches the simulation's
`source_dataset_version`, `price_basis`, and date coverage.

The dataset is then available in the strategy via `StrategyContext.features`:

```python
def evaluate(self, context: StrategyContext) -> StrategySignal | None:
    regime_table = context.features.get("regime_classification")
    if regime_table is None:
        return None

    regime_df = regime_table.to_pandas()
    latest = regime_df.iloc[-1]

    if latest["regime_trend"] == "bull" and latest["regime_risk"] == "risk_on":
        # trend-following logic
        ...
    elif latest["regime_mean_reversion"] == "mean_reverting":
        # mean reversion logic
        ...
```

---

## Code Organization

```
feature_engineering/
  regimes/
    __init__.py
    regime_type.py                  ← TrendRegime, VolatilityRegime, LiquidityRegime,
                                       MeanReversionRegime, RiskRegime enums +
                                       classification result types
    regime_classification_service.py ← Orchestrates all four classifiers
    classifiers/
      trend_regime_classifier.py
      volatility_regime_classifier.py
      liquidity_regime_classifier.py
      mean_reversion_regime_classifier.py
  services/
    regime_classification_feature_service.py  ← Feature-job adapter
  jobs/
    regime_classification_feature_job.py      ← Pipeline orchestration

storage/parquet/
  schemas.py           ← FEATURE_REGIME_CLASSIFICATION_SCHEMA
  datasets.py          ← FEATURE_REGIME_CLASSIFICATION_DATASET
  repositories/
    parquet_feature_repository.py  ← "regime_classification" in FEATURE_DATASETS_BY_NAME
```

---

## Relationship to Legacy `regime` Feature

The legacy `regime` feature (bull/bear/sideways via 50/200 MA crossover, dataset key
`feature_regime`) is unchanged and remains available.  The new `regime_classification`
dataset (`feature_regime_classification`) is richer and multi-dimensional.  Both are
independently versioned and lineage-tracked.  Strategies should migrate to
`regime_classification` for new work.

---

## Lineage and Validation

- Computed datasets are written in `unvalidated` state by
  `FeatureDatasetWriterService.write_feature_dataset()`.
- `mark_validated()` transitions them to `validated` state.
- `FeatureDependencyResolverService.resolve()` only selects `validated` datasets.
- All lineage checks from TASK-2.1 apply: source_dataset_version, price_basis, date
  coverage, and symbol count must all match the simulation request.

---

## Known Limitations

- Warmup bars (first `long_window` bars per symbol) produce null regime labels; simulations
  must account for this.
- Percentile thresholds are expanding (no look-ahead), so early labels are less stable.
- Universe-version enforcement is not yet persisted in `FeatureDatasetVersion`; symbol
  coverage is validated by count only.
- Computation-parameter specificity is not enforced by the resolver; any validated variant
  of `regime_classification` will be selected.

---

## Future Direction (Adaptive Strategy Layer)

TASK-2.2 deliberately does **not** implement automatic strategy behavior changes.
The expected follow-up tasks are:

- **TASK-2.3**: Regime-conditioned simulation analysis — slice performance metrics by
  regime labels from `regime_classification` to answer "when does this strategy work?"
- **TASK-3.x**: Regime-aware strategy generation — `CompositeRuleStrategy` regime filters
  to activate/deactivate rules based on current regime.
- **TASK-4.x**: Online regime classification for live/paper trading (separate from
  persisted feature pipeline).
