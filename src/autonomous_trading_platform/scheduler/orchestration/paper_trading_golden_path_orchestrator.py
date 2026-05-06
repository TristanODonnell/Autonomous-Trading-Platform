from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
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

            latest_raw_bars = (
                self.session.query(DatasetVersions)
                .filter(DatasetVersions.dataset_name == "raw_bars")
                .filter(DatasetVersions.validation_status == "validated")
                .order_by(DatasetVersions.created_at.desc())
                .first()
            )

            if latest_raw_bars is None:
                raise RuntimeError("No raw_bars dataset version found after ingestion")

            run_feature_pipeline_cycle(
                now_utc=now_utc,
                price_basis=PriceBasis.RAW,
                dataset_version_id=latest_raw_bars.dataset_version_id,
                symbols=["SPY"],
                start_date=latest_raw_bars.date_coverage_start,
                end_date=latest_raw_bars.date_coverage_end,
                include_returns=True,
                include_volatility=False,
                include_moving_average=False,
                include_liquidity=False,
                include_regime=False,
            )

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
                    "feature_pipeline_cycle",
                    "trading_cycle",
                ],
            },
            job=_run_pipeline_chain,
        )

        return PaperTradingGoldenPathResult(
            correlation_id=correlation_id,
        )
