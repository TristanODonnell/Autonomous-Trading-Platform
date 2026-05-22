from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.metric_labels import observability_environment
from autonomous_trading_platform.observability.metrics import (
    research_regime_analysis_duration,
    research_regime_dimension_coverage_pct,
)
from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)

from .regime_analysis_repository import RegimeAnalysisParquetRepository
from .regime_analysis_result import RegimeAnalysisResult
from .regime_bucket import REGIME_DIMENSIONS, RegimeBucket
from .regime_join_service import RegimeJoinService
from .regime_metrics import RegimeConditionedMetrics, compute_regime_metrics
from .regime_transition_analysis import RegimeTransitionAnalyzer
from .strategy_regime_profile import build_strategy_regime_profile

logger = logging.getLogger(__name__)

_COMPONENT = "research.regime_analysis_service"


@dataclass(slots=True)
class RegimeAnalysisRequest:
    equity_curve: pd.DataFrame
    trade_logs: pd.DataFrame
    regime_data: pd.DataFrame
    identity: SimulationArtifactIdentity
    per_bar_metrics: pd.DataFrame = field(default_factory=pd.DataFrame)
    analyze_transitions: bool = True
    transition_window_bars: int = 20
    bars_per_year: int = BARS_PER_YEAR


class RegimeAnalysisService:
    """
    Orchestrates regime-conditioned analysis of simulation outputs.

    Workflow:
    1. Join persisted regime labels onto equity curve and trade logs
    2. Compute bar returns from the portfolio equity curve
    3. For each (dimension, label) pair, compute regime-conditioned metrics
    4. Build per-dimension sensitivity scores and strategy regime profile
    5. Run transition analysis per dimension (optional)
    6. Persist regime analysis artifacts (optional)
    7. Return RegimeAnalysisResult
    """

    def __init__(
        self,
        *,
        join_service: RegimeJoinService | None = None,
        transition_analyzer: RegimeTransitionAnalyzer | None = None,
        repository: RegimeAnalysisParquetRepository | None = None,
    ) -> None:
        self.join_service = join_service or RegimeJoinService()
        self.transition_analyzer = transition_analyzer or RegimeTransitionAnalyzer()
        self.repository = repository

    def analyze(
        self,
        request: RegimeAnalysisRequest,
        *,
        persist: bool = True,
    ) -> RegimeAnalysisResult:
        _t0 = time.perf_counter()
        _env = observability_environment()
        _log_ctx = LogContext(
            component=_COMPONENT,
            experiment_id=request.identity.experiment_id,
            strategy_id=request.identity.strategy_id,
            dataset_version=request.identity.dataset_version,
            stage_name=request.identity.stage_name,
        )
        logger.info("regime_analysis_started", extra=_log_ctx.to_extra())

        equity_with_regime = self.join_service.join_equity_curve(
            equity_curve=request.equity_curve,
            regime_data=request.regime_data,
        )

        trades_with_regime = self.join_service.join_symbol_frame(
            frame=request.trade_logs,
            regime_data=request.regime_data,
        )

        bar_returns_with_regime = self._compute_bar_returns_with_regime(equity_with_regime)
        total_bars = len(bar_returns_with_regime)

        all_metrics: list[RegimeConditionedMetrics] = []
        metrics_by_dim: dict[str, dict[str, RegimeConditionedMetrics]] = {}

        for dimension in REGIME_DIMENSIONS:
            col = f"regime_{dimension}"
            metrics_by_label: dict[str, RegimeConditionedMetrics] = {}
            all_buckets = RegimeBucket.all_for_dimension(dimension)
            non_empty_buckets = 0

            for bucket in all_buckets:
                bar_returns, bar_count = self._filter_returns(
                    bar_returns_with_regime, col, bucket.label
                )
                regime_trades = self._filter_trades(trades_with_regime, col, bucket.label)

                m = compute_regime_metrics(
                    bucket=bucket,
                    bar_returns=bar_returns,
                    trades=regime_trades,
                    bar_count=bar_count,
                    total_bars=total_bars,
                    bars_per_year=request.bars_per_year,
                )
                metrics_by_label[bucket.label] = m
                all_metrics.append(m)
                if bar_count > 0:
                    non_empty_buckets += 1

            metrics_by_dim[dimension] = metrics_by_label

            coverage_pct = non_empty_buckets / len(all_buckets) if all_buckets else 0.0
            research_regime_dimension_coverage_pct.record(
                coverage_pct,
                {"environment": _env, "dimension": dimension},
            )
            if coverage_pct < 0.5:
                logger.warning(
                    "regime_dimension_sparse",
                    extra={
                        **_log_ctx.to_extra(),
                        "dimension": dimension,
                        "coverage_pct": round(coverage_pct, 3),
                        "non_empty_buckets": non_empty_buckets,
                        "total_buckets": len(all_buckets),
                    },
                )

        profile = build_strategy_regime_profile(
            strategy_id=request.identity.strategy_id,
            run_id=request.identity.run_id,
            experiment_id=request.identity.experiment_id,
            metrics_by_dim=metrics_by_dim,
        )

        transition_analyses = {}
        if request.analyze_transitions:
            for dimension in REGIME_DIMENSIONS:
                col = f"regime_{dimension}"
                if col in equity_with_regime.columns and not equity_with_regime.empty:
                    transition_analyses[dimension] = self.transition_analyzer.analyze(
                        equity_curve_with_regime=equity_with_regime,
                        bar_returns_with_regime=bar_returns_with_regime,
                        dimension=dimension,
                        window_bars=request.transition_window_bars,
                    )

        result = RegimeAnalysisResult(
            run_id=request.identity.run_id,
            experiment_id=request.identity.experiment_id,
            strategy_id=request.identity.strategy_id,
            dataset_version=request.identity.dataset_version,
            stage_name=request.identity.stage_name,
            window_role=request.identity.window_role,
            profile=profile,
            transition_analyses=transition_analyses,
            all_regime_metrics=all_metrics,
        )

        if persist and self.repository is not None:
            self.repository.persist(result, identity=request.identity)

        _duration = time.perf_counter() - _t0
        research_regime_analysis_duration.record(_duration, {"environment": _env})
        logger.info(
            "regime_analysis_completed",
            extra={
                **_log_ctx.to_extra(),
                "duration_seconds": round(_duration, 3),
                "total_bars": total_bars,
                "dimensions_analyzed": len(REGIME_DIMENSIONS),
            },
        )

        return result

    @staticmethod
    def _compute_bar_returns_with_regime(
        equity_with_regime: pd.DataFrame,
    ) -> pd.DataFrame:
        if equity_with_regime.empty or "equity" not in equity_with_regime.columns:
            return pd.DataFrame(columns=["timestamp", "bar_return"])

        regime_cols = [c for c in equity_with_regime.columns if c.startswith("regime_")]
        sorted_eq = equity_with_regime.sort_values("timestamp").reset_index(drop=True)

        equities = sorted_eq["equity"].to_numpy(dtype=np.float64)

        if len(equities) < 2:
            result = sorted_eq[["timestamp"] + regime_cols].copy()
            result["bar_return"] = np.nan
            return result

        bar_returns = np.diff(equities) / equities[:-1]

        # bar_return at index i corresponds to sorted_eq row i+1 (the bar that generated it)
        result = sorted_eq.iloc[1:][["timestamp"] + regime_cols].copy().reset_index(drop=True)
        result["bar_return"] = bar_returns
        return result

    @staticmethod
    def _filter_returns(
        bar_returns_with_regime: pd.DataFrame,
        col: str,
        label: str,
    ) -> tuple[np.ndarray, int]:
        if col not in bar_returns_with_regime.columns or bar_returns_with_regime.empty:
            return np.array([], dtype=np.float64), 0

        mask = bar_returns_with_regime[col] == label
        bar_count = int(mask.sum())
        returns = (
            bar_returns_with_regime.loc[mask, "bar_return"].dropna().to_numpy(dtype=np.float64)
        )
        return returns, bar_count

    @staticmethod
    def _filter_trades(
        trades_with_regime: pd.DataFrame,
        col: str,
        label: str,
    ) -> pd.DataFrame:
        if trades_with_regime.empty or col not in trades_with_regime.columns:
            return pd.DataFrame()
        return trades_with_regime[trades_with_regime[col] == label]
