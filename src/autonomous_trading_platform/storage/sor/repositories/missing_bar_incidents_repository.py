from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.missing_bar_incidents import (
    MissingBarIncidents,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class MissingBarIncidentsRepository(BaseRepository):
    def get_by_incident_id(self, incident_id: str) -> MissingBarIncidents | None:
        stmt = select(MissingBarIncidents).where(MissingBarIncidents.incident_id == incident_id)
        return cast(MissingBarIncidents | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: MissingBarIncidents) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[MissingBarIncidents]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: MissingBarIncidents) -> MissingBarIncidents:
        existing = self.get_by_incident_id(row.incident_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in MissingBarIncidents.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_incident_id(self, incident_id: str) -> None:
        obj = self.get_by_incident_id(incident_id)
        if obj is not None:
            self.session.delete(obj)
