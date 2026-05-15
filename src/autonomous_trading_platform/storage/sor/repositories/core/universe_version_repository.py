# autonomous_trading_platform/storage/sor/repositories/core/universe_version_repository.py

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.enums import UniverseStatus
from autonomous_trading_platform.contracts.validators.universe_version import is_valid_transition
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class ImmutableVersionError(Exception):
    """Raised when a mutation is attempted on an active UniverseVersion."""


class InvalidStatusTransitionError(Exception):
    """Raised when an invalid lifecycle transition is attempted."""


class UniverseVersionRepository(BaseRepository):
    def get_by_version_id(self, version_id: str) -> UniverseVersion | None:
        stmt = select(UniverseVersion).where(UniverseVersion.universe_version_id == version_id)
        return cast(UniverseVersion | None, self.session.execute(stmt).scalar_one_or_none())

    def get_active_version(self, as_of: datetime) -> UniverseVersion | None:
        stmt = select(UniverseVersion).where(
            UniverseVersion.status == UniverseStatus.ACTIVE,
            UniverseVersion.effective_from <= as_of,
            (UniverseVersion.effective_to.is_(None)) | (UniverseVersion.effective_to > as_of),
        )
        return cast(UniverseVersion | None, self.session.execute(stmt).scalar_one_or_none())

    def get_previous_version_before(self, as_of: datetime) -> UniverseVersion | None:
        stmt = (
            select(UniverseVersion)
            .where(
                UniverseVersion.status == UniverseStatus.ACTIVE,
                UniverseVersion.effective_from < as_of,
            )
            .order_by(UniverseVersion.effective_from.desc())
        )
        return cast(UniverseVersion | None, self.session.execute(stmt).scalars().first())

    def get_members(self, version_id: str) -> list[UniverseMember]:
        stmt = (
            select(UniverseMember)
            .where(UniverseMember.universe_version_id == version_id)
            .order_by(UniverseMember.rank.asc().nullsfirst(), UniverseMember.symbol.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_symbols(self, version_id: str) -> list[str]:
        members = self.get_members(version_id)
        return [m.symbol for m in members]

    def insert_version(self, row: UniverseVersion) -> None:
        if row.status == UniverseStatus.ACTIVE:
            raise ValueError(
                "Cannot insert a version with status 'active' directly. "
                "Insert as 'candidate' then call activate_version()."
            )
        self.session.add(row)

    def insert_members(self, rows: list[UniverseMember]) -> None:
        if not rows:
            return
        version_id = rows[0].universe_version_id
        version = self.get_by_version_id(version_id)
        if version is not None and version.status == UniverseStatus.ACTIVE:
            raise ImmutableVersionError(
                f"Cannot insert members into active universe version '{version_id}'. "
                "Universe versions are immutable after activation."
            )
        self.session.add_all(rows)

    def activate_version(self, version_id: str) -> UniverseVersion:
        version = self.get_by_version_id(version_id)
        if version is None:
            raise ValueError(f"Universe version '{version_id}' not found.")

        if version.status == UniverseStatus.ACTIVE:
            return version

        if not is_valid_transition(version.status, UniverseStatus.ACTIVE):
            raise InvalidStatusTransitionError(
                f"Cannot transition universe version from '{version.status}' to 'active'. "
                f"Valid transitions from '{version.status}': "
                f"{', '.join(sorted(_allowed_transitions(version.status)))}"
            )

        members = self.get_members(version_id)
        if not members:
            raise ValueError(
                f"Cannot activate universe version '{version_id}': it has no members. "
                "A universe version must have at least one member symbol."
            )

        version.status = UniverseStatus.ACTIVE
        return version

    def retire_active_version(self, retired_at: datetime) -> UniverseVersion | None:
        version = self._get_current_active_version()
        if version is None:
            return None

        version.effective_to = retired_at
        version.status = UniverseStatus.RETIRED
        return version

    def _get_current_active_version(self) -> UniverseVersion | None:
        stmt = select(UniverseVersion).where(
            UniverseVersion.status == UniverseStatus.ACTIVE,
            UniverseVersion.effective_to.is_(None),
        )
        return cast(UniverseVersion | None, self.session.execute(stmt).scalar_one_or_none())

    def _guard_not_active(self, version_id: str) -> None:
        version = self.get_by_version_id(version_id)
        if version is not None and version.status == UniverseStatus.ACTIVE:
            raise ImmutableVersionError(
                f"Universe version '{version_id}' is active and cannot be mutated. "
                "Universe versions are immutable after activation."
            )


def _allowed_transitions(status: str) -> set[str]:
    from autonomous_trading_platform.contracts.validators.universe_version import (
        _VALID_STATUS_TRANSITIONS,
    )

    return _VALID_STATUS_TRANSITIONS.get(status, set())
