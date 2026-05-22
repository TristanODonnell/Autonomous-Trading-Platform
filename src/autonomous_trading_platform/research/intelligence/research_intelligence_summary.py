"""Research intelligence summary container (TASK-2.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomous_trading_platform.research.intelligence.candidate_ranking_service import (
    CandidateScore,
)
from autonomous_trading_platform.research.intelligence.overfitting_estimation_service import (
    OverfitEstimate,
)
from autonomous_trading_platform.research.intelligence.regime_similarity_analysis import (
    RegimeFingerprint,
)
from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    ResearchFeatureVector,
)
from autonomous_trading_platform.research.intelligence.robustness_prediction_service import (
    RobustnessEstimate,
)


@dataclass
class ResearchIntelligenceSummary:
    """
    Complete ML-assisted intelligence output for a single strategy candidate.

    Aggregates all intelligence services into a single, serialisable container.
    Cluster membership (cluster_id) is filled in later when batch clustering
    is performed across multiple candidates.
    """

    strategy_id: str
    experiment_id: str
    dataset_version: str
    run_id: str
    computed_at: datetime

    feature_vector: ResearchFeatureVector
    candidate_score: CandidateScore
    robustness_estimate: RobustnessEstimate
    overfit_estimate: OverfitEstimate
    regime_fingerprint: RegimeFingerprint | None

    cluster_id: str | None = None  # set after batch clustering

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "experiment_id": self.experiment_id,
            "dataset_version": self.dataset_version,
            "run_id": self.run_id,
            "computed_at": self.computed_at.isoformat(),
            "cluster_id": self.cluster_id,
            "candidate_score": self.candidate_score.as_dict(),
            "robustness_estimate": self.robustness_estimate.as_dict(),
            "overfit_estimate": self.overfit_estimate.as_dict(),
            "feature_vector": {
                "config_hash": self.feature_vector.config_hash,
                "data_completeness": round(self.feature_vector.data_completeness, 4),
                "fields": self.feature_vector.as_dict(),
            },
        }
        if self.regime_fingerprint is not None:
            d["regime_fingerprint"] = self.regime_fingerprint.as_dict()
        return d
