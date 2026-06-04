"""Portfolio domain replay hook."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.portfolio_analytics_service import (
    PortfolioAnalyticsService,
)
from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
    PortfolioEquityCurveService,
)
from autonomous_trading_platform.application.services.portfolio_summary_service import (
    PortfolioSummaryService,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    PortfolioReplayResult,
    PortfolioSummaryBundle,
)


def snapshot_portfolio_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> PortfolioReplayResult:
    """Read portfolio state as of the latest snapshots for this timestamp."""
    base = dict(
        domain="portfolio",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    warnings: list[str] = []
    portfolio_value: Decimal | None = None
    cash_balance: Decimal | None = None
    open_positions = 0

    try:
        summary_svc = PortfolioSummaryService(session=session)
        raw_summary = summary_svc.get_summary()
        portfolio_value = Decimal(str(raw_summary.get("current_portfolio_value", 0)))
        cash_balance = Decimal(str(raw_summary.get("cash_balance", 0)))
        open_positions = int(raw_summary.get("open_positions", 0))
    except Exception as exc:
        warnings.append(f"Portfolio summary failed: {exc}")

    try:
        PortfolioAnalyticsService(session=session).get_holdings()
    except Exception as exc:
        warnings.append(f"Portfolio holdings read failed: {exc}")

    curve_points = 0
    try:
        curve = PortfolioEquityCurveService(session=session).get_equity_curve("1m")
        curve_points = len(curve.get("points", []))
    except Exception:
        pass

    return PortfolioReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        portfolio_value=portfolio_value,
        cash_balance=cash_balance,
        open_positions=open_positions,
        summary={
            "portfolio_value": str(portfolio_value) if portfolio_value is not None else None,
            "cash_balance": str(cash_balance) if cash_balance is not None else None,
            "open_positions": open_positions,
            "equity_curve_points": curve_points,
            "timestamp": timestamp.isoformat(),
        },
    )


def build_portfolio_summary(*, session: Session) -> PortfolioSummaryBundle:
    """Read latest portfolio state for the platform artifact bundle."""
    portfolio_value = None
    cash_balance = None
    invested_capital = None
    open_positions = 0
    total_pnl_pct = None
    curve_points = 0

    try:
        raw = PortfolioSummaryService(session=session).get_summary()
        portfolio_value = str(raw.get("current_portfolio_value", ""))
        cash_balance = str(raw.get("cash_balance", ""))
        invested_capital = str(raw.get("invested_capital", ""))
        open_positions = int(raw.get("open_positions", 0))
        total_pnl_pct = str(raw.get("total_pnl_percent", ""))
    except Exception:
        pass

    try:
        curve = PortfolioEquityCurveService(session=session).get_equity_curve("1m")
        curve_points = len(curve.get("points", []))
    except Exception:
        pass

    return PortfolioSummaryBundle(
        portfolio_value=portfolio_value,
        cash_balance=cash_balance,
        invested_capital=invested_capital,
        open_positions=open_positions,
        total_pnl_pct=total_pnl_pct,
        equity_curve_points_count=curve_points,
    )
