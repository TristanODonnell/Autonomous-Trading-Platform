from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.safety.errors import RuntimeGateNotArmedError


class RuntimeGateService:
    """
    Manage temporary runtime arming for live trading.

    """

    def __init__(self) -> None:
        self._armed: bool = False
        self._armed_by: str | None = None
        self._reason: str | None = None
        self._armed_at: datetime | None = None
        self._expires_at: datetime | None = None

    def arm(
        self,
        reason: str,
        armed_by: str,
        expires_at: datetime | None = None,
    ) -> None:
        """
        Arm the runtime gate for live trading.

        Args:
            reason: Human-readable reason for arming live trading.
            armed_by: Identifier for the operator arming the gate.
            expires_at: Optional UTC expiration time.
        """
        now = datetime.now(UTC)

        if expires_at is not None and expires_at <= now:
            raise ValueError("expires_at must be in the future.")

        self._armed = True
        self._armed_by = armed_by
        self._reason = reason
        self._armed_at = now
        self._expires_at = expires_at

    def disarm(self) -> None:
        """Disarm the runtime gate."""
        self._armed = False
        self._armed_by = None
        self._reason = None
        self._armed_at = None
        self._expires_at = None

    def is_armed(self) -> bool:
        """
        Return whether the runtime gate is currently open.

        If the arming window has expired, this method automatically disarms
        the gate and returns False.
        """
        if not self._armed:
            return False

        if self._expires_at is not None and datetime.now(UTC) >= self._expires_at:
            self.disarm()
            return False

        return True

    def assert_runtime_gate_open(self) -> None:
        """Raise if live trading is not currently armed."""
        if not self.is_armed():
            raise RuntimeGateNotArmedError("Live trading runtime gate is not armed.")

    def get_status(self) -> dict[str, str | bool | None]:
        """Return the current runtime gate state for inspection/CLI use."""
        return {
            "armed": self.is_armed(),
            "armed_by": self._armed_by,
            "reason": self._reason,
            "armed_at": self._armed_at.isoformat() if self._armed_at else None,
            "expires_at": self._expires_at.isoformat() if self._expires_at else None,
        }
