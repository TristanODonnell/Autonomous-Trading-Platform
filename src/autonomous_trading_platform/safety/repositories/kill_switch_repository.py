from datetime import UTC, datetime

from autonomous_trading_platform.safety.models.kill_switch_state import KillSwitchState


class KillSwitchRepository:
    def __init__(self) -> None:
        self._state = KillSwitchState(
            enabled=False,
            reason=None,
            updated_by=None,
            updated_at=None,
        )

    def get_state(self) -> KillSwitchState:
        return self._state

    def enable(self, *, reason: str, updated_by: str) -> KillSwitchState:
        self._state = KillSwitchState(
            enabled=True,
            reason=reason,
            updated_by=updated_by,
            updated_at=datetime.now(UTC),
        )
        return self._state

    def disable(self, *, reason: str, updated_by: str) -> KillSwitchState:
        self._state = KillSwitchState(
            enabled=False,
            reason=reason,
            updated_by=updated_by,
            updated_at=datetime.now(UTC),
        )
        return self._state
