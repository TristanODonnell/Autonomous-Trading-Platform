"""
Rotation report at the end of a platform backtest (portfolio rotation step 5).

Portfolio-mode runs get a `rotation` section in the artifact: performance from the
sleeves, a buy-and-hold benchmark read from the replay's own bars dataset, rotation
activity, turnover and per-strategy contribution.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.rotation_report_service import (
    RotationReportService,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.governance.rotation_report import RotationReport
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

logger = get_logger(__name__)

DEFAULT_BENCHMARK = "SPY"


def build_rotation_summary(
    *,
    session: Session,
    start_date: date,
    end_date: date,
    starting_cash: float,
    dataset_version_id: str | None,
    symbols: list[str],
    benchmark_symbol: str = DEFAULT_BENCHMARK,
) -> RotationReport | None:
    """The rotation report for a portfolio-mode run; None when portfolio mode is off."""
    if not OperatorSettingsRepository(session).get_or_create_default().portfolio_mode_enabled:
        return None
    closes = None
    if dataset_version_id and benchmark_symbol in {s.upper() for s in symbols}:
        closes = load_daily_closes(
            session=session,
            dataset_version=dataset_version_id,
            symbol=benchmark_symbol,
            start_date=start_date,
            end_date=end_date,
        )
    return RotationReportService(session).build(
        start_date=start_date,
        end_date=end_date,
        starting_cash=starting_cash,
        benchmark_symbol=benchmark_symbol,
        benchmark_closes=closes,
    )


def load_daily_closes(
    *,
    session: Session,
    dataset_version: str,
    symbol: str,
    start_date: date,
    end_date: date,
    price_basis: PriceBasis = PriceBasis.RAW,
    simulation_runner: Any = None,
) -> pd.Series | None:
    """Last close per day for one symbol from a bars dataset, or None if unavailable.

    Uses the simulation runner's reader and dataset resolver, so it sees the same bars
    as research and bench re-sims.
    """
    try:
        if simulation_runner is None:
            from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
                build_simulation_context,
            )

            simulation_runner = build_simulation_context(
                session=session, universe_size=1
            ).simulation_runner
        reader = getattr(getattr(simulation_runner, "window_loader", None), "bar_reader", None)
        resolver = getattr(simulation_runner, "dataset_resolver", None)
        if reader is None or resolver is None:
            return None
        resolved = resolver.resolve_bars_dataset(
            dataset_version=dataset_version, price_basis=price_basis
        )
        table = reader.read(
            dataset=resolved.dataset,
            dataset_version=dataset_version,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date + timedelta(days=1),
        )
    except Exception as exc:
        logger.warning(
            "rotation_report.benchmark_unavailable", extra={"symbol": symbol, "error": str(exc)}
        )
        return None
    if table.num_rows == 0:
        return None
    return daily_closes(table.to_pandas())


def daily_closes(bars: pd.DataFrame) -> pd.Series:
    """Last close per calendar day (UTC) from intraday or daily bars."""
    frame = bars[["timestamp", "close"]].copy()
    frame["day"] = pd.to_datetime(frame["timestamp"], utc=True).dt.date
    return frame.sort_values("timestamp").groupby("day")["close"].last().astype(float)
