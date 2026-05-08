from datetime import UTC
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class AllocationOverridesRepository(BaseRepository):
    def get_by_override_id(self, override_id: str) -> AllocationOverrides | None:
        stmt = select(AllocationOverrides).where(AllocationOverrides.override_id == override_id)
        return cast(AllocationOverrides | None, self.session.scalars(stmt).one_or_none())

    def get_active_override(
        self,
        strategy_id: str,
        now: UTCDateTime | None = None,
    ) -> AllocationOverrides | None:

        from datetime import datetime

        if now is None:
            now = datetime.now(tz=UTC)

        stmt = select(AllocationOverrides).where(
            AllocationOverrides.strategy_id == strategy_id,
            AllocationOverrides.is_active.is_(True),
        )
        override = cast(
            AllocationOverrides | None,
            self.session.scalars(stmt).one_or_none(),
        )

        if override is None:
            return None

        # Treat as inactive if it has passed its expiry
        if override.expires_at is not None and override.expires_at <= now:
            return None

        return override

    def create_override(self, row: AllocationOverrides) -> None:

        existing = self.get_active_override(row.strategy_id)
        if existing is not None:
            raise ValueError(
                f"Active override already exists for strategy '{row.strategy_id}' "
                f"(override_id={existing.override_id}). "
                "Call deactivate_override() before creating a new one."
            )
        self.session.add(row)

    def deactivate_override(self, strategy_id: str) -> bool:
        stmt = select(AllocationOverrides).where(
            AllocationOverrides.strategy_id == strategy_id,
            AllocationOverrides.is_active.is_(True),
        )
        override = cast(
            AllocationOverrides | None,
            self.session.scalars(stmt).one_or_none(),
        )
        if override is None:
            return False

        override.is_active = False
        return True

    def get_all_active(self) -> list[AllocationOverrides]:
        stmt = select(AllocationOverrides).where(AllocationOverrides.is_active.is_(True))
        return list(self.session.scalars(stmt).all())
