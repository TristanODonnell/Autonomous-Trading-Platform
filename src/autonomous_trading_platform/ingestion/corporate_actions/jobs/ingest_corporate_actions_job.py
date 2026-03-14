from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service import (
    CorporateActionIngestionService,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService


class IngestCorporateActionsJob:
    def __init__(
        self,
        session: Session,
        run_id: str,
        audit_logger: AuditLoggingService,
        cycle_timestamp: datetime,
    ) -> None:
        self.session = session
        self.run_id = run_id
        self.audit_logger = audit_logger
        self.cycle_timestamp = cycle_timestamp

    def ingest_corporate_actions_job(self) -> None:
        service = CorporateActionIngestionService(
            session=self.session,
            run_id=self.run_id,
            audit_logger=self.audit_logger,
            cycle_timestamp=self.cycle_timestamp,
        )
        service.ingest_corporate_actions()
