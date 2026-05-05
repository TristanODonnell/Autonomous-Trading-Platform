from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
    PortfolioEquityCurveService,
)
from autonomous_trading_platform.application.services.portfolio_summary_service import (
    PortfolioSummaryService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.portfolio_schemas import (
    PortfolioEquityCurvePeriod,
    PortfolioEquityCurveResponse,
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
