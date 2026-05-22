# ML-Assisted Research Intelligence (TASK-2.5)

## Overview

The research intelligence layer consumes existing validation and regime
analysis artifacts to produce **candidate ranking, strategy clustering,
robustness estimation, and overfitting probability estimation**.

This layer is:

- **Research intelligence** — offline analysis and prioritisation
- **Deterministic** — same inputs always produce the same outputs
- **Explainable** — every score is decomposed into labelled components
- **Artifact-friendly** — outputs persist to Parquet alongside simulation artifacts

This layer is NOT:

- Predictive market ML
- Deep learning or neural nets
- Reinforcement learning
- Live adaptive trading
- Online learning
- Autonomous portfolio management

---

## Architecture

```
research/intelligence/
  __init__.py
  research_feature_vector_builder.py   ← canonical normalised feature vectors
  candidate_ranking_service.py         ← deterministic weighted ranking
  strategy_clustering_service.py       ← hierarchical clustering
  robustness_prediction_service.py     ← deployment robustness estimation
  overfitting_estimation_service.py    ← overfit probability aggregation
  regime_similarity_analysis.py        ← regime fingerprints & similarity
  research_intelligence_summary.py     ← summary container
  research_intelligence_service.py     ← top-level orchestrator
  research_intelligence_artifact_repository.py  ← Parquet persistence
```

### Data Flow

```
ValidationSummary (TASK-2.4)     StrategyRegimeProfile (TASK-2.3)
        │                                  │
        └──────────┬───────────────────────┘
                   ▼
      ResearchFeatureVectorBuilder
                   │
         ResearchFeatureVector
        (normalised 35-field vector)
                   │
       ┌───────────┼──────────────┬────────────────┐
       ▼           ▼              ▼                ▼
  CandidateRanking  Clustering  RobustnessEstimate  OverfitEstimate
       │           │              │                │
       └───────────┴──────────────┴────────────────┘
                   │
         ResearchIntelligenceSummary
                   │
    ResearchIntelligenceArtifactRepository
                   │
          Parquet (intelligence/)
```

---

## Research Feature Vector

The `ResearchFeatureVector` is a stable, normalised 35-field numeric
vector that encodes all available research intelligence signals.

**All values are in [0, 1].  Higher is consistently better.**

### Field Groups

| Group | # Fields | Source |
|-------|----------|--------|
| Validation robustness components | 7 | ValidationSummary.robustness_score |
| Overfitting indicators | 9 | ValidationSummary.stage_results["overfitting"] |
| Walk-forward metrics | 4 | ValidationSummary.stage_results["walk_forward"] |
| Stress metrics | 2 | ValidationSummary.stage_results["stress_test"] |
| Regime sensitivity | 7 | StrategyRegimeProfile |
| Strategy metadata | 6 | StrategyFamily + n_parameters |

### Normalisation Rules

| Signal type | Normalisation |
|------------|---------------|
| Already-[0,1] components | Used as-is |
| Sharpe ratios | `clamp(0.5 + sharpe / 6.0, 0, 1)` |
| Regime robustness (worst-regime Sharpe) | `clamp(0.5 + v / 4.0, 0, 1)` |
| Overall regime sensitivity | `1 - clamp(sensitivity / 2.0)` |
| Bad signals (degradation, instability) | `1 - normalised_bad_signal` |
| Boolean flags (low_trade_count) | 0.0 if True (bad), 1.0 if False |
| Missing data | 0.5 (neutral) |

**Data completeness** tracks what fraction of fields have real data vs
neutral defaults.

---

## Candidate Ranking

`CandidateRankingService` computes a `CandidateScore` for each strategy.

### Composite Score Formula

```
composite_score = Σ (effective_weight_i × signal_i)
```

where effective weights are normalised so they sum to 1.0.

### Default Weights

| Signal | Weight | Rationale |
|--------|--------|-----------|
| robustness_overall | 0.25 | Primary robustness summary |
| overfitting_resistance | 0.20 | Most important deployability gate |
| regime_robustness | 0.15 | Works across market conditions |
| walk_forward_consistency | 0.15 | Temporal generalisation |
| stress_resilience | 0.10 | Adverse scenario survival |
| parameter_stability | 0.10 | Not a knife-edge parameter fit |
| trade_reliability | 0.05 | Statistical significance |

### Deployability Score

A separate, stricter score using only:
- walk_forward_consistency
- overfitting_resistance
- regime_is_robust
- stress_survival_rate

Strategies are flagged `is_deployable = deployability_score >= threshold`.

**Note:** `is_deployable=True` does NOT automatically enable trading.
It is a research prioritisation flag only.

### Weakness Flags

Hard-threshold checks that flag specific concerns regardless of the
composite score, e.g. "weak walk-forward consistency", "high overfit risk".

---

## Strategy Clustering

`StrategyClusteringService` groups candidates using agglomerative
hierarchical clustering (single-linkage) over Euclidean distances on the
normalised feature vector.

### Algorithm

1. Compute pairwise Euclidean distances
2. Iteratively merge closest pair of distinct clusters
3. Stop merging when min distance ≥ `distance_threshold` (default 0.30)
4. Apply `max_clusters` cap
5. Sort clusters by size (largest first)

### Cluster Properties

| Property | Description |
|----------|-------------|
| `representative_strategy_id` | Member closest to centroid |
| `intra_cluster_variance` | Mean squared distance to centroid |
| `dominant_family` | Most common strategy family in cluster |
| `regime_bias` | Detected regime specialisation |
| `is_parameter_spam` | True if very low variance + > 2 members |

---

## Robustness Estimation

`RobustnessPredictionService` estimates deployment robustness as a
probability in [0, 1].

### Aggregation

Weighted average of validation signals (using feature vector fields):

| Signal | Weight |
|--------|--------|
| val_robustness_overall | 0.30 |
| val_walk_forward_consistency | 0.20 |
| overfit_resistance | 0.20 |
| val_regime_robustness | 0.15 |
| val_stress_resilience | 0.10 |
| wf_fold_sharpe_stability | 0.05 |

### Fragility Detection

If any of the following signals falls below a hard floor, fragility is
boosted regardless of the weighted average:

- walk_forward_consistency < 0.25 → "very low walk-forward consistency"
- overfit_resistance < 0.20 → "very high overfitting risk"
- regime_robustness < 0.15 → "catastrophic regime failure"
- stress_resilience < 0.15 → "fails nearly all stress scenarios"

### Deployment Suitability

| Band | Condition |
|------|-----------|
| SUITABLE | robustness_probability ≥ 0.60 and no fragility flags |
| BORDERLINE | robustness_probability ≥ 0.40 |
| UNSUITABLE | below borderline threshold |

---

## Overfitting Estimation

`OverfittingEstimationService` produces a higher-level overfit probability
by aggregating the per-indicator signals from TASK-2.4, plus regime signals.

### Two-Level Aggregation

**Primary indicators** (85% weight):

| Indicator | Weight |
|-----------|--------|
| train_test_degradation | 0.25 |
| fold_instability | 0.20 |
| MC instability | 0.15 |
| regime_concentration | 0.15 |
| parameter_fragility | 0.10 |
| narrow_period_alpha | 0.08 |
| low_trade_count | 0.07 |

**Regime signals** (15% weight by default):

| Signal | Weight |
|--------|--------|
| regime_sensitivity_norm | 0.60 |
| regime_is_robust | 0.40 |

### Risk Bands

| Band | Probability |
|------|-------------|
| LOW | < 0.30 |
| MEDIUM | 0.30 – 0.55 |
| HIGH | 0.55 – 0.75 |
| CRITICAL | ≥ 0.75 |

---

## Regime-Aware Intelligence

`RegimeSimilarityAnalyzer` builds a `RegimeFingerprint` from
`StrategyRegimeProfile` and supports:

- Pairwise strategy similarity by regime profile
- Regime-specialisation detection (strong dominant dimension)
- Portfolio regime diversification score

### RegimeFingerprint Vector

A stable 20-element vector:
- 15 elements: per-label Sharpe (normalised) for all 5 × 3 regime buckets
- 5 elements: per-dimension sensitivity scores

Used for cosine similarity comparisons.

### Regime Specialists

A strategy is a "regime specialist" if its dominant dimension's average
normalised Sharpe significantly exceeds all other dimensions.  Useful for
identifying strategies that may only work in bull/trending markets, etc.

---

## Artifact Persistence

Three Parquet datasets are written:

| Dataset | Key | Partition |
|---------|-----|-----------|
| `intelligence/candidate_rankings/` | per strategy × run | (experiment_id, strategy_id) |
| `intelligence/regime_fingerprints/` | per strategy × dimension | (experiment_id, strategy_id) |
| `intelligence/cluster_assignments/` | per cluster member | (experiment_id) |

All artifacts are linked to `run_id`, `experiment_id`, `strategy_id`,
`dataset_version`, and `config_hash`.

---

## CLI

```bash
# Rank a single strategy
python scripts/run_research_intelligence.py rank-strategies \
    --experiment-id my-experiment \
    --strategy-id strat_001 \
    --dataset-version v1 \
    --run-id <uuid> \
    --output human

# Cluster multiple strategies
python scripts/run_research_intelligence.py cluster-strategies \
    --experiment-id my-experiment \
    --strategy-ids strat_001,strat_002,strat_003 \
    --dataset-version v1 \
    --output json

# Full intelligence summary
python scripts/run_research_intelligence.py summarize-research-intelligence \
    --experiment-id my-experiment \
    --strategy-id strat_001 \
    --dataset-version v1

# Overfit risk analysis
python scripts/run_research_intelligence.py analyze-overfit-risk \
    --experiment-id my-experiment \
    --strategy-id strat_001 \
    --dataset-version v1 \
    --output human
```

---

## Design Invariants

1. **Deterministic**: same inputs → same vector → same scores.
2. **Explainable**: every score decomposes into labelled components.
3. **No market prediction**: this layer analyses research metadata only.
4. **No autonomous promotion**: `is_deployable=True` is a human-review flag.
5. **Graceful degradation**: missing inputs produce neutral (0.5) defaults.
6. **No duplication**: existing TASK-2.4 / TASK-2.3 systems are consumed, not rebuilt.

---

## Deferred Systems

The following are intentionally out of scope for TASK-2.5:

| System | Reason deferred |
|--------|----------------|
| Predictive alpha ML | Requires live/real-time market data pipeline |
| Deep learning models | Requires labelled training datasets at scale |
| Reinforcement learning | Requires simulation environment interface |
| Live adaptive strategy switching | Requires TASK-3.x regime gating infrastructure |
| Meta-allocation weighting | Requires TASK-4.x portfolio layer |
| Online learning | Requires TASK-3.x + live execution layer |

These are reserved for future research phases after the deterministic
intelligence layer is validated in production.
