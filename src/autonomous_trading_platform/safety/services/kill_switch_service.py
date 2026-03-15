from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.safety.errors import KillSwitchEnabledError


class KillSwitchService:
    """
    Manage the live-trading kill switch.

    """

    def __init__(self) -> None:
        self._enabled: bool = False
        self._reason: str | None = None
        self._updated_by: str | None = None
        self._updated_at: datetime | None = None

    def enable(self, reason: str, updated_by: str) -> None:
        """
        Enable the kill switch and block live trading.
        """
        self._enabled = True
        self._reason = reason
        self._updated_by = updated_by
        self._updated_at = datetime.now(UTC)

    def disable(self, reason: str, updated_by: str) -> None:
        """
        Disable the kill switch and allow live trading to proceed if other
        gates also pass.
        """
        self._enabled = False
        self._reason = reason
        self._updated_by = updated_by
        self._updated_at = datetime.now(UTC)

    def is_enabled(self) -> bool:
        """Return whether the kill switch is currently enabled."""
        return self._enabled

    def assert_not_enabled(self) -> None:
        """Raise if the kill switch is enabled."""
        if self.is_enabled():
            reason_suffix = f" Reason: {self._reason}" if self._reason else ""
            raise KillSwitchEnabledError(
                f"Live trading blocked by external kill switch.{reason_suffix}"
            )

    def get_status(self) -> dict[str, str | bool | None]:
        """Return the current kill-switch state for inspection/CLI use."""
        return {
            "enabled": self._enabled,
            "reason": self._reason,
            "updated_by": self._updated_by,
            "updated_at": self._updated_at.isoformat() if self._updated_at else None,
        }
