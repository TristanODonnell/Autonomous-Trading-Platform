from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.portfolio_analytics_service import (
    PortfolioAnalyticsService,
)
from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
    PortfolioEquityCurveService,
)
from autonomous_trading_platform.application.services.portfolio_summary_service import (
    PortfolioSummaryService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.portfolio_schemas import (
    PortfolioAllocationResponse,
    PortfolioEquityCurvePeriod,
    PortfolioEquityCurveResponse,
    PortfolioHoldingsResponse,
    PortfolioPerformanceByPeriodResponse,
    PortfolioPerformanceResponse,
    PortfolioRiskResponse,
    PortfolioSummaryResponse,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get("/summary", response_model=SuccessEnvelope[PortfolioSummaryResponse])
def get_portfolio_summary(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[PortfolioSummaryResponse]:
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
) -> SuccessEnvelope[PortfolioHoldingsResponse]:
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
) -> SuccessEnvelope[PortfolioAllocationResponse]:
    service = PortfolioAnalyticsService(session=session)
    result = service.get_allocation()

    return success_response(
        data=PortfolioAllocationResponse(**result),
        request_id=request_id,
    )


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
