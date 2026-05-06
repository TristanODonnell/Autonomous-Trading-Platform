from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
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
    - assumes ingestion + features already exist (seeded or pre-run)
    - runs trading cycle through RuntimeJobRunner
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

        self.runner.run(
            job_name="paper_trading_golden_path",
            trigger_type="scheduler",
            correlation_id=correlation_id,
            input_summary_json={
                "mode": "seeded",
                "now_utc": now_utc.isoformat(),
            },
            job=lambda: run_trading_cycle(now_utc=now_utc),
        )

        return PaperTradingGoldenPathResult(
            correlation_id=correlation_id,
        )
