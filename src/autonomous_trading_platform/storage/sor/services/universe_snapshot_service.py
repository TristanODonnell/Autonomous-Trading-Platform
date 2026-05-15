# DEPRECATED: UniverseSnapshotService (query service) has been superseded by UniverseVersionQueryService.
# See: storage/sor/services/universe_version_query_service.py

from datetime import date

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.universe_snapshots import UniverseSnapshot
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class UniverseSnapshotService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_snapshot_for_date(self, as_of_date: date) -> UniverseSnapshot | None:
        with SorUnitOfWork(self.session) as uow:
            return uow.universe_snapshots.get_by_snapshot_date(as_of_date)

    def is_symbol_eligible(self, symbol: str, as_of_date: date) -> bool:
        with SorUnitOfWork(self.session) as uow:
            snapshot = uow.universe_snapshots.get_by_snapshot_date(as_of_date)

            if snapshot is None:
                return False

            return symbol in snapshot.symbols
