from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.missing_bar_incidents import MissingBarIncidents
from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import SymbolDateCoverage
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class IngestionQualityRecorderService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_symbol_date_quality(
        self,
        coverage_rows: list[SymbolDateCoverage],
        incidents: list[MissingBarIncidents],
    ) -> None:
        with SorUnitOfWork(self.session) as uow:
            for row in coverage_rows:
                uow.symbol_date_coverage.upsert(row)

            if incidents:
                uow.missing_bar_incidents.insert_many(incidents)
