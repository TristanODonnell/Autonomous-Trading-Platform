from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from autonomous_trading_platform.storage.sor.models.kill_switch_state import (
    KILL_SWITCH_SINGLETON_ID,
    KillSwitchState,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class KillSwitchStateRepository(BaseRepository):
    def get_current_state(self) -> KillSwitchState:
        row = cast(
            KillSwitchState | None, self.session.get(KillSwitchState, KILL_SWITCH_SINGLETON_ID)
        )
        if row is None:
            row = KillSwitchState(
                id=KILL_SWITCH_SINGLETON_ID,
                is_enabled=False,
                reason=None,
                updated_by=None,
                updated_at=None,
                cleared_by=None,
                cleared_at=None,
                created_at=datetime.now(UTC),
            )
            self.session.add(row)
            self.session.flush()
        return row

    def enable(
        self,
        *,
        reason: str,
        updated_by: str,
        updated_at: datetime,
    ) -> KillSwitchState:
        state = self.get_current_state()
        state.is_enabled = True
        state.reason = reason
        state.updated_by = updated_by
        state.updated_at = updated_at
        state.cleared_by = None
        state.cleared_at = None
        self.session.flush()
        return state

    def disable(
        self,
        *,
        reason: str,
        cleared_by: str,
        cleared_at: datetime,
    ) -> KillSwitchState:
        state = self.get_current_state()
        state.is_enabled = False
        state.reason = reason
        state.updated_by = cleared_by
        state.updated_at = cleared_at
        state.cleared_by = cleared_by
        state.cleared_at = cleared_at
        self.session.flush()
        return state
