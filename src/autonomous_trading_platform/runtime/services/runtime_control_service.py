from __future__ import annotations

from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.repositories.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)


class RuntimeControlService:
    def __init__(self, repository: RuntimeControlStateRepository) -> None:
        self.repository = repository

    def get_state(self) -> RuntimeControlState:
        return self.repository.get_or_create_global_state()

    def start_trading(self, *, reason: str | None = None, updated_by: str) -> RuntimeControlState:
        state = self.repository.set_trading_enabled(
            enabled=True,
            reason=reason,
            updated_by=updated_by,
        )
        state.trading_paused = False
        self.repository.session.flush()
        return state

    def stop_trading(self, *, reason: str | None = None, updated_by: str) -> RuntimeControlState:
        return self.repository.set_trading_enabled(
            enabled=False,
            reason=reason,
            updated_by=updated_by,
        )

    def pause_trading(self, *, reason: str | None = None, updated_by: str) -> RuntimeControlState:
        return self.repository.set_trading_paused(
            paused=True,
            reason=reason,
            updated_by=updated_by,
        )

    def resume_trading(self, *, reason: str | None = None, updated_by: str) -> RuntimeControlState:
        return self.repository.set_trading_paused(
            paused=False,
            reason=reason,
            updated_by=updated_by,
        )

    def enable_kill_switch(
        self,
        *,
        reason: str | None = None,
        updated_by: str,
    ) -> RuntimeControlState:
        return self.repository.set_kill_switch(
            enabled=True,
            reason=reason,
            updated_by=updated_by,
        )

    def disable_kill_switch(
        self,
        *,
        reason: str | None = None,
        updated_by: str,
    ) -> RuntimeControlState:
        return self.repository.set_kill_switch(
            enabled=False,
            reason=reason,
            updated_by=updated_by,
        )

    def update_trading_mode(
        self,
        *,
        trading_mode: str,
        reason: str | None = None,
        updated_by: str,
    ) -> RuntimeControlState:
        return self.repository.set_trading_mode(
            trading_mode=trading_mode,
            reason=reason,
            updated_by=updated_by,
        )

    def get_cycle_block_reason(self, *, expected_trading_mode: str) -> str | None:
        state = self.get_state()

        if state.kill_switch_enabled:
            return "kill_switch_enabled"

        if not state.trading_enabled:
            return "trading_disabled"

        if state.trading_paused:
            return "trading_paused"

        if state.trading_mode != expected_trading_mode:
            return "trading_mode_mismatch"

        return None
