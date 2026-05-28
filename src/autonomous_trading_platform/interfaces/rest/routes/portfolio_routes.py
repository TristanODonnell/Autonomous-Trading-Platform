from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_alpaca_broker_client,
    get_request_id,
    get_settings,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.alpaca_portfolio_service import (
    AlpacaPortfolioService,
)
from autonomous_trading_platform.application.services.portfolio_analytics_service import (
    PortfolioAnalyticsService,
)
from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
    PortfolioEquityCurveService,
)
from autonomous_trading_platform.application.services.portfolio_summary_service import (
    PortfolioSummaryService,
)
from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.interfaces.rest.schemas.portfolio_schemas import (
    FactorExposureHistoryResponse,
    FactorExposureSnapshotResponse,
    PortfolioAllocationResponse,
    PortfolioEquityCurvePeriod,
    PortfolioEquityCurveResponse,
    PortfolioHoldingsResponse,
    PortfolioPerformanceByPeriodResponse,
    PortfolioPerformanceResponse,
    PortfolioRiskResponse,
    PortfolioSummaryResponse,
    StrategyFactorExposureHistoryResponse,
)
from autonomous_trading_platform.storage.sor.repositories.core.factor_exposure_snapshot_repository import (
    FactorExposureSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)
_alpaca_dependency = Depends(get_alpaca_broker_client)


def _alpaca_service(
    client: AlpacaBrokerClient | None,
    session: Session,
) -> AlpacaPortfolioService | None:
    if client is None:
        return None
    # In simulation/backtesting mode always read from DB so the frontend
    # reflects backtest results rather than the live broker account.
    try:
        ctrl = RuntimeControlStateRepository(session).get_global_state()
        if ctrl is not None and ctrl.trading_mode == "simulation":
            return None
    except Exception:
        pass
    try:
        settings = get_settings()
        return AlpacaPortfolioService(client=client, initial_capital=settings.initial_capital)
    except Exception:
        return None


@router.get("/summary", response_model=SuccessEnvelope[PortfolioSummaryResponse])
def get_portfolio_summary(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    alpaca_client: AlpacaBrokerClient | None = _alpaca_dependency,
) -> SuccessEnvelope[PortfolioSummaryResponse]:
    alpaca = _alpaca_service(alpaca_client, session)
    if alpaca is not None:
        try:
            result = alpaca.get_summary()
            return success_response(
                data=PortfolioSummaryResponse(**result),
                request_id=request_id,
            )
        except Exception:
            pass

    service = PortfolioSummaryService(session=session)
    result = service.get_summary()
    return success_response(
        data=PortfolioSummaryResponse(**result),
        request_id=request_id,
    )


@router.get("/equity-curve", response_model=SuccessEnvelope[PortfolioEquityCurveResponse])
def get_portfolio_equity_curve(
    period: PortfolioEquityCurvePeriod,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[PortfolioEquityCurveResponse]:
    service = PortfolioEquityCurveService(session=session)
    result = service.get_equity_curve(period=period)

    return success_response(
        data=PortfolioEquityCurveResponse(**result),
        request_id=request_id,
    )


@router.get("/performance", response_model=SuccessEnvelope[PortfolioPerformanceResponse])
def get_portfolio_performance(
    from_date: Annotated[date | None, Query()] = None,
    to_date: Annotated[date | None, Query()] = None,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[PortfolioPerformanceResponse]:
    service = PortfolioAnalyticsService(session=session)
    result = service.get_performance(from_date=from_date, to_date=to_date)

    return success_response(
        data=PortfolioPerformanceResponse(**result),
        request_id=request_id,
    )


@router.get("/holdings", response_model=SuccessEnvelope[PortfolioHoldingsResponse])
def get_portfolio_holdings(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    alpaca_client: AlpacaBrokerClient | None = _alpaca_dependency,
) -> SuccessEnvelope[PortfolioHoldingsResponse]:
    alpaca = _alpaca_service(alpaca_client, session)
    if alpaca is not None:
        try:
            result = alpaca.get_holdings()
            return success_response(
                data=PortfolioHoldingsResponse(**result),
                request_id=request_id,
            )
        except Exception:
            pass

    service = PortfolioAnalyticsService(session=session)
    result = service.get_holdings()
    return success_response(
        data=PortfolioHoldingsResponse(**result),
        request_id=request_id,
    )


@router.get("/allocation", response_model=SuccessEnvelope[PortfolioAllocationResponse])
def get_portfolio_allocation(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    alpaca_client: AlpacaBrokerClient | None = _alpaca_dependency,
) -> SuccessEnvelope[PortfolioAllocationResponse]:
    alpaca = _alpaca_service(alpaca_client, session)
    if alpaca is not None:
        try:
            result = alpaca.get_allocation()
            if result["by_strategy"]:
                return success_response(
                    data=PortfolioAllocationResponse(**result),
                    request_id=request_id,
                )
        except Exception:
            pass

    service = PortfolioAnalyticsService(session=session)
    result = service.get_allocation()

    # No live positions — fall back to configured strategy allocation percentages
    # so the chart shows what operators have set, not just an empty state.
    if not result["by_strategy"]:
        result = _allocation_from_strategy_config(session=session)

    return success_response(
        data=PortfolioAllocationResponse(**result),
        request_id=request_id,
    )


def _allocation_from_strategy_config(*, session: Session) -> dict:
    from decimal import Decimal

    rows = StrategyAllocationService(session=session).get_allocations_for_active_strategies()
    by_strategy = [
        {
            "name": row["display_name"],
            "allocated_capital": row["allocated_capital"] or Decimal("0"),
            "percent_of_portfolio": row["allocation_pct"] or Decimal("0"),
        }
        for row in rows
    ]
    return {"by_strategy": by_strategy, "by_asset": []}


@router.get("/risk", response_model=SuccessEnvelope[PortfolioRiskResponse])
def get_portfolio_risk(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[PortfolioRiskResponse]:
    service = PortfolioAnalyticsService(session=session)
    result = service.get_risk()

    return success_response(
        data=PortfolioRiskResponse(**result),
        request_id=request_id,
    )


@router.get(
    "/performance/by-period",
    response_model=SuccessEnvelope[PortfolioPerformanceByPeriodResponse],
)
def get_portfolio_performance_by_period(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[PortfolioPerformanceByPeriodResponse]:
    service = PortfolioAnalyticsService(session=session)
    result = service.get_performance_by_period()

    return success_response(
        data=PortfolioPerformanceByPeriodResponse(**result),
        request_id=request_id,
    )


@router.get(
    "/factor-exposures/current",
    response_model=SuccessEnvelope[FactorExposureSnapshotResponse],
)
def get_current_factor_exposures(
    portfolio_id: Annotated[str | None, Query()] = None,
    lookback_window: Annotated[int | None, Query()] = None,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[FactorExposureSnapshotResponse]:
    repo = FactorExposureSnapshotRepository(session)
    row = repo.get_latest_snapshot(portfolio_id=portfolio_id, lookback_window=lookback_window)
    if row is None:
        empty = FactorExposureSnapshotResponse(
            snapshot_id="",
            run_id=None,
            portfolio_id=portfolio_id,
            computed_at=datetime.now(UTC),
            as_of_date=datetime.now(UTC),
            lookback_window=lookback_window or 0,
            benchmark_symbol="",
            benchmark_source="",
            factor_computation_version="",
            portfolio_exposures={},
            strategy_exposures=[],
            symbol_exposures=[],
            sector_exposures={},
            concentration_diagnostics=[],
            warnings=["No factor exposure snapshot available"],
            factor_methodology={},
            data_lineage={},
            duration_seconds=0.0,
        )
        return success_response(data=empty, request_id=request_id)
    return success_response(data=_factor_snapshot_response(row), request_id=request_id)


@router.get(
    "/factor-exposures/history",
    response_model=SuccessEnvelope[FactorExposureHistoryResponse],
)
def get_factor_exposure_history(
    portfolio_id: Annotated[str | None, Query()] = None,
    lookback_window: Annotated[int | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=3650)] = 30,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[FactorExposureHistoryResponse]:
    repo = FactorExposureSnapshotRepository(session)
    rows = repo.get_snapshot_history(
        since=datetime.now(UTC) - timedelta(days=days),
        portfolio_id=portfolio_id,
        lookback_window=lookback_window,
        limit=limit,
    )
    return success_response(
        data=FactorExposureHistoryResponse(
            snapshots=[_factor_snapshot_response(row) for row in rows]
        ),
        request_id=request_id,
    )


@router.get(
    "/factor-exposures/strategies/{strategy_id}",
    response_model=SuccessEnvelope[StrategyFactorExposureHistoryResponse],
)
def get_strategy_factor_decomposition(
    strategy_id: str,
    lookback_window: Annotated[int | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=3650)] = 30,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[StrategyFactorExposureHistoryResponse]:
    repo = FactorExposureSnapshotRepository(session)
    rows = repo.get_strategy_history(
        strategy_id=strategy_id,
        since=datetime.now(UTC) - timedelta(days=days),
        lookback_window=lookback_window,
        limit=limit,
    )
    return success_response(
        data=StrategyFactorExposureHistoryResponse(
            exposures=[
                {
                    "exposure_id": row.exposure_id,
                    "snapshot_id": row.snapshot_id,
                    "run_id": row.run_id,
                    "portfolio_id": row.portfolio_id,
                    "strategy_id": row.strategy_id,
                    "computed_at": row.computed_at,
                    "as_of_date": row.as_of_date,
                    "lookback_window": row.lookback_window,
                    "benchmark_symbol": row.benchmark_symbol,
                    "strategy_weight": row.strategy_weight,
                    "symbol_count": row.symbol_count,
                    "exposures": row.exposures,
                    "top_symbol_contributors": row.top_symbol_contributors,
                }
                for row in rows
            ]
        ),
        request_id=request_id,
    )


def _factor_snapshot_response(row) -> FactorExposureSnapshotResponse:
    return FactorExposureSnapshotResponse(
        snapshot_id=row.snapshot_id,
        run_id=row.run_id,
        portfolio_id=row.portfolio_id,
        computed_at=row.computed_at,
        as_of_date=row.as_of_date,
        lookback_window=row.lookback_window,
        benchmark_symbol=row.benchmark_symbol,
        benchmark_source=row.benchmark_source,
        factor_computation_version=row.factor_computation_version,
        portfolio_exposures=row.portfolio_exposures,
        strategy_exposures=row.strategy_exposures,
        symbol_exposures=row.symbol_exposures,
        sector_exposures=row.sector_exposures,
        concentration_diagnostics=row.concentration_diagnostics,
        warnings=row.warnings,
        factor_methodology=row.factor_methodology,
        data_lineage=row.data_lineage,
        duration_seconds=row.duration_seconds,
    )
