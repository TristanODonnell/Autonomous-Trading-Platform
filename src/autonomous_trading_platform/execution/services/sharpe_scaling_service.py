# autonomous_trading_platform/execution/services/sharpe_scaling_service.py

from __future__ import annotations

import logging
from decimal import Decimal

import numpy as np

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR as _BARS_PER_YEAR

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


class SharpeScalingService:
    """
    Computes a sharpe_scalar in (0, 1] from recent bar closes.

    Algorithm (Sharpe-ratio-based position scaling):
        log_returns      = bar-over-bar log returns
        mean_return      = annualized mean of log returns
        realized_vol     = annualized stddev of log returns
        rolling_sharpe   = (mean_return - risk_free_rate) / realized_vol

    Scaling rule:
        - sharpe >= target_sharpe  → scalar = 1.0  (full size)
        - sharpe <= 0              → scalar = min_scalar  (floor, not zero)
        - 0 < sharpe < target      → scalar = linear interpolation between
                                     min_scalar and 1.0

    Returns None when there are insufficient bars (caller uses full size).

    Design mirrors VolatilityScalingService so both scalars can be multiplied
    together in PortfolioConstructionService before hitting PositionSizer.
    """

    def __init__(
        self,
        target_sharpe: float = 1.0,
        risk_free_rate: float = 0.05,
        min_scalar: float = 0.25,
        min_bars: int = 20,
    ) -> None:
        if target_sharpe <= 0:
            raise ValueError(f"target_sharpe must be positive, got {target_sharpe}")
        if not (0.0 <= risk_free_rate < 1.0):
            raise ValueError(f"risk_free_rate must be in [0, 1), got {risk_free_rate}")
        if not (0.0 < min_scalar <= 1.0):
            raise ValueError(f"min_scalar must be in (0, 1], got {min_scalar}")
        if min_bars < 2:
            raise ValueError(f"min_bars must be >= 2, got {min_bars}")

        self._target_sharpe = target_sharpe
        self._risk_free_rate = risk_free_rate
        self._min_scalar = min_scalar
        self._min_bars = min_bars

    def compute_scalar(
        self,
        symbol: str,
        closes: list[float],
    ) -> Decimal | None:
        """
        Returns a Decimal scalar in (0, 1], or None if insufficient data.
        """
        if len(closes) < self._min_bars:
            logger.debug(
                "sharpe_scaling.insufficient_bars",
                extra={
                    "symbol": symbol,
                    "bars_available": len(closes),
                    "min_bars": self._min_bars,
                },
            )
            return None

        closes_arr = np.array(closes, dtype=float)
        valid = (closes_arr[:-1] > 0) & (closes_arr[1:] > 0)
        log_returns = np.log(closes_arr[1:][valid] / closes_arr[:-1][valid])

        if len(log_returns) < self._min_bars - 1:
            return None

        bar_vol = float(np.std(log_returns, ddof=1))

        if bar_vol <= 0:
            # Zero vol — perfectly trending or flat; treat as Sharpe = target.
            return ONE

        # Annualize both mean and vol using the same convention as VolatilityScalingService
        annual_mean = float(np.mean(log_returns)) * _BARS_PER_YEAR
        annual_vol = bar_vol * np.sqrt(_BARS_PER_YEAR)

        rolling_sharpe = (annual_mean - self._risk_free_rate) / annual_vol

        # Map Sharpe → scalar
        if rolling_sharpe >= self._target_sharpe:
            scalar = 1.0
        elif rolling_sharpe <= 0:
            scalar = self._min_scalar
        else:
            # Linear interpolation: 0 → min_scalar, target → 1.0
            t = rolling_sharpe / self._target_sharpe
            scalar = self._min_scalar + t * (1.0 - self._min_scalar)

        logger.debug(
            "sharpe_scaling.computed",
            extra={
                "symbol": symbol,
                "rolling_sharpe": round(rolling_sharpe, 4),
                "target_sharpe": self._target_sharpe,
                "sharpe_scalar": round(scalar, 4),
            },
        )

        return Decimal(str(round(scalar, 6)))
