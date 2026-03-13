from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.corporate_actions.jobs.ingest_corporate_actions_job import (
    IngestCorporateActionsJob,
)
from src.db import get_session


def run_corporate_action_ingestion_cycle() -> None:
    """
    Entry point for the Airflow DAG.
    """
    session: Session = get_session()

    try:
        job = IngestCorporateActionsJob(session=session)
        job.ingest_corporate_actions_job()
    finally:
        session.close()
