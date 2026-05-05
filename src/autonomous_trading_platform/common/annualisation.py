"""
Shared annualisation constants for return and risk metrics.

Convention: trading days everywhere.
--------------------------------------
Both CAGR and Sharpe/Sortino/volatility use the same time base so that
metrics produced by return_metrics and risk_metrics are directly comparable
in filtering, ranking, and composite scoring.

  bars_per_year = 252 trading days × 78 five-minute bars/session = 19_656

CAGR is expressed in trading-day years (exponent = bars / bars_per_year).
This makes it ~1.45× a calendar-day year, so reported CAGR will be slightly
higher than a calendar-day figure for the same backtest — that is expected and
correct for intra-system comparisons.  If comparing against an external
calendar-day CAGR, note the convention difference.

If daily or 1-minute bar support is added, pass bars_per_year explicitly to
all metric functions (e.g. 252 for daily, 252*390 for 1-minute).
"""

BARS_PER_DAY: int = 78  # five-minute bars per regular session
TRADING_DAYS_PER_YEAR: int = 252
BARS_PER_YEAR: int = TRADING_DAYS_PER_YEAR * BARS_PER_DAY  # 19_656
