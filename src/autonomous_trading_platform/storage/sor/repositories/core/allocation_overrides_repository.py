from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import or_, select

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
        """Return the active, non-expired override for a strategy, or None."""
        if now is None:
            now = datetime.now(tz=UTC)

        stmt = select(AllocationOverrides).where(
            AllocationOverrides.strategy_id == strategy_id,
            AllocationOverrides.is_active.is_(True),
            or_(
                AllocationOverrides.expires_at.is_(None),
                AllocationOverrides.expires_at > now,
            ),
        )
        return cast(AllocationOverrides | None, self.session.scalars(stmt).one_or_none())

    def get_all_active_non_expired(
        self,
        now: UTCDateTime | None = None,
    ) -> list[AllocationOverrides]:
        """Return all active, non-expired overrides across all strategies.

        Ordered by created_at DESC so the most-recent row wins when callers
        use setdefault() to build a per-strategy dict.
        """
        if now is None:
            now = datetime.now(tz=UTC)
        return list(
            self.session.scalars(
                select(AllocationOverrides)
                .where(
                    AllocationOverrides.is_active.is_(True),
                    or_(
                        AllocationOverrides.expires_at.is_(None),
                        AllocationOverrides.expires_at > now,
                    ),
                )
                .order_by(
                    AllocationOverrides.created_at.desc(),
                    AllocationOverrides.override_id.asc(),
                )
            ).all()
        )

    def get_all_expired_active(
        self,
        now: UTCDateTime | None = None,
    ) -> list[AllocationOverrides]:
        """Return overrides that are still marked is_active=True but have passed their expires_at."""
        if now is None:
            now = datetime.now(tz=UTC)
        return list(
            self.session.scalars(
                select(AllocationOverrides).where(
                    AllocationOverrides.is_active.is_(True),
                    AllocationOverrides.expires_at.isnot(None),
                    AllocationOverrides.expires_at <= now,
                )
            ).all()
        )

    def create_override(self, row: AllocationOverrides, now: UTCDateTime | None = None) -> None:
        """Persist a new override.

        Raises if a non-expired active override already exists.  Silently
        deactivates any expired-but-still-active records so the
        one-active-per-strategy invariant is maintained after creation.
        """
        if now is None:
            now = datetime.now(tz=UTC)
        existing = self.get_active_override(row.strategy_id, now=now)
        if existing is not None:
            raise ValueError(
                f"Active override already exists for strategy '{row.strategy_id}' "
                f"(override_id={existing.override_id}). "
                "Call deactivate_override() before creating a new one."
            )
        for stale in self._get_expired_active_for_strategy(row.strategy_id, now=now):
            stale.is_active = False
        self.session.add(row)

    def deactivate_override(self, strategy_id: str) -> bool:
        """Set is_active=False on all active overrides for a strategy (including expired ones).

        Returns True if at least one record was deactivated.
        """
        rows = list(
            self.session.scalars(
                select(AllocationOverrides).where(
                    AllocationOverrides.strategy_id == strategy_id,
                    AllocationOverrides.is_active.is_(True),
                )
            ).all()
        )
        if not rows:
            return False
        for row in rows:
            row.is_active = False
        return True

    def get_all_active(self) -> list[AllocationOverrides]:
        stmt = select(AllocationOverrides).where(AllocationOverrides.is_active.is_(True))
        return list(self.session.scalars(stmt).all())

    def _get_expired_active_for_strategy(
        self,
        strategy_id: str,
        now: datetime,
    ) -> list[AllocationOverrides]:
        """Return is_active=True records for a strategy that have passed their expires_at."""
        return list(
            self.session.scalars(
                select(AllocationOverrides).where(
                    AllocationOverrides.strategy_id == strategy_id,
                    AllocationOverrides.is_active.is_(True),
                    AllocationOverrides.expires_at.isnot(None),
                    AllocationOverrides.expires_at <= now,
                )
            ).all()
        )
