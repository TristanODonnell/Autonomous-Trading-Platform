"""
Research intelligence artifact repository (TASK-2.5).

Persists ResearchIntelligenceSummary artifacts to Parquet using the
platform's standard hive-partitioned dataset conventions.

Datasets written:
  intelligence/candidate_rankings/      One row per summary
  intelligence/regime_fingerprints/     One row per fingerprint dimension
  intelligence/cluster_assignments/     One row per cluster member
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from autonomous_trading_platform.research.intelligence.research_intelligence_summary import (
    ResearchIntelligenceSummary,
)
from autonomous_trading_platform.research.intelligence.strategy_clustering_service import (
    StrategyCluster,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    INTELLIGENCE_CANDIDATE_RANKING_DATASET,
    INTELLIGENCE_CLUSTER_ASSIGNMENT_DATASET,
    INTELLIGENCE_REGIME_FINGERPRINT_DATASET,
)

logger = logging.getLogger(__name__)


class ResearchIntelligenceArtifactRepository:
    """
    Persists research intelligence artifacts to Parquet.

    Parameters
    ----------
    base_path
        Root directory for Parquet storage (default: "data").
    """

    def __init__(self, *, base_path: str | Path = "data") -> None:
        self._base = Path(base_path)

    def persist(self, summary: ResearchIntelligenceSummary) -> None:
        """Persist a single ResearchIntelligenceSummary."""
        self._persist_candidate_ranking(summary)
        if summary.regime_fingerprint is not None:
            self._persist_regime_fingerprint(summary)

    def persist_clusters(
        self,
        clusters: list[StrategyCluster],
        experiment_id: str,
        dataset_version: str,
        run_id: str,
    ) -> None:
        """Persist cluster assignment records."""
        rows: list[dict[str, Any]] = []
        today = datetime.now(UTC).date()
        for cluster in clusters:
            for member in cluster.members:
                rows.append(
                    {
                        "experiment_id": experiment_id,
                        "dataset_version": dataset_version,
                        "run_id": run_id,
                        "strategy_id": member.strategy_id,
                        "cluster_id": cluster.cluster_id,
                        "representative_strategy_id": cluster.representative_strategy_id,
                        "distance_to_centroid": member.distance_to_centroid,
                        "cluster_size": cluster.size,
                        "is_parameter_spam": cluster.is_parameter_spam,
                        "dominant_family": cluster.dominant_family or "",
                        "regime_bias": cluster.regime_bias or "",
                        "intra_cluster_variance": cluster.intra_cluster_variance,
                        "date": today,
                    }
                )
        if not rows:
            return
        df = pd.DataFrame(rows)
        self._write(df, INTELLIGENCE_CLUSTER_ASSIGNMENT_DATASET)
        logger.info("Persisted %d cluster assignment rows", len(rows))

    # ------------------------------------------------------------------

    def _persist_candidate_ranking(self, summary: ResearchIntelligenceSummary) -> None:
        cs = summary.candidate_score
        rs = summary.robustness_estimate
        oe = summary.overfit_estimate
        fv = summary.feature_vector
        today = datetime.now(UTC).date()

        row = {
            "experiment_id": summary.experiment_id,
            "strategy_id": summary.strategy_id,
            "dataset_version": summary.dataset_version,
            "run_id": summary.run_id,
            "computed_at": summary.computed_at,
            "rank": cs.rank,
            "composite_score": cs.composite_score,
            "deployability_score": cs.deployability_score,
            "is_deployable": cs.is_deployable,
            "data_completeness": fv.data_completeness,
            "config_hash": fv.config_hash,
            "robustness_probability": rs.robustness_probability,
            "fragility_score": rs.fragility_score,
            "robustness_confidence": rs.confidence,
            "deployment_suitability": rs.deployment_suitability.value,
            "overfit_probability": oe.overfit_probability,
            "overfit_risk_band": oe.risk_band.value,
            "cluster_id": summary.cluster_id or "",
            "weakness_count": len(cs.weakness_flags),
            "date": today,
        }

        df = pd.DataFrame([row])
        self._write(df, INTELLIGENCE_CANDIDATE_RANKING_DATASET)
        logger.debug(
            "Persisted candidate ranking for strategy=%s rank=%d",
            summary.strategy_id,
            cs.rank,
        )

    def _persist_regime_fingerprint(self, summary: ResearchIntelligenceSummary) -> None:
        fp = summary.regime_fingerprint
        assert fp is not None
        today = datetime.now(UTC).date()
        rows: list[dict[str, Any]] = []

        for dim, sensitivity in fp.sensitivity_by_dimension.items():
            label_sharpes = fp.sharpe_by_dim_label.get(dim, {})
            rows.append(
                {
                    "experiment_id": summary.experiment_id,
                    "strategy_id": summary.strategy_id,
                    "dataset_version": summary.dataset_version,
                    "run_id": summary.run_id,
                    "regime_dimension": dim,
                    "dimension_sensitivity": sensitivity,
                    "regime_diversity": fp.regime_diversity,
                    "overall_robustness": fp.overall_robustness,
                    "dominant_regime_dimension": fp.dominant_regime_dimension or "",
                    "is_regime_specialist": fp.is_regime_specialist,
                    "label_sharpe_json": str(
                        {
                            k: round(v, 4) if v is not None else None
                            for k, v in label_sharpes.items()
                        }
                    ),
                    "date": today,
                }
            )

        if rows:
            df = pd.DataFrame(rows)
            self._write(df, INTELLIGENCE_REGIME_FINGERPRINT_DATASET)

    def _write(self, df: pd.DataFrame, dataset: Any) -> None:
        root = self._base
        for part in dataset.root_parts:
            root = root / part

        partition_cols = list(dataset.partition_cols)
        for col in partition_cols:
            if col not in df.columns:
                continue
            vals = df[col].unique()
            for val in vals:
                part_df = df[df[col] == val].drop(columns=[col])
                part_dir = root / f"{col}={val}"
                part_dir.mkdir(parents=True, exist_ok=True)
                fname = part_dir / "part-0.parquet"
                table = pa.Table.from_pandas(part_df, preserve_index=False)
                pq.write_table(table, fname)
            return

        # No partition — write directly
        root.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, root / "part-0.parquet")
