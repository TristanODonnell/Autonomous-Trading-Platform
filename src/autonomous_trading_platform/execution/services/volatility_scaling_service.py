# autonomous_trading_platform/execution/services/volatility_scaling_service.py

from __future__ import annotations

import logging
from decimal import Decimal

import numpy as np

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR as _BARS_PER_YEAR

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


class VolatilityScalingService:
    """
    Computes a vol_scalar in (0, 1] from recent bar closes.

    Algorithm (volatility targeting):
        realized_vol = annualized stddev of bar-over-bar log returns
        vol_scalar   = min(target_vol / realized_vol, 1.0)

    When realized vol is low, scalar approaches 1.0 (full size).
    When realized vol is high, scalar shrinks the position proportionally.
    Scalar never exceeds 1.0 — we only scale down, never up.

    Returns None when there are insufficient bars to compute vol,
    which tells the caller to skip scaling (use full size).
    """

    def __init__(
        self,
        target_annual_vol: float = 0.15,
        min_bars: int = 20,
    ) -> None:
        if target_annual_vol <= 0:
            raise ValueError(f"target_annual_vol must be positive, got {target_annual_vol}")
        if min_bars < 2:
            raise ValueError(f"min_bars must be >= 2, got {min_bars}")

        self._target_annual_vol = target_annual_vol
        self._min_bars = min_bars

    def compute_scalar(
        self,
        symbol: str,
        closes: list[float],
    ) -> Decimal | None:
        if len(closes) < self._min_bars:
            logger.debug(
                "vol_scaling.insufficient_bars",
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

        annual_vol = bar_vol * np.sqrt(_BARS_PER_YEAR)

        if annual_vol <= 0:
            return None

        scalar = min(self._target_annual_vol / annual_vol, 1.0)

        logger.debug(
            "vol_scaling.computed",
            extra={
                "symbol": symbol,
                "realized_annual_vol": round(annual_vol, 4),
                "target_annual_vol": self._target_annual_vol,
                "vol_scalar": round(scalar, 4),
            },
        )

        return Decimal(str(round(scalar, 6)))
