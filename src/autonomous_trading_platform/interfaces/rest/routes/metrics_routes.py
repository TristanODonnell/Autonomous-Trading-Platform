from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id, require_operator_or_admin
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.api.errors import APIError, ErrorCode
from autonomous_trading_platform.application.services.metric_lineage_service import (
    MetricLineageService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.metrics_schemas import (
    BlendedMetricsHistoryResponse,
    BlendedMetricsResponse,
    LiveMetricsResponse,
    MetricLineageMetadataResponse,
    MetricLineageSummaryResponse,
    ResearchMetricsResponse,
)

router = APIRouter(prefix="/metrics", tags=["metrics"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


def _lineage_meta(lineage) -> MetricLineageMetadataResponse:
    return MetricLineageMetadataResponse(
        metric_lineage_type=lineage.metric_lineage_type.value,
        environment=lineage.environment,
        calculation_version=lineage.calculation_version,
        generated_at=lineage.generated_at,
        lookback_window_days=lineage.lookback_window_days,
        source_run_ids=lineage.source_run_ids,
        source_strategy_ids=lineage.source_strategy_ids,
    )


def _blended_to_response(blended) -> BlendedMetricsResponse:
    return BlendedMetricsResponse(
        blended_snapshot_id=blended.blended_snapshot_id,
        strategy_id=blended.strategy_id,
        computed_at=blended.computed_at,
        rebalance_run_id=blended.rebalance_run_id,
        metric_lineage_type=blended.metric_lineage_type.value,
        lineage=_lineage_meta(blended.lineage),
        alpha=blended.alpha,
        days_live=blended.days_live,
        trade_count=blended.trade_count,
        research_score=blended.research_score,
        live_score=blended.live_score,
        blended_score=blended.blended_score,
        research_metrics_snapshot_id=blended.research_metrics_snapshot_id,
        live_metrics_snapshot_id=blended.live_metrics_snapshot_id,
    )


@router.get(
    "/lineage/{strategy_id}",
    response_model=SuccessEnvelope[MetricLineageSummaryResponse],
    summary="Get metric lineage summary for a strategy",
)
def get_metric_lineage_summary(
    strategy_id: str,
    run_id: Annotated[str | None, Query()] = None,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[MetricLineageSummaryResponse]:
    """Answer: is this metric from simulation or realized runtime?

    Returns the lineage state for a strategy — which metric domains are
    available, the current blending weight (alpha), and the blended score.
    Operators can use this to understand why a strategy is receiving a given
    allocation despite weak backtest or strong live performance.
    """
    svc = MetricLineageService(session)
    research = svc.resolve_research_metrics(strategy_id, run_id=run_id)
    live = svc.resolve_live_metrics(strategy_id)
    blended_row = svc.get_latest_blended(strategy_id)

    current_alpha: float | None = blended_row.alpha if blended_row else None
    current_blended_score = blended_row.blended_score if blended_row else None

    lineage_answer = (
        "Blended (research + live)"
        if blended_row
        else "Live only"
        if (live.trade_count or 0) > 0
        else "Research only"
        if research
        else "No metrics available"
    )

    return success_response(
        data=MetricLineageSummaryResponse(
            strategy_id=strategy_id,
            has_research_metrics=research is not None,
            has_live_metrics=(live.trade_count or 0) > 0 or live.realized_return is not None,
            has_blended_metrics=blended_row is not None,
            current_alpha=current_alpha,
            current_blended_score=current_blended_score,
            research_metric_lineage_type=research.metric_lineage_type.value if research else None,
            live_metric_lineage_type=live.metric_lineage_type.value,
            metric_lineage_type_answer=lineage_answer,
        ),
        request_id=request_id,
    )


@router.get(
    "/research/{strategy_id}",
    response_model=SuccessEnvelope[ResearchMetricsResponse],
    summary="Get research (simulation) metrics for a strategy",
)
def get_research_metrics(
    strategy_id: str,
    run_id: Annotated[str | None, Query()] = None,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[ResearchMetricsResponse]:
    """Return research/simulation metrics for a strategy.

    These are sourced exclusively from backtests and simulations.
    Used for promotion evaluation and offline strategy ranking.
    """
    svc = MetricLineageService(session)
    research = svc.resolve_research_metrics(strategy_id, run_id=run_id)
    if research is None:
        raise APIError(
            code=ErrorCode.NOT_FOUND,
            message=f"No research metrics found for strategy {strategy_id!r}",
            http_status=404,
        )
    return success_response(
        data=ResearchMetricsResponse(
            metrics_snapshot_id=research.metrics_snapshot_id,
            strategy_id=research.strategy_id,
            run_id=research.run_id,
            created_at=research.created_at,
            metric_lineage_type=research.metric_lineage_type.value,
            lineage=_lineage_meta(research.lineage),
            total_return=research.total_return,
            sharpe_ratio=research.sharpe_ratio,
            max_drawdown=research.max_drawdown,
            trade_count=research.trade_count,
            winning_trade_count=research.winning_trade_count,
            losing_trade_count=research.losing_trade_count,
            volatility=research.volatility,
            win_rate=research.win_rate,
            metrics_json=research.metrics_json,
        ),
        request_id=request_id,
    )


@router.get(
    "/live/{strategy_id}",
    response_model=SuccessEnvelope[LiveMetricsResponse],
    summary="Get live (realized runtime) metrics for a strategy",
)
def get_live_metrics(
    strategy_id: str,
    window_days: Annotated[int, Query(ge=1, le=365)] = 20,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[LiveMetricsResponse]:
    """Return live metrics computed from realized fills and runtime equity curve.

    Used for health monitoring, degradation detection, and adaptive allocation.
    """
    svc = MetricLineageService(session)
    live = svc.resolve_live_metrics(strategy_id, window_days=window_days)
    return success_response(
        data=LiveMetricsResponse(
            snapshot_id=live.snapshot_id,
            strategy_id=live.strategy_id,
            run_id=live.run_id,
            computed_at=live.computed_at,
            metric_lineage_type=live.metric_lineage_type.value,
            lineage=_lineage_meta(live.lineage),
            window_days=live.window_days,
            window_trades=live.window_trades,
            realized_return=live.realized_return,
            rolling_sharpe=live.rolling_sharpe,
            realized_drawdown=live.realized_drawdown,
            realized_volatility=live.realized_volatility,
            live_win_rate=live.live_win_rate,
            trade_count=live.trade_count,
            winning_trade_count=live.winning_trade_count,
            days_live=live.days_live,
            days_since_profitable_day=live.days_since_profitable_day,
        ),
        request_id=request_id,
    )


@router.post(
    "/blended/{strategy_id}",
    response_model=SuccessEnvelope[BlendedMetricsResponse],
    status_code=201,
    summary="Compute and persist a blended metric snapshot for a strategy",
)
def compute_blended_metrics(
    strategy_id: str,
    run_id: Annotated[str | None, Query()] = None,
    rebalance_run_id: Annotated[str | None, Query()] = None,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[BlendedMetricsResponse]:
    """Compute and persist a blended metric snapshot.

    Blends research and live metrics using the confidence-adaptive alpha weight.
    Alpha increases as the strategy accumulates live history and fills.
    """
    svc = MetricLineageService(session)
    research = svc.resolve_research_metrics(strategy_id, run_id=run_id)
    blended = svc.compute_and_persist_blended(
        strategy_id,
        research=research,
        rebalance_run_id=rebalance_run_id,
    )
    session.commit()
    return success_response(
        data=_blended_to_response(blended),
        request_id=request_id,
    )


@router.get(
    "/blended/{strategy_id}",
    response_model=SuccessEnvelope[BlendedMetricsResponse],
    summary="Get the latest blended metric snapshot for a strategy",
)
def get_latest_blended_metrics(
    strategy_id: str,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[BlendedMetricsResponse]:
    """Return the most recently persisted blended metrics for a strategy."""
    svc = MetricLineageService(session)
    blended = svc.get_latest_blended(strategy_id)
    if blended is None:
        raise APIError(
            code=ErrorCode.NOT_FOUND,
            message=f"No blended metrics found for strategy {strategy_id!r}",
            http_status=404,
        )
    return success_response(data=_blended_to_response(blended), request_id=request_id)


@router.get(
    "/blended/{strategy_id}/history",
    response_model=SuccessEnvelope[BlendedMetricsHistoryResponse],
    summary="Get blended metric history for a strategy",
)
def get_blended_metrics_history(
    strategy_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[BlendedMetricsHistoryResponse]:
    """Return paginated blended metric history.

    Useful for analyzing how the blending alpha evolved over time as the
    strategy accumulated live trading evidence.
    """
    svc = MetricLineageService(session)
    history = svc.list_blended_history(strategy_id, limit=limit)
    return success_response(
        data=BlendedMetricsHistoryResponse(
            strategy_id=strategy_id,
            snapshots=[_blended_to_response(b) for b in history],
            total=len(history),
        ),
        request_id=request_id,
    )
