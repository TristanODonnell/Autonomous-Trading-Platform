from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from autonomous_trading_platform.risk.risk_engine import PortfolioExposureSnapshot

logger = logging.getLogger(__name__)


class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(StrEnum):
    GROSS_EXPOSURE_BREACH = "gross_exposure_breach"
    STRATEGY_CONCENTRATION = "strategy_concentration"
    SECTOR_CONCENTRATION = "sector_concentration"
    DRAWDOWN_BREACH = "drawdown_breach"
    PORTFOLIO_VOL_BREACH = "portfolio_vol_breach"
    CORRELATION_BREACH = "correlation_breach"


@dataclass(frozen=True)
class RiskAlert:
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    context: dict


@runtime_checkable
class IRiskNotifier(Protocol):
    def notify(self, alert: RiskAlert) -> None: ...


class LoggingRiskNotifier:
    """Emits structured log lines. Default notifier."""

    def notify(self, alert: RiskAlert) -> None:
        log_fn = logger.critical if alert.severity == AlertSeverity.CRITICAL else logger.warning
        log_fn(
            "risk_alert.%s",
            alert.alert_type,
            extra={
                "severity": alert.severity,
                "message": alert.message,
                **alert.context,
            },
        )


@dataclass
class RiskThresholds:
    # Gross exposure as a ratio of total capital
    max_gross_exposure_ratio_warning: float = 1.0  # 100 % of capital
    max_gross_exposure_ratio_critical: float = 1.5  # 150 %

    # Largest single-strategy weight in the portfolio (0–1)
    max_strategy_weight_warning: float = 0.30  # 30 %
    max_strategy_weight_critical: float = 0.50  # 50 %

    # Largest single-sector weight (0–1)
    max_sector_weight_warning: float = 0.40  # 40 %
    max_sector_weight_critical: float = 0.60  # 60 %

    # Drawdown from peak (positive fraction, e.g. 0.10 = -10 %)
    max_drawdown_warning: float = 0.10
    max_drawdown_critical: float = 0.20

    # Annualised portfolio volatility
    max_portfolio_vol_warning: float = 0.15
    max_portfolio_vol_critical: float = 0.25

    # Average pairwise strategy correlation (0–1)
    max_avg_correlation_warning: float = 0.60
    max_avg_correlation_critical: float = 0.80


class RiskAlertService:
    """
    Evaluates a PortfolioExposureSnapshot against RiskThresholds and fires
    RiskAlerts through one or more IRiskNotifier implementations.

    All checks are independent; a single evaluate() call may fire multiple
    alerts.  The method also returns the full list of alerts raised so
    callers can inspect or persist them.

    Args:
        snapshot        — from RiskEngine.compute_exposures()
        total_capital   — for converting gross exposure to a ratio
        equity_curve    — optional list[float]; enables drawdown check
        realized_vol    — optional float (annualised); enables vol check
                          (pass PortfolioVolTargetingService.compute_realized_vol())
        avg_correlation — optional float (0–1); enables correlation check
                          (pass correlation_service.average_pairwise_correlation())
    """

    def __init__(
        self,
        thresholds: RiskThresholds | None = None,
        notifiers: list[IRiskNotifier] | None = None,
    ) -> None:
        self._t = thresholds or RiskThresholds()
        self._notifiers: list[IRiskNotifier] = notifiers or [LoggingRiskNotifier()]

    def evaluate(
        self,
        snapshot: PortfolioExposureSnapshot,
        total_capital: float,
        equity_curve: list[float] | None = None,
        realized_vol: float | None = None,
        avg_correlation: float | None = None,
    ) -> list[RiskAlert]:
        alerts: list[RiskAlert] = []

        alerts.extend(self._check_gross_exposure(snapshot, total_capital))
        alerts.extend(self._check_strategy_concentration(snapshot))
        alerts.extend(self._check_sector_concentration(snapshot))

        if equity_curve is not None:
            alerts.extend(self._check_drawdown(equity_curve))

        if realized_vol is not None:
            alerts.extend(self._check_portfolio_vol(realized_vol))

        if avg_correlation is not None:
            alerts.extend(self._check_correlation(avg_correlation))

        for alert in alerts:
            for notifier in self._notifiers:
                notifier.notify(alert)

        return alerts

    def _check_gross_exposure(
        self,
        snapshot: PortfolioExposureSnapshot,
        total_capital: float,
    ) -> list[RiskAlert]:
        if total_capital <= 0:
            return []

        ratio = float(snapshot.total_gross_exposure_usd) / total_capital

        if ratio >= self._t.max_gross_exposure_ratio_critical:
            return [
                self._alert(
                    AlertType.GROSS_EXPOSURE_BREACH,
                    AlertSeverity.CRITICAL,
                    f"Gross exposure ratio {ratio:.1%} exceeds critical limit "
                    f"{self._t.max_gross_exposure_ratio_critical:.1%}",
                    {
                        "gross_exposure_ratio": ratio,
                        "gross_exposure_usd": float(snapshot.total_gross_exposure_usd),
                        "total_capital_usd": total_capital,
                    },
                )
            ]

        if ratio >= self._t.max_gross_exposure_ratio_warning:
            return [
                self._alert(
                    AlertType.GROSS_EXPOSURE_BREACH,
                    AlertSeverity.WARNING,
                    f"Gross exposure ratio {ratio:.1%} exceeds warning limit "
                    f"{self._t.max_gross_exposure_ratio_warning:.1%}",
                    {
                        "gross_exposure_ratio": ratio,
                        "gross_exposure_usd": float(snapshot.total_gross_exposure_usd),
                        "total_capital_usd": total_capital,
                    },
                )
            ]

        return []

    def _check_strategy_concentration(
        self,
        snapshot: PortfolioExposureSnapshot,
    ) -> list[RiskAlert]:
        weight = float(snapshot.max_strategy_weight)

        if weight >= self._t.max_strategy_weight_critical:
            # Find which strategy it is for context
            top = max(
                snapshot.by_strategy.values(),
                key=lambda e: e.gross_exposure_usd,
                default=None,
            )
            sid = top.strategy_id if top else "unknown"
            return [
                self._alert(
                    AlertType.STRATEGY_CONCENTRATION,
                    AlertSeverity.CRITICAL,
                    f"Strategy '{sid}' has weight {weight:.1%}, exceeds critical "
                    f"limit {self._t.max_strategy_weight_critical:.1%}",
                    {"strategy_id": sid, "strategy_weight": weight},
                )
            ]

        if weight >= self._t.max_strategy_weight_warning:
            top = max(
                snapshot.by_strategy.values(),
                key=lambda e: e.gross_exposure_usd,
                default=None,
            )
            sid = top.strategy_id if top else "unknown"
            return [
                self._alert(
                    AlertType.STRATEGY_CONCENTRATION,
                    AlertSeverity.WARNING,
                    f"Strategy '{sid}' has weight {weight:.1%}, exceeds warning "
                    f"limit {self._t.max_strategy_weight_warning:.1%}",
                    {"strategy_id": sid, "strategy_weight": weight},
                )
            ]

        return []

    def _check_sector_concentration(
        self,
        snapshot: PortfolioExposureSnapshot,
    ) -> list[RiskAlert]:
        if not snapshot.by_sector:
            return []

        alerts = []
        for sec_exp in snapshot.by_sector.values():
            weight = float(sec_exp.weight)

            if weight >= self._t.max_sector_weight_critical:
                alerts.append(
                    self._alert(
                        AlertType.SECTOR_CONCENTRATION,
                        AlertSeverity.CRITICAL,
                        f"Sector '{sec_exp.sector}' weight {weight:.1%} exceeds "
                        f"critical limit {self._t.max_sector_weight_critical:.1%}",
                        {"sector": sec_exp.sector, "sector_weight": weight},
                    )
                )
            elif weight >= self._t.max_sector_weight_warning:
                alerts.append(
                    self._alert(
                        AlertType.SECTOR_CONCENTRATION,
                        AlertSeverity.WARNING,
                        f"Sector '{sec_exp.sector}' weight {weight:.1%} exceeds "
                        f"warning limit {self._t.max_sector_weight_warning:.1%}",
                        {"sector": sec_exp.sector, "sector_weight": weight},
                    )
                )

        return alerts

    def _check_drawdown(self, equity_curve: list[float]) -> list[RiskAlert]:
        if len(equity_curve) < 2:
            return []

        # np.maximum.accumulate is consistent with risk_metrics.max_drawdown.
        # At the end-point both approaches agree numerically (running_peak[-1]
        # == max(equity_curve)), but using accumulate makes the intent explicit
        # and keeps the two drawdown implementations uniform so they cannot
        # silently diverge if this method is ever extended to scan mid-curve.
        import numpy as np

        arr = np.array(equity_curve, dtype=float)
        running_peak = np.maximum.accumulate(arr)
        peak = float(running_peak[-1])
        current = float(arr[-1])
        if peak <= 0:
            return []

        drawdown = (peak - current) / peak  # positive fraction

        if drawdown >= self._t.max_drawdown_critical:
            return [
                self._alert(
                    AlertType.DRAWDOWN_BREACH,
                    AlertSeverity.CRITICAL,
                    f"Portfolio drawdown {drawdown:.1%} exceeds critical limit "
                    f"{self._t.max_drawdown_critical:.1%}",
                    {"drawdown": drawdown, "peak_equity": peak, "current_equity": current},
                )
            ]

        if drawdown >= self._t.max_drawdown_warning:
            return [
                self._alert(
                    AlertType.DRAWDOWN_BREACH,
                    AlertSeverity.WARNING,
                    f"Portfolio drawdown {drawdown:.1%} exceeds warning limit "
                    f"{self._t.max_drawdown_warning:.1%}",
                    {"drawdown": drawdown, "peak_equity": peak, "current_equity": current},
                )
            ]

        return []

    def _check_portfolio_vol(self, realized_vol: float) -> list[RiskAlert]:
        if realized_vol >= self._t.max_portfolio_vol_critical:
            return [
                self._alert(
                    AlertType.PORTFOLIO_VOL_BREACH,
                    AlertSeverity.CRITICAL,
                    f"Portfolio vol {realized_vol:.1%} exceeds critical limit "
                    f"{self._t.max_portfolio_vol_critical:.1%}",
                    {"realized_annual_vol": realized_vol},
                )
            ]

        if realized_vol >= self._t.max_portfolio_vol_warning:
            return [
                self._alert(
                    AlertType.PORTFOLIO_VOL_BREACH,
                    AlertSeverity.WARNING,
                    f"Portfolio vol {realized_vol:.1%} exceeds warning limit "
                    f"{self._t.max_portfolio_vol_warning:.1%}",
                    {"realized_annual_vol": realized_vol},
                )
            ]

        return []

    def _check_correlation(self, avg_correlation: float) -> list[RiskAlert]:
        if avg_correlation >= self._t.max_avg_correlation_critical:
            return [
                self._alert(
                    AlertType.CORRELATION_BREACH,
                    AlertSeverity.CRITICAL,
                    f"Average strategy correlation {avg_correlation:.2f} exceeds "
                    f"critical limit {self._t.max_avg_correlation_critical:.2f}",
                    {"avg_pairwise_correlation": avg_correlation},
                )
            ]

        if avg_correlation >= self._t.max_avg_correlation_warning:
            return [
                self._alert(
                    AlertType.CORRELATION_BREACH,
                    AlertSeverity.WARNING,
                    f"Average strategy correlation {avg_correlation:.2f} exceeds "
                    f"warning limit {self._t.max_avg_correlation_warning:.2f}",
                    {"avg_pairwise_correlation": avg_correlation},
                )
            ]

        return []

    @staticmethod
    def _alert(
        alert_type: AlertType,
        severity: AlertSeverity,
        message: str,
        context: dict,
    ) -> RiskAlert:
        return RiskAlert(
            alert_type=alert_type,
            severity=severity,
            message=message,
            context=context,
        )
