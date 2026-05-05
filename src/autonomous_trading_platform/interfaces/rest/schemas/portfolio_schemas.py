from decimal import Decimal

from pydantic import BaseModel


class PortfolioSummaryResponse(BaseModel):
    current_portfolio_value: Decimal
    todays_pnl_amount: Decimal
    todays_pnl_percent: Decimal
    total_pnl_amount: Decimal
    total_pnl_percent: Decimal
    cash_balance: Decimal
