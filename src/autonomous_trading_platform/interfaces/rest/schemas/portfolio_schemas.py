from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class PortfolioSummaryResponse(BaseModel):
    current_portfolio_value: Decimal
    todays_pnl_amount: Decimal
    todays_pnl_percent: Decimal
    total_pnl_amount: Decimal
    total_pnl_percent: Decimal
    cash_balance: Decimal


PortfolioEquityCurvePeriod = Literal["today", "1w", "1m", "3m", "ytd"]


class PortfolioEquityCurvePoint(BaseModel):
    timestamp: datetime
    value: Decimal


class PortfolioEquityCurveResponse(BaseModel):
    period: PortfolioEquityCurvePeriod
    points: list[PortfolioEquityCurvePoint]
