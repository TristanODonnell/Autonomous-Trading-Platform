from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service import (
    CorporateActionIngestionService,
)


class IngestCorporateActionsJob:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ingest_corporate_actions_job(self) -> None:
        service = CorporateActionIngestionService(self.session)
        service.ingest_corporate_actions()
