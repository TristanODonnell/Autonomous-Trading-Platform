from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
)
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_experiment_pipeline_cycle import (
    run_experiment_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle import (
    run_market_backfill_cycle,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)


@dataclass(frozen=True)
class HistoricalResearchGoldenPathResult:
    correlation_id: str


class HistoricalResearchGoldenPathOrchestrator:
    """
    Historical research golden path:

    backfill raw bars
    -> corporate action ingestion / adjustment
    -> feature pipeline
    -> experiment pipeline (optional — pass experiment_plan to run())
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.runner = RuntimeJobRunner(
            repository=RuntimeJobRunRepository(session),
        )

    def _latest_backfill_raw_dataset(self) -> DatasetVersions:
        row = (
            self.session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == "raw_bars")
            .filter(DatasetVersions.validation_status == "validated")
            .filter(
                DatasetVersions.metadata_json["dataset_type"].as_string() == "historical_backfill"
            )
            .order_by(DatasetVersions.created_at.desc())
            .first()
        )

        if not isinstance(row, DatasetVersions):
            raise RuntimeError("No validated historical raw_bars backfill dataset found")

        return row

    @staticmethod
    def _dataset_symbols(dataset_version: DatasetVersions) -> list[str]:
        source_manifest = dataset_version.source_manifest or {}
        symbols = source_manifest.get("symbols")

        if not isinstance(symbols, list) or not all(isinstance(symbol, str) for symbol in symbols):
            raise RuntimeError(
                f"Dataset {dataset_version.dataset_version_id} does not declare symbols"
            )

        return sorted(symbols)

    def run(
        self,
        *,
        symbols: list[str],
        start: datetime,
        end: datetime,
        now_utc: datetime,
        experiment_plan: ExperimentDefinition | None = None,
    ) -> HistoricalResearchGoldenPathResult:
        correlation_id = str(uuid4())

        def _run_pipeline_chain() -> None:
            run_market_backfill_cycle(
                symbols=symbols,
                start=start,
                end=end,
            )

            raw_dataset = self._latest_backfill_raw_dataset()

            # Capture all values before any cycle closes the session
            raw_version_id = raw_dataset.dataset_version_id
            raw_symbols = self._dataset_symbols(raw_dataset)
            raw_start = raw_dataset.date_coverage_start
            raw_end = raw_dataset.date_coverage_end

            run_corporate_action_ingestion_cycle(
                source_raw_bars_dataset_version_id=raw_version_id,
            )

            # For now, feature pipeline can run on raw until adjusted-bar production is fully wired.
            # Later switch this to adjusted_bars dataset_version_id.
            run_feature_pipeline_cycle(
                now_utc=now_utc,
                price_basis=PriceBasis.RAW,
                dataset_version_id=raw_version_id,
                symbols=raw_symbols,
                start_date=raw_start,
                end_date=raw_end,
                include_returns=True,
                include_volatility=True,
                include_moving_average=True,
                include_liquidity=True,
                include_regime=True,
            )

            if experiment_plan is not None:
                run_experiment_pipeline_cycle(
                    experiment_plan=experiment_plan,
                    now_utc=now_utc,
                )

        steps = [
            "market_backfill_cycle",
            "corporate_action_ingestion_cycle",
            "feature_pipeline_cycle",
        ]
        if experiment_plan is not None:
            steps.append("experiment_pipeline_cycle")

        self.runner.run(
            job_name="historical_research_golden_path",
            trigger_type="scheduler",
            correlation_id=correlation_id,
            input_summary_json={
                "mode": "historical_research",
                "now_utc": now_utc.isoformat(),
                "symbols": symbols,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "steps": steps,
            },
            job=_run_pipeline_chain,
        )

        return HistoricalResearchGoldenPathResult(
            correlation_id=correlation_id,
        )
