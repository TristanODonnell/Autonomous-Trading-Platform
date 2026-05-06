from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository

GLOBAL_CONTROL_ID = "global"


class RuntimeControlStateRepository(BaseRepository):
    def get_global_state(self) -> RuntimeControlState | None:
        stmt = select(RuntimeControlState).where(
            RuntimeControlState.control_id == GLOBAL_CONTROL_ID
        )
        return cast(
            RuntimeControlState | None,
            self.session.scalars(stmt).one_or_none(),
        )

    def get_or_create_global_state(self) -> RuntimeControlState:
        existing = self.get_global_state()

        if existing is not None:
            return existing

        row = RuntimeControlState(
            control_id=GLOBAL_CONTROL_ID,
            trading_enabled=True,
            trading_paused=False,
            kill_switch_enabled=False,
            trading_mode="paper",
            reason=None,
            updated_by=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        self.session.add(row)
        self.session.flush()

        return row

    def save(self, row: RuntimeControlState) -> RuntimeControlState:
        existing = self.get_global_state()

        if existing is None:
            self.session.add(row)
            self.session.flush()
            return row

        for column in RuntimeControlState.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        self.session.flush()
        return existing

    def set_trading_enabled(
        self,
        *,
        enabled: bool,
        reason: str | None,
        updated_by: str,
    ) -> RuntimeControlState:
        state = self.get_or_create_global_state()
        state.trading_enabled = enabled
        state.reason = reason
        state.updated_by = updated_by
        state.updated_at = datetime.now(UTC)

        self.session.flush()
        return state

    def set_trading_paused(
        self,
        *,
        paused: bool,
        reason: str | None,
        updated_by: str,
    ) -> RuntimeControlState:
        state = self.get_or_create_global_state()
        state.trading_paused = paused
        state.reason = reason
        state.updated_by = updated_by
        state.updated_at = datetime.now(UTC)

        self.session.flush()
        return state

    def set_kill_switch(
        self,
        *,
        enabled: bool,
        reason: str | None,
        updated_by: str,
    ) -> RuntimeControlState:
        state = self.get_or_create_global_state()
        state.kill_switch_enabled = enabled
        state.reason = reason
        state.updated_by = updated_by
        state.updated_at = datetime.now(UTC)

        self.session.flush()
        return state

    def set_trading_mode(
        self,
        *,
        trading_mode: str,
        reason: str | None,
        updated_by: str,
    ) -> RuntimeControlState:
        state = self.get_or_create_global_state()
        state.trading_mode = trading_mode
        state.reason = reason
        state.updated_by = updated_by
        state.updated_at = datetime.now(UTC)

        self.session.flush()
        return state
