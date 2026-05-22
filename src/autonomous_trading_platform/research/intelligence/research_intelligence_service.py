"""
Research intelligence service — top-level orchestrator (TASK-2.5).

Accepts existing validation and regime artifacts and produces a
ResearchIntelligenceSummary for each strategy candidate.

Supports:
  - single-candidate analysis
  - batch ranking across multiple candidates
  - batch clustering across multiple candidates

This service does NOT:
  - run simulations
  - promote strategies
  - enable trading
  - make autonomous decisions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.metric_labels import observability_environment
from autonomous_trading_platform.observability.metrics import (
    research_intelligence_cluster_count,
    research_intelligence_overfit_estimate,
    research_intelligence_ranking_score,
)
from autonomous_trading_platform.research.intelligence.candidate_ranking_service import (
    CandidateRankingService,
    RankingWeights,
)
from autonomous_trading_platform.research.intelligence.overfitting_estimation_service import (
    OverfittingEstimationService,
)
from autonomous_trading_platform.research.intelligence.regime_similarity_analysis import (
    RegimeSimilarityAnalyzer,
)
from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    ResearchFeatureVectorBuilder,
)
from autonomous_trading_platform.research.intelligence.research_intelligence_summary import (
    ResearchIntelligenceSummary,
)
from autonomous_trading_platform.research.intelligence.robustness_prediction_service import (
    RobustnessPredictionService,
)
from autonomous_trading_platform.research.intelligence.strategy_clustering_service import (
    StrategyCluster,
    StrategyClusteringService,
)

logger = logging.getLogger(__name__)

_COMPONENT = "research.intelligence_service"


@dataclass
class ResearchIntelligenceRequest:
    """
    All inputs needed to produce a ResearchIntelligenceSummary.

    Required
    --------
    strategy_id, experiment_id, dataset_version, run_id

    Optional enrichment
    -------------------
    validation_summary  — ValidationSummary from TASK-2.4
    regime_profile      — StrategyRegimeProfile from TASK-2.3
    strategy_family     — StrategyFamily value string (e.g. "momentum")
    n_parameters        — number of tunable parameters
    persist             — whether to persist the summary artifact
    """

    strategy_id: str
    experiment_id: str
    dataset_version: str
    run_id: str

    validation_summary: Any | None = None  # ValidationSummary
    regime_profile: Any | None = None  # StrategyRegimeProfile
    strategy_family: str | None = None
    n_parameters: int | None = None
    persist: bool = True


class ResearchIntelligenceService:
    """
    Orchestrates the full ML-assisted research intelligence pipeline.

    Parameters
    ----------
    vector_builder
        ResearchFeatureVectorBuilder instance (default if None).
    ranking_service
        CandidateRankingService instance (default if None).
    clustering_service
        StrategyClusteringService instance (default if None).
    robustness_service
        RobustnessPredictionService instance (default if None).
    overfitting_service
        OverfittingEstimationService instance (default if None).
    regime_analyzer
        RegimeSimilarityAnalyzer instance (default if None).
    artifact_repository
        Optional ResearchIntelligenceArtifactRepository for persistence.
    """

    def __init__(
        self,
        *,
        vector_builder: ResearchFeatureVectorBuilder | None = None,
        ranking_service: CandidateRankingService | None = None,
        clustering_service: StrategyClusteringService | None = None,
        robustness_service: RobustnessPredictionService | None = None,
        overfitting_service: OverfittingEstimationService | None = None,
        regime_analyzer: RegimeSimilarityAnalyzer | None = None,
        artifact_repository: Any | None = None,
    ) -> None:
        self._vector_builder = vector_builder or ResearchFeatureVectorBuilder()
        self._ranking_service = ranking_service or CandidateRankingService()
        self._clustering_service = clustering_service or StrategyClusteringService()
        self._robustness_service = robustness_service or RobustnessPredictionService()
        self._overfitting_service = overfitting_service or OverfittingEstimationService()
        self._regime_analyzer = regime_analyzer or RegimeSimilarityAnalyzer()
        self._repo = artifact_repository

    def analyze(
        self,
        request: ResearchIntelligenceRequest,
    ) -> ResearchIntelligenceSummary:
        """Produce a full intelligence summary for a single strategy candidate."""
        _env = observability_environment()
        logger.info(
            "intelligence_analysis_started",
            extra=LogContext(
                component=_COMPONENT,
                experiment_id=request.experiment_id,
                strategy_id=request.strategy_id,
                dataset_version=request.dataset_version,
            ).to_extra(),
        )

        # Feature vector
        vector = self._vector_builder.build(
            strategy_id=request.strategy_id,
            experiment_id=request.experiment_id,
            dataset_version=request.dataset_version,
            validation_summary=request.validation_summary,
            regime_profile=request.regime_profile,
            strategy_family=request.strategy_family,
            n_parameters=request.n_parameters,
        )

        # Candidate score (single)
        candidate_score = self._ranking_service.score_single(vector)

        # Robustness estimate
        robustness_est = self._robustness_service.estimate(vector)

        # Overfitting estimate
        overfit_est = self._overfitting_service.estimate(vector)

        # Regime fingerprint (if regime profile available)
        regime_fp = None
        if request.regime_profile is not None:
            regime_fp = self._regime_analyzer.build_fingerprint(
                strategy_id=request.strategy_id,
                experiment_id=request.experiment_id,
                regime_profile=request.regime_profile,
            )

        summary = ResearchIntelligenceSummary(
            strategy_id=request.strategy_id,
            experiment_id=request.experiment_id,
            dataset_version=request.dataset_version,
            run_id=request.run_id,
            computed_at=datetime.now(UTC),
            feature_vector=vector,
            candidate_score=candidate_score,
            robustness_estimate=robustness_est,
            overfit_estimate=overfit_est,
            regime_fingerprint=regime_fp,
        )

        research_intelligence_ranking_score.record(
            candidate_score.composite_score, {"environment": _env}
        )
        research_intelligence_overfit_estimate.record(
            overfit_est.overfit_probability, {"environment": _env}
        )

        logger.info(
            "intelligence_analysis_completed",
            extra=LogContext(
                component=_COMPONENT,
                experiment_id=request.experiment_id,
                strategy_id=request.strategy_id,
                robustness_score=robustness_est.robustness_probability,
                overfit_probability=overfit_est.overfit_probability,
            ).to_extra(),
        )

        if request.persist and self._repo is not None:
            try:
                self._repo.persist(summary)
            except Exception as exc:
                logger.warning("Failed to persist intelligence artifacts: %s", exc)

        return summary

    def rank_candidates(
        self,
        summaries: list[ResearchIntelligenceSummary],
        weights: RankingWeights | None = None,
    ) -> list[tuple[int, ResearchIntelligenceSummary]]:
        """
        Re-rank a batch of summaries.

        Returns a list of (rank, summary) sorted by composite_score descending.
        Ranking uses the feature vectors already embedded in each summary.
        """
        if not summaries:
            return []
        vectors = [s.feature_vector for s in summaries]
        ranked_scores = self._ranking_service.rank(vectors, weights)

        # Build a lookup by strategy_id → summary
        summary_map = {s.strategy_id: s for s in summaries}

        result: list[tuple[int, ResearchIntelligenceSummary]] = []
        for score in ranked_scores:
            s = summary_map[score.strategy_id]
            # Update rank on the candidate_score (create new CandidateScore with rank)
            from autonomous_trading_platform.research.intelligence.candidate_ranking_service import (
                CandidateScore as CS,
            )

            updated_score = CS(
                strategy_id=score.strategy_id,
                experiment_id=score.experiment_id,
                rank=score.rank,
                composite_score=score.composite_score,
                components=score.components,
                weakness_flags=score.weakness_flags,
                deployability_score=score.deployability_score,
                is_deployable=score.is_deployable,
                explanation=score.explanation,
                data_completeness=score.data_completeness,
            )
            s.candidate_score = updated_score
            result.append((score.rank, s))
        return result

    def cluster_candidates(
        self,
        summaries: list[ResearchIntelligenceSummary],
    ) -> list[StrategyCluster]:
        """
        Cluster a batch of summaries by feature vector similarity.

        Updates each summary's cluster_id field in-place.
        Returns the list of StrategyCluster objects.
        """
        if not summaries:
            return []
        vectors = [s.feature_vector for s in summaries]
        clusters = self._clustering_service.cluster(vectors)

        # Assign cluster_id back to each summary
        for cluster in clusters:
            for member in cluster.members:
                for s in summaries:
                    if s.strategy_id == member.strategy_id:
                        s.cluster_id = cluster.cluster_id

        _env = observability_environment()
        research_intelligence_cluster_count.record(len(clusters), {"environment": _env})
        logger.info(
            "intelligence_clustering_completed",
            extra={
                **LogContext(component=_COMPONENT).to_extra(),
                "candidate_count": len(summaries),
                "cluster_count": len(clusters),
            },
        )
        return clusters

    def analyze_regime_diversity(
        self,
        summaries: list[ResearchIntelligenceSummary],
    ) -> dict[str, Any]:
        """
        Analyse regime diversity across a batch of strategy candidates.

        Returns a dict with:
          - regime_specialists: strategies strongly dominant in one regime
          - diversification_score: portfolio regime diversity [0, 1]
          - pairwise_similarities: (id1, id2) → similarity
        """
        fps = [s.regime_fingerprint for s in summaries if s.regime_fingerprint is not None]
        if not fps:
            return {"regime_specialists": {}, "diversification_score": 0.0}

        specialists = self._regime_analyzer.detect_regime_specialists(fps)
        div_score = self._regime_analyzer.regime_diversification_score(fps)
        similarities = self._regime_analyzer.pairwise_similarities(fps)

        return {
            "regime_specialists": specialists,
            "diversification_score": round(div_score, 4),
            "pairwise_similarities": {
                f"{k[0]}|{k[1]}": round(v, 4) for k, v in similarities.items()
            },
        }
