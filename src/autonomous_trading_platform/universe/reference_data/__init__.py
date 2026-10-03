"""
Versioned reference data shipped with the package.

sp500_ticker_start_end.csv
    Point-in-time S&P 500 membership: one row per (ticker, start_date, end_date)
    membership spell; empty end_date = still a member.
    Source: https://github.com/fja05680/sp500 (MIT, see
    LICENSE_sp500_ticker_start_end.txt), commit a2430f2a (2026-09-07).
    Refresh by downloading the same file from that repository and updating the
    commit reference above.
"""
