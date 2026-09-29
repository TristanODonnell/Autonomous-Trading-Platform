"""
Bench re-simulation (portfolio rotation step 3).

Re-runs each tracked strategy's stored config — exactly as researched, no
generation — over one shared recent window, so every strategy is judged on the
same fresh data. Produces per-strategy metrics, a quality score on the shared
scale (metrics_quality_score) and a daily return series for redundancy checks.

Runs are tagged with an experiment id starting with BENCH_RESIM_EXPERIMENT_PREFIX,
which keeps them out of the approval-backtest metrics that blended quality reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    BENCH_RESIM_EXPERIMENT_PREFIX,
    metrics_quality_score,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.strategy.configs.stored_config import stored_config_parameters

logger = get_logger(__name__)


class _Runner(Protocol):
    def run(self, request: SimulationRunRequest) -> Any: ...


@dataclass(frozen=True)
class BenchWindow:
    dataset_version: str
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date


@dataclass
class ResimOutcome:
    strategy_id: str
    strategy_type: str | None
    run_id: UUID | None = None
    trade_count: int | None = None
    total_return: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    score: Decimal | None = None
    # Daily close-to-close returns over the window, indexed by date.
    daily_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class BenchResimulationService:
    def __init__(
        self,
        session: Session,
        simulation_runner: _Runner,
        *,
        initial_cash: float = 100_000.0,
        random_seed: int = 42,
    ) -> None:
        self._session = session
        self._runner = simulation_runner
        self._initial_cash = initial_cash
        self._seed = random_seed

    @staticmethod
    def experiment_id(review_id: str) -> str:
        return f"{BENCH_RESIM_EXPERIMENT_PREFIX}{review_id}"

    def resimulate(
        self, strategy_ids: list[str], *, window: BenchWindow, review_id: str
    ) -> dict[str, ResimOutcome]:
        """Re-simulate each strategy over the window. One failure never stops the rest."""
        return {
            sid: self._resimulate_one(sid, window=window, review_id=review_id)
            for sid in strategy_ids
        }

    def _resimulate_one(
        self, strategy_id: str, *, window: BenchWindow, review_id: str
    ) -> ResimOutcome:
        config = self._session.get(StrategyConfigs, strategy_id)
        if config is None or not config.strategy_type:
            return ResimOutcome(strategy_id, None, error="missing_strategy_config")
        outcome = ResimOutcome(strategy_id, config.strategy_type)
        try:
            result = self._runner.run(
                SimulationRunRequest(
                    strategy_id=strategy_id,
                    strategy_config={
                        "type": config.strategy_type,
                        "strategy_id": strategy_id,
                        "parameters": stored_config_parameters(config.config_json),
                    },
                    dataset_version=window.dataset_version,
                    random_seed=self._seed,
                    price_basis=window.price_basis,
                    symbols=list(window.symbols),
                    start_date=window.start_date,
                    end_date=window.end_date,
                    initial_cash=self._initial_cash,
                    experiment_id=self.experiment_id(review_id),
                    window_role="bench",
                    stage_name="bench_resim",
                )
            )
        except Exception as exc:
            logger.warning(
                "bench_resim.strategy_failed",
                extra={"strategy_id": strategy_id, "review_id": review_id, "error": str(exc)},
            )
            outcome.error = str(exc) or type(exc).__name__
            return outcome

        outcome.run_id = result.run_id
        outcome.trade_count = int(result.trade_count)
        outcome.total_return = float(result.return_metrics.total_return)
        outcome.sharpe_ratio = float(result.risk_metrics.sharpe_ratio)
        outcome.max_drawdown = float(result.risk_metrics.max_drawdown)
        outcome.win_rate = float(result.trade_metrics.win_rate)
        outcome.score = metrics_quality_score(
            sharpe=outcome.sharpe_ratio,
            total_return=outcome.total_return,
            max_drawdown=outcome.max_drawdown,
            win_rate=outcome.win_rate,
            trade_count=outcome.trade_count,
        )
        outcome.daily_returns = daily_returns(result.equity_curve)
        return outcome


def daily_returns(equity_curve: pd.DataFrame | None) -> pd.Series:
    """Close-to-close daily returns from an equity curve with timestamp/equity columns."""
    if equity_curve is None or equity_curve.empty:
        return pd.Series(dtype=float)
    frame = equity_curve[["timestamp", "equity"]].copy()
    frame["day"] = pd.to_datetime(frame["timestamp"], utc=True).dt.date
    closes = frame.sort_values("timestamp").groupby("day")["equity"].last().astype(float)
    return closes.pct_change().dropna()
