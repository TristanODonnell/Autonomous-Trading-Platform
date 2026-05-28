from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.safety.errors import KillSwitchEnabledError
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


class KillSwitchService:
    """
    Manage the live-trading kill switch backed by the SOR.

    State is persisted on every enable/disable call and read directly
    from the database on every check, so it survives service restarts,
    deploys, and scheduler restarts.

    runtime_control_repo is provided so that RuntimeControlState.kill_switch_enabled
    stays in sync with the dedicated KillSwitchState table, allowing the
    trading-cycle block check (get_cycle_block_reason) to continue reading
    from RuntimeControlState without any structural changes.
    """

    def __init__(
        self,
        repository: KillSwitchStateRepository,
        runtime_control_repo: RuntimeControlStateRepository | None = None,
    ) -> None:
        self._repo = repository
        self._runtime_control_repo = runtime_control_repo

    def enable(self, reason: str, updated_by: str) -> None:
        """Enable the kill switch and block live trading."""
        at = datetime.now(UTC)
        self._repo.enable(reason=reason, updated_by=updated_by, updated_at=at)
        if self._runtime_control_repo is not None:
            self._runtime_control_repo.set_kill_switch(
                enabled=True,
                reason=reason,
                updated_by=updated_by,
            )

    def disable(self, reason: str, updated_by: str) -> None:
        """Disable the kill switch and allow live trading to proceed."""
        at = datetime.now(UTC)
        self._repo.disable(reason=reason, cleared_by=updated_by, cleared_at=at)
        if self._runtime_control_repo is not None:
            self._runtime_control_repo.set_kill_switch(
                enabled=False,
                reason=reason,
                updated_by=updated_by,
            )

    def is_enabled(self) -> bool:
        """Return whether the kill switch is currently enabled (reads from SOR)."""
        return bool(self._repo.get_current_state().is_enabled)

    def assert_not_enabled(self) -> None:
        """Raise KillSwitchEnabledError if the kill switch is enabled."""
        state = self._repo.get_current_state()
        if state.is_enabled:
            reason_suffix = f" Reason: {state.reason}" if state.reason else ""
            raise KillSwitchEnabledError(
                f"Live trading blocked by external kill switch.{reason_suffix}"
            )

    def get_status(self) -> dict[str, str | bool | None]:
        """Return the current kill-switch state for inspection/CLI use."""
        state = self._repo.get_current_state()
        return {
            "enabled": state.is_enabled,
            "reason": state.reason,
            "updated_by": state.updated_by,
            "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            "cleared_by": state.cleared_by,
            "cleared_at": state.cleared_at.isoformat() if state.cleared_at else None,
        }

    def emit_startup_audit_event(self, *, audit_repo) -> None:
        """
        Emit a KILL_SWITCH_STATE_LOADED audit event reflecting the state read
        from the SOR at service startup.  Call once after construction.
        """
        state = self._repo.get_current_state()
        now = datetime.now(UTC)
        audit_repo.record_operator_action(
            action="KILL_SWITCH_STATE_LOADED",
            actor="system",
            reason="startup state load",
            occurred_at=now,
            metadata={
                "is_enabled": state.is_enabled,
                "kill_switch_reason": state.reason,
                "updated_by": state.updated_by,
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
                "cleared_by": state.cleared_by,
                "cleared_at": state.cleared_at.isoformat() if state.cleared_at else None,
            },
            component="safety",
        )
