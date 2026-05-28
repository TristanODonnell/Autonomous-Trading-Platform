from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
    compute_alpha,
)
from autonomous_trading_platform.contracts.runtime.blended_metrics_summary import (
    BlendedMetricsSummary,
)
from autonomous_trading_platform.contracts.runtime.live_metrics_summary import LiveMetricsSummary
from autonomous_trading_platform.contracts.runtime.metric_lineage import (
    MetricLineageMetadata,
    MetricLineageType,
)
from autonomous_trading_platform.contracts.runtime.research_metrics_summary import (
    ResearchMetricsSummary,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ratp_blended_metrics_computed_total,
    ratp_blended_strategy_score,
    ratp_live_metric_weight_alpha,
    ratp_live_metric_weight_updated_total,
    ratp_metric_lineage_resolved_total,
    ratp_research_metrics_resolved_total,
)
from autonomous_trading_platform.storage.sor.models.blended_metrics_snapshots import (
    BlendedMetricsSnapshot,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.repositories.core.blended_metrics_repository import (
    BlendedMetricsRepository,
)

logger = get_logger(__name__)

_CALCULATION_VERSION = "1.0"

# Weights for quality score components (mirrors QualityBasedReallocationService).
_W_SHARPE = 0.40
_W_RETURN = 1.50
_W_WIN_RATE = 0.40
_W_TRADE_COUNT = 0.25
_W_DRAWDOWN = 2.00
_TRADE_COUNT_SCALE = 100.0
_MIN_SCORE = 0.01


def _research_quality_score(metrics: ResearchMetricsSummary) -> float:
    score = 1.0
    if metrics.sharpe_ratio is not None:
        score += _W_SHARPE * max(metrics.sharpe_ratio, -1.0)
    if metrics.total_return is not None:
        score += _W_RETURN * metrics.total_return
    if metrics.win_rate is not None:
        score += _W_WIN_RATE * metrics.win_rate
    if metrics.trade_count is not None:
        score += _W_TRADE_COUNT * min(metrics.trade_count / _TRADE_COUNT_SCALE, _W_TRADE_COUNT)
    if metrics.max_drawdown is not None:
        score -= _W_DRAWDOWN * abs(metrics.max_drawdown)
    return max(score, _MIN_SCORE)


def _live_quality_score(metrics: LiveMetricsSummary) -> float:
    score = 1.0
    if metrics.rolling_sharpe is not None:
        score += _W_SHARPE * max(metrics.rolling_sharpe, -1.0)
    if metrics.realized_return is not None:
        score += _W_RETURN * metrics.realized_return
    if metrics.live_win_rate is not None:
        score += _W_WIN_RATE * metrics.live_win_rate
    if metrics.trade_count is not None:
        score += _W_TRADE_COUNT * min(metrics.trade_count / _TRADE_COUNT_SCALE, _W_TRADE_COUNT)
    if metrics.realized_drawdown is not None:
        score -= _W_DRAWDOWN * abs(metrics.realized_drawdown)
    return max(score, _MIN_SCORE)


class MetricLineageService:
    """Resolves, computes, and persists explicitly lineage-tagged metric summaries.

    This service is the single authority for answering:
      - "Where did this metric come from?" (research vs live vs blended)
      - "What was the blending weight at the time of allocation?"
      - "Has live confidence grown enough to trust live metrics more than backtest?"

    Research metrics are sourced from MetricsSummary rows (tied to simulation runs).
    Live metrics are computed by LivePerformanceMetricsService from realized fills.
    Blended metrics are a weighted combination, persisted to blended_metrics_snapshots.
    """

    def __init__(
        self,
        session: Session,
        *,
        live_perf_service: LivePerformanceMetricsService | None = None,
        blended_repo: BlendedMetricsRepository | None = None,
        environment: str | None = None,
    ) -> None:
        self._session = session
        self._live_perf_service = live_perf_service or LivePerformanceMetricsService(
            session, environment=environment
        )
        self._blended_repo = blended_repo or BlendedMetricsRepository(session)
        self._environment = environment or os.getenv("APP_ENV")

    # ------------------------------------------------------------------
    # Research metric resolution
    # ------------------------------------------------------------------

    def resolve_research_metrics(
        self,
        strategy_id: str,
        *,
        run_id: str | None = None,
    ) -> ResearchMetricsSummary | None:
        """Resolve the most relevant research metrics for a strategy.

        If run_id is provided, fetches that specific simulation's MetricsSummary.
        Otherwise falls back to the latest MetricsSummary linked to a simulation
        run for this strategy_id (via metrics_json fallback).
        """
        now = datetime.now(UTC)
        row = self._find_metrics_row(strategy_id=strategy_id, run_id=run_id)
        if row is None:
            return None

        attrs = {"strategy_id": strategy_id}
        ratp_research_metrics_resolved_total.add(1, attrs)
        ratp_metric_lineage_resolved_total.add(
            1, {"lineage_type": MetricLineageType.RESEARCH.value}
        )

        logger.info(
            "RESEARCH_METRICS_RESOLVED",
            extra={
                "strategy_id": strategy_id,
                "run_id": row.run_id,
                "metrics_snapshot_id": row.metrics_snapshot_id,
                "metric_lineage_type": MetricLineageType.RESEARCH.value,
                "environment": self._environment,
            },
        )

        return ResearchMetricsSummary(
            metrics_snapshot_id=row.metrics_snapshot_id,
            strategy_id=strategy_id,
            run_id=row.run_id,
            created_at=row.created_at,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.RESEARCH,
                environment=self._environment,
                calculation_version=_CALCULATION_VERSION,
                generated_at=now,
                source_run_ids=[row.run_id],
                source_strategy_ids=[strategy_id],
            ),
            total_return=row.total_return,
            sharpe_ratio=row.sharpe_ratio,
            max_drawdown=row.max_drawdown,
            trade_count=row.trade_count,
            winning_trade_count=row.winning_trade_count,
            losing_trade_count=row.losing_trade_count,
            volatility=row.volatility,
            metrics_json=row.metrics_json,
        )

    # ------------------------------------------------------------------
    # Live metric resolution
    # ------------------------------------------------------------------

    def resolve_live_metrics(
        self,
        strategy_id: str,
        *,
        run_id: str | None = None,
        window_days: int = LivePerformanceMetricsService.DEFAULT_WINDOW_DAYS,
        window_trades: int = LivePerformanceMetricsService.DEFAULT_WINDOW_TRADES,
        now: datetime | None = None,
    ) -> LiveMetricsSummary:
        """Compute live metrics from realized fills and return as a LiveMetricsSummary."""
        if now is None:
            now = datetime.now(UTC)

        raw = self._live_perf_service.compute_for_strategy(
            strategy_id,
            run_id=run_id,
            window_days=window_days,
            window_trades=window_trades,
            now=now,
        )

        ratp_metric_lineage_resolved_total.add(1, {"lineage_type": MetricLineageType.LIVE.value})

        return LiveMetricsSummary(
            snapshot_id=raw.snapshot_id,
            strategy_id=strategy_id,
            run_id=raw.run_id,
            computed_at=raw.computed_at,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.LIVE,
                environment=self._environment,
                calculation_version=_CALCULATION_VERSION,
                generated_at=now,
                lookback_window_days=window_days,
                source_strategy_ids=[strategy_id],
            ),
            window_days=raw.window_days,
            window_trades=raw.window_trades,
            realized_return=raw.realized_return,
            rolling_sharpe=raw.rolling_sharpe,
            realized_drawdown=raw.realized_drawdown,
            realized_volatility=raw.realized_volatility,
            live_win_rate=raw.live_win_rate,
            trade_count=raw.trade_count,
            winning_trade_count=raw.winning_trade_count,
            days_live=raw.days_live,
            days_since_profitable_day=raw.days_since_profitable_day,
        )

    # ------------------------------------------------------------------
    # Blended metric computation
    # ------------------------------------------------------------------

    def compute_blended_metrics(
        self,
        strategy_id: str,
        *,
        research: ResearchMetricsSummary | None = None,
        live: LiveMetricsSummary | None = None,
        rebalance_run_id: str | None = None,
        now: datetime | None = None,
    ) -> BlendedMetricsSummary:
        """Compute a confidence-weighted blend of research and live metrics.

        If research or live are not provided they are resolved automatically.
        alpha (live weight) increases as the strategy accumulates runtime history.
        """
        if now is None:
            now = datetime.now(UTC)

        if research is None:
            research = self.resolve_research_metrics(strategy_id)
        if live is None:
            live = self.resolve_live_metrics(strategy_id, now=now)

        days_live = live.days_live or 0
        trade_count = live.trade_count or 0
        alpha = compute_alpha(days_live=days_live, trade_count=trade_count)

        research_score = _research_quality_score(research) if research is not None else _MIN_SCORE
        live_score = _live_quality_score(live)
        blended_score = alpha * live_score + (1.0 - alpha) * research_score
        blended_score = max(blended_score, _MIN_SCORE)

        attrs = {"strategy_id": strategy_id}
        ratp_blended_metrics_computed_total.add(1, attrs)
        ratp_blended_strategy_score.record(blended_score, attrs)
        ratp_live_metric_weight_alpha.record(alpha, attrs)
        ratp_live_metric_weight_updated_total.add(1, attrs)
        ratp_metric_lineage_resolved_total.add(1, {"lineage_type": MetricLineageType.BLENDED.value})

        logger.info(
            "BLENDED_METRICS_COMPUTED",
            extra={
                "strategy_id": strategy_id,
                "alpha": alpha,
                "research_score": research_score,
                "live_score": live_score,
                "blended_score": blended_score,
                "days_live": days_live,
                "trade_count": trade_count,
                "rebalance_run_id": rebalance_run_id,
                "metric_lineage_type": MetricLineageType.BLENDED.value,
                "environment": self._environment,
            },
        )

        return BlendedMetricsSummary(
            blended_snapshot_id=str(uuid4()),
            strategy_id=strategy_id,
            computed_at=now,
            rebalance_run_id=rebalance_run_id,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.BLENDED,
                environment=self._environment,
                calculation_version=_CALCULATION_VERSION,
                generated_at=now,
                source_strategy_ids=[strategy_id],
                source_run_ids=[research.run_id] if research else [],
            ),
            research_metrics=research,
            live_metrics=live,
            alpha=alpha,
            days_live=days_live if days_live > 0 else None,
            trade_count=trade_count if trade_count > 0 else None,
            research_score=research_score,
            live_score=live_score,
            blended_score=blended_score,
            research_metrics_snapshot_id=research.metrics_snapshot_id if research else None,
            live_metrics_snapshot_id=live.snapshot_id,
        )

    def compute_and_persist_blended(
        self,
        strategy_id: str,
        *,
        research: ResearchMetricsSummary | None = None,
        live: LiveMetricsSummary | None = None,
        rebalance_run_id: str | None = None,
        now: datetime | None = None,
    ) -> BlendedMetricsSummary:
        """Compute blended metrics and persist a snapshot to the SOR."""
        summary = self.compute_blended_metrics(
            strategy_id,
            research=research,
            live=live,
            rebalance_run_id=rebalance_run_id,
            now=now,
        )
        self._blended_repo.insert(
            BlendedMetricsSnapshot(
                blended_snapshot_id=summary.blended_snapshot_id,
                strategy_id=summary.strategy_id,
                rebalance_run_id=summary.rebalance_run_id,
                computed_at=summary.computed_at,
                metric_lineage_type=MetricLineageType.BLENDED.value,
                environment=self._environment,
                calculation_version=_CALCULATION_VERSION,
                alpha_weight=summary.alpha,
                days_live=summary.days_live,
                trade_count=summary.trade_count,
                research_score=summary.research_score,
                live_score=summary.live_score,
                blended_score=summary.blended_score,
                research_metrics_snapshot_id=summary.research_metrics_snapshot_id,
                live_metrics_snapshot_id=summary.live_metrics_snapshot_id,
            )
        )
        return summary

    def get_latest_blended(self, strategy_id: str) -> BlendedMetricsSummary | None:
        """Return the most-recently persisted blended snapshot, or None."""
        row = self._blended_repo.get_latest(strategy_id)
        if row is None:
            return None
        return BlendedMetricsSummary(
            blended_snapshot_id=row.blended_snapshot_id,
            strategy_id=row.strategy_id,
            computed_at=row.computed_at,
            rebalance_run_id=row.rebalance_run_id,
            lineage=MetricLineageMetadata(
                metric_lineage_type=MetricLineageType.BLENDED,
                environment=row.environment,
                calculation_version=row.calculation_version or _CALCULATION_VERSION,
                generated_at=row.computed_at,
            ),
            alpha=row.alpha_weight,
            days_live=row.days_live,
            trade_count=row.trade_count,
            research_score=row.research_score,
            live_score=row.live_score,
            blended_score=row.blended_score,
            research_metrics_snapshot_id=row.research_metrics_snapshot_id,
            live_metrics_snapshot_id=row.live_metrics_snapshot_id,
        )

    def list_blended_history(
        self, strategy_id: str, *, limit: int = 50
    ) -> list[BlendedMetricsSummary]:
        """Return paginated blended snapshot history for a strategy."""
        rows = self._blended_repo.list_for_strategy(strategy_id, limit=limit)
        return [
            BlendedMetricsSummary(
                blended_snapshot_id=row.blended_snapshot_id,
                strategy_id=row.strategy_id,
                computed_at=row.computed_at,
                rebalance_run_id=row.rebalance_run_id,
                lineage=MetricLineageMetadata(
                    metric_lineage_type=MetricLineageType.BLENDED,
                    environment=row.environment,
                    calculation_version=row.calculation_version or _CALCULATION_VERSION,
                    generated_at=row.computed_at,
                ),
                alpha=row.alpha_weight,
                days_live=row.days_live,
                trade_count=row.trade_count,
                research_score=row.research_score,
                live_score=row.live_score,
                blended_score=row.blended_score,
                research_metrics_snapshot_id=row.research_metrics_snapshot_id,
                live_metrics_snapshot_id=row.live_metrics_snapshot_id,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_metrics_row(self, *, strategy_id: str, run_id: str | None) -> MetricsSummary | None:
        """Find a MetricsSummary row for the strategy, preferring the explicit run_id."""
        if run_id:
            row = cast(
                MetricsSummary | None,
                self._session.scalars(
                    select(MetricsSummary).where(MetricsSummary.run_id == run_id).limit(1)
                ).one_or_none(),
            )
            if row is not None:
                return row

        # Fallback: latest row whose metrics_json contains the strategy_id.
        rows = self._session.scalars(
            select(MetricsSummary).order_by(MetricsSummary.created_at.desc()).limit(200)
        ).all()
        for row in rows:
            json_blob = row.metrics_json or {}
            if (
                json_blob.get("strategy_id") == strategy_id
                or json_blob.get("strategy_ids", {}).get(strategy_id) is not None
            ):
                return cast(MetricsSummary, row)
        return None
