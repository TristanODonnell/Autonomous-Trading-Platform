from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomous_trading_platform.research.analysis.regimes.regime_metrics import (
    RegimeConditionedMetrics,
)
from autonomous_trading_platform.research.analysis.regimes.regime_transition_analysis import (
    RegimeTransitionAnalysis,
)
from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
    StrategyRegimeProfile,
)


@dataclass(frozen=True)
class RegimeAnalysisResult:
    run_id: str
    experiment_id: str
    strategy_id: str
    dataset_version: str
    stage_name: str
    window_role: str
    profile: StrategyRegimeProfile
    transition_analyses: dict[str, RegimeTransitionAnalysis]
    all_regime_metrics: list[RegimeConditionedMetrics]
    component_regime_analysis: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        """Human-readable summary dict suitable for CLI output."""
        best_by_dim: dict[str, str | None] = {}
        worst_by_dim: dict[str, str | None] = {}
        for dim, summary in {
            "trend": self.profile.by_trend,
            "volatility": self.profile.by_volatility,
            "liquidity": self.profile.by_liquidity,
            "mean_reversion": self.profile.by_mean_reversion,
            "risk": self.profile.by_risk,
        }.items():
            best_by_dim[dim] = summary.sensitivity.best_regime
            worst_by_dim[dim] = summary.sensitivity.worst_regime

        transition_counts = {dim: a.transition_count for dim, a in self.transition_analyses.items()}

        return {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "overall_sensitivity": self.profile.overall_sensitivity,
            "is_regime_robust": self.profile.is_regime_robust,
            "best_regime_by_dimension": best_by_dim,
            "worst_regime_by_dimension": worst_by_dim,
            "transition_counts": transition_counts,
            "total_regime_metric_rows": len(self.all_regime_metrics),
        }
