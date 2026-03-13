from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job import (
    IngestBarsJob,
)
from src.db import get_session


def floor_to_five_minutes(timestamp: datetime) -> datetime:
    minute = (timestamp.minute // 5) * 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def run_market_ingestion_cycle() -> None:
    """
    Entry point for the Airflow DAG.
    """
    session: Session = get_session()

    try:
        expected_symbols = {"SPY"}

        now_utc = datetime.now(UTC)
        cycle_end = floor_to_five_minutes(now_utc)
        cycle_start = cycle_end - timedelta(minutes=5)

        job = IngestBarsJob(
            expected_symbols=expected_symbols,
            session=session,
        )

        job.run_once(start=cycle_start, end=cycle_end)
    finally:
        session.close()
