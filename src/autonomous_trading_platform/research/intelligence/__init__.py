"""ML-assisted research intelligence layer (TASK-2.5).

Consumes validation/regime/research artifacts to produce:
- candidate ranking
- strategy clustering
- robustness estimation
- overfitting probability estimation
- regime-aware research prioritisation

All outputs are deterministic, explainable, and offline.
"""

from autonomous_trading_platform.research.intelligence.candidate_ranking_service import (
    CandidateRankingService,
    CandidateScore,
    RankingComponent,
    RankingWeights,
)
from autonomous_trading_platform.research.intelligence.overfitting_estimation_service import (
    OverfitEstimate,
    OverfitRiskBand,
    OverfittingEstimationService,
)
from autonomous_trading_platform.research.intelligence.regime_similarity_analysis import (
    RegimeFingerprint,
    RegimeSimilarityAnalyzer,
)
from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    FeatureField,
    ResearchFeatureVector,
    ResearchFeatureVectorBuilder,
)
from autonomous_trading_platform.research.intelligence.research_intelligence_service import (
    ResearchIntelligenceRequest,
    ResearchIntelligenceService,
)
from autonomous_trading_platform.research.intelligence.research_intelligence_summary import (
    ResearchIntelligenceSummary,
)
from autonomous_trading_platform.research.intelligence.robustness_prediction_service import (
    DeploymentSuitability,
    RobustnessEstimate,
    RobustnessPredictionService,
)
from autonomous_trading_platform.research.intelligence.strategy_clustering_service import (
    ClusterMember,
    StrategyCluster,
    StrategyClusteringService,
)

__all__ = [
    "CandidateRankingService",
    "CandidateScore",
    "ClusterMember",
    "DeploymentSuitability",
    "FeatureField",
    "OverfitEstimate",
    "OverfitRiskBand",
    "OverfittingEstimationService",
    "RankingComponent",
    "RankingWeights",
    "RegimeFingerprint",
    "RegimeSimilarityAnalyzer",
    "ResearchFeatureVector",
    "ResearchFeatureVectorBuilder",
    "ResearchIntelligenceRequest",
    "ResearchIntelligenceService",
    "ResearchIntelligenceSummary",
    "RobustnessEstimate",
    "RobustnessPredictionService",
    "StrategyCluster",
    "StrategyClusteringService",
]
