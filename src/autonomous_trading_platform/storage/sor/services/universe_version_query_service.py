# autonomous_trading_platform/storage/sor/services/universe_version_query_service.py

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class UniverseVersionQueryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_symbols_for_date(self, as_of_date: date) -> list[str]:
        as_of_dt = datetime.combine(as_of_date, time.min, tzinfo=UTC)
        with SorUnitOfWork(self.session) as uow:
            version = uow.universe_versions.get_active_version(as_of_dt)
            if version is None:
                return []
            return uow.universe_versions.get_symbols(version.universe_version_id)

    def is_symbol_eligible(self, symbol: str, as_of_date: date) -> bool:
        return symbol in self.get_symbols_for_date(as_of_date)
