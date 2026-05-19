from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.universe_rotation_records import (
    UniverseRotationRecord,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class UniverseRotationRepository(BaseRepository):
    def insert_record(self, row: UniverseRotationRecord) -> None:
        self.session.add(row)

    def get_by_rotation_id(self, rotation_id: str) -> UniverseRotationRecord | None:
        stmt = select(UniverseRotationRecord).where(
            UniverseRotationRecord.rotation_id == rotation_id
        )
        return cast(
            UniverseRotationRecord | None,
            self.session.execute(stmt).scalar_one_or_none(),
        )

    def get_latest(self) -> UniverseRotationRecord | None:
        stmt = (
            select(UniverseRotationRecord)
            .order_by(UniverseRotationRecord.created_at.desc())
            .limit(1)
        )
        return cast(
            UniverseRotationRecord | None,
            self.session.execute(stmt).scalars().first(),
        )

    def get_recent(self, limit: int = 20) -> list[UniverseRotationRecord]:
        stmt = (
            select(UniverseRotationRecord)
            .order_by(UniverseRotationRecord.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_for_version(self, version_id: str) -> list[UniverseRotationRecord]:
        stmt = (
            select(UniverseRotationRecord)
            .where(UniverseRotationRecord.new_universe_version_id == version_id)
            .order_by(UniverseRotationRecord.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_in_window(self, start: datetime, end: datetime) -> list[UniverseRotationRecord]:
        """Return rotation records with created_at in [start, end), ordered chronologically."""
        stmt = (
            select(UniverseRotationRecord)
            .where(
                UniverseRotationRecord.created_at >= start,
                UniverseRotationRecord.created_at < end,
            )
            .order_by(UniverseRotationRecord.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_for_previous_version(self, version_id: str) -> list[UniverseRotationRecord]:
        stmt = (
            select(UniverseRotationRecord)
            .where(UniverseRotationRecord.previous_universe_version_id == version_id)
            .order_by(UniverseRotationRecord.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())
