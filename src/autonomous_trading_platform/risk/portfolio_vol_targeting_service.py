from __future__ import annotations

import logging
import math
from decimal import Decimal

ZERO = Decimal("0")
ONE = Decimal("1")

_TRADING_DAYS_PER_YEAR = 252

logger = logging.getLogger(__name__)


class PortfolioVolTargetingService:
    """
    Computes a portfolio-level vol scalar in (0, 1] from the equity curve.

    Algorithm:
        daily_returns = equity[t] / equity[t-1] - 1
        realized_vol  = annualised stddev of daily_returns
        vol_scalar    = min(target_vol / realized_vol, 1.0)

    When portfolio vol is running hot the scalar shrinks all positions
    uniformly.  Scalar never exceeds 1.0 — we only scale down, never up.

    Returns None when there are insufficient data points; callers treat
    None as "no opinion — use full size".

    How this differs from per-asset VolatilityScalingService:
        - Input   : equity curve (one value per trading day), not bar closes
        - Output  : ONE scalar for the whole portfolio, not per-symbol
        - Annualisation: sqrt(252 days), not sqrt(78 bars/day * 252)
    """

    def __init__(
        self,
        target_annual_vol: float = 0.10,
        min_bars: int = 20,
    ) -> None:
        if target_annual_vol <= 0:
            raise ValueError(f"target_annual_vol must be positive, got {target_annual_vol}")
        if min_bars < 2:
            raise ValueError(f"min_bars must be >= 2, got {min_bars}")

        self._target = target_annual_vol
        self._min_bars = min_bars

    def compute_scalar(self, equity_curve: list[float]) -> Decimal | None:
        """
        equity_curve — ordered portfolio equity values, one per trading day,
                       most recent last.

        Returns Decimal in (0, 1], or None if insufficient history.
        """
        realized = self.compute_realized_vol(equity_curve)
        if realized is None:
            return None

        if realized <= 0:
            return ONE

        scalar = min(self._target / realized, 1.0)

        logger.debug(
            "portfolio_vol_targeting.scalar",
            extra={
                "realized_annual_vol": round(realized, 4),
                "target_annual_vol": self._target,
                "portfolio_vol_scalar": round(scalar, 4),
            },
        )

        return Decimal(str(round(scalar, 6)))

    def compute_realized_vol(self, equity_curve: list[float]) -> float | None:
        """
        Returns annualised realised vol as a plain float, or None if
        insufficient history.  Used directly by RiskAlertService.
        """
        if len(equity_curve) < self._min_bars:
            logger.debug(
                "portfolio_vol_targeting.insufficient_bars",
                extra={
                    "bars_available": len(equity_curve),
                    "min_bars": self._min_bars,
                },
            )
            return None

        daily_returns = [
            equity_curve[i] / equity_curve[i - 1] - 1.0
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]

        if len(daily_returns) < self._min_bars - 1:
            return None

        n = len(daily_returns)
        mean = sum(daily_returns) / n
        variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        daily_vol = math.sqrt(variance)
        return daily_vol * math.sqrt(_TRADING_DAYS_PER_YEAR)
