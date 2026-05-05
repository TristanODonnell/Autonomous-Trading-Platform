from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance

# States that count as "active" for the health endpoint
_ACTIVE_GOVERNANCE_STATES = {"approved_for_paper_trading", "approved_for_live_trading"}


class SystemHealthService:
    """
    Lightweight read-only service for the GET /api/v1/system/health endpoint.
    Must stay fast — this endpoint is polled frequently.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def get_health(self) -> dict:
        active_count = self._count_active_strategies()
        alerts = self._collect_alerts()
        status = self._derive_status(alerts)
        trading_mode = self._settings.trading_environment.value  # "simulation"|"paper"|"live"

        return {
            "status": status,
            "trading_mode": trading_mode,
            "active_strategy_count": active_count,
            "alerts": alerts,
        }

    def _count_active_strategies(self) -> int:
        count = (
            self._session.query(StrategyGovernance)
            .filter(StrategyGovernance.current_state.in_(_ACTIVE_GOVERNANCE_STATES))
            .count()
        )
        return int(count)

    def _collect_alerts(self) -> list[str]:
        alerts: list[str] = []

        # Kill-switch flag from Settings env var (persisted source of truth)
        if self._settings.no_live_trading:
            alerts.append("live_trading_disabled")

        # Shadow mode active — trades are simulated, not submitted
        if self._settings.shadow_mode_enabled:
            alerts.append("shadow_mode_active")

        return alerts

    @staticmethod
    def _derive_status(alerts: list[str]) -> str:
        # Expand this logic as more alert types are added
        if not alerts:
            return "healthy"
        return "degraded"
