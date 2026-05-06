from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.repositories.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)


@dataclass(frozen=True)
class PaperTradingGoldenPathResult:
    correlation_id: str


class PaperTradingGoldenPathOrchestrator:
    """
    High-level orchestrator for the paper trading golden path.

    v1 scope:
    - runs market ingestion cycle
    - runs trading cycle after ingestion
    - records the parent golden-path runtime job
    - provides correlation_id for runtime chain verification
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.runner = RuntimeJobRunner(
            repository=RuntimeJobRunRepository(session),
        )

    def run(
        self,
        *,
        now_utc: datetime,
    ) -> PaperTradingGoldenPathResult:
        correlation_id = str(uuid4())

        def _run_pipeline_chain() -> None:
            run_market_ingestion_cycle(now_utc=now_utc)
            run_trading_cycle(now_utc=now_utc)

        self.runner.run(
            job_name="paper_trading_golden_path",
            trigger_type="scheduler",
            correlation_id=correlation_id,
            input_summary_json={
                "mode": "full_pipeline",
                "now_utc": now_utc.isoformat(),
                "steps": [
                    "market_ingestion_cycle",
                    "trading_cycle",
                ],
            },
            job=_run_pipeline_chain,
        )

        return PaperTradingGoldenPathResult(
            correlation_id=correlation_id,
        )
