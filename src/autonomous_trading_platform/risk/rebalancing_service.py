from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.risk.risk_engine import PortfolioExposureSnapshot

ZERO = Decimal("0")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebalanceDelta:
    """Rebalance instruction for a single strategy."""

    strategy_id: str
    current_weight: Decimal  # fraction of total gross exposure right now
    target_weight: Decimal
    drift: Decimal  # abs(current_weight - target_weight)
    action: str  # "reduce" | "increase"
    suggested_capital_usd: Decimal


@dataclass(frozen=True)
class RebalanceReport:
    """
    Output of RebalancingService.compute_rebalance().
    Contains only strategies whose drift exceeds the threshold.
    needs_rebalance is False when all weights are within tolerance.
    """

    needs_rebalance: bool
    total_capital_usd: Decimal
    deltas: list[RebalanceDelta]  # sorted by drift descending
    max_drift: Decimal
    drift_threshold: Decimal


class RebalancingService:
    """
    Compares current strategy weights (from a PortfolioExposureSnapshot)
    against target weights and produces rebalance instructions when any
    weight drifts beyond drift_threshold.

    Output is purely advisory — acting on the deltas (placing orders,
    adjusting allocation policies) is the caller's responsibility.

    Args:
        target_weights   — {strategy_id: target_fraction}  must sum to <= 1.0
        drift_threshold  — trigger rebalance when abs drift exceeds this (default 5 %)
    """

    def __init__(
        self,
        target_weights: dict[str, float],
        drift_threshold: float = 0.05,
    ) -> None:
        if not target_weights:
            raise ValueError("target_weights must not be empty")
        if any(w < 0 for w in target_weights.values()):
            raise ValueError("All target weights must be >= 0")
        total = sum(target_weights.values())
        if total > 1.0 + 1e-9:
            raise ValueError(f"target_weights sum to {total:.4f} — must be <= 1.0")
        if not (0.0 < drift_threshold < 1.0):
            raise ValueError(f"drift_threshold must be in (0, 1), got {drift_threshold}")

        self._target_weights: dict[str, Decimal] = {
            sid: Decimal(str(w)) for sid, w in target_weights.items()
        }
        self._drift_threshold = Decimal(str(drift_threshold))

    def compute_rebalance(
        self,
        snapshot: PortfolioExposureSnapshot,
        total_capital_usd: float,
    ) -> RebalanceReport:
        """
        snapshot          — latest PortfolioExposureSnapshot from RiskEngine
        total_capital_usd — current total portfolio capital
                            (use IAllocationProvider.total_capital)
        """
        total_capital = Decimal(str(total_capital_usd))
        total_gross = snapshot.total_gross_exposure_usd

        deltas: list[RebalanceDelta] = []
        max_drift = ZERO

        for sid, target_w in self._target_weights.items():
            exposure = snapshot.by_strategy.get(sid)
            current_w = (
                exposure.gross_exposure_usd / total_gross
                if exposure is not None and total_gross > ZERO
                else ZERO
            )

            drift = abs(current_w - target_w)
            if drift > max_drift:
                max_drift = drift

            if drift < self._drift_threshold:
                continue

            deltas.append(
                RebalanceDelta(
                    strategy_id=sid,
                    current_weight=current_w,
                    target_weight=target_w,
                    drift=drift,
                    action="reduce" if current_w > target_w else "increase",
                    suggested_capital_usd=total_capital * target_w,
                )
            )

        needs_rebalance = bool(deltas)

        if needs_rebalance:
            logger.info(
                "rebalancing.drift_detected",
                extra={
                    "strategies_drifted": len(deltas),
                    "max_drift": float(max_drift),
                    "drift_threshold": float(self._drift_threshold),
                    "strategies": [d.strategy_id for d in deltas],
                },
            )
        else:
            logger.debug(
                "rebalancing.within_tolerance",
                extra={
                    "max_drift": float(max_drift),
                    "drift_threshold": float(self._drift_threshold),
                },
            )

        return RebalanceReport(
            needs_rebalance=needs_rebalance,
            total_capital_usd=total_capital,
            deltas=sorted(deltas, key=lambda d: d.drift, reverse=True),
            max_drift=max_drift,
            drift_threshold=self._drift_threshold,
        )
