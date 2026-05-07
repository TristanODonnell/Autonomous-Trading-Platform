from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle import (
    run_corporate_action_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_ingestion_cycle import (
    run_market_ingestion_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
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
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.runner = RuntimeJobRunner(
            repository=RuntimeJobRunRepository(session),
        )

    def _get_active_daily_raw_bars_dataset(self, *, now_utc: datetime) -> DatasetVersions | None:
        trading_date = now_utc.date()
        row = (
            self.session.query(DatasetVersions)
            .filter(DatasetVersions.dataset_name == "raw_bars")
            .filter(DatasetVersions.price_basis == PriceBasis.RAW.value)
            .filter(DatasetVersions.interval == BarInterval.FIVE_MIN.value)
            .filter(DatasetVersions.validation_status == "validated")
            .filter(DatasetVersions.date_coverage_start == trading_date)
            .filter(DatasetVersions.date_coverage_end == trading_date)
            .filter(
                DatasetVersions.metadata_json["dataset_lifecycle"].as_string()
                == "active_daily_incremental"
            )
            .order_by(DatasetVersions.created_at.desc())
            .first()
        )

        return row if isinstance(row, DatasetVersions) else None

    @staticmethod
    def _dataset_symbols(dataset_version: DatasetVersions) -> list[str]:
        source_manifest = dataset_version.source_manifest or {}
        symbols = source_manifest.get("symbols")
        if not isinstance(symbols, list) or not all(
            isinstance(symbol, str) and symbol for symbol in symbols
        ):
            raise RuntimeError(
                f"raw_bars dataset {dataset_version.dataset_version_id} does not declare symbols"
            )
        return sorted(symbols)

    def run_intraday_tick(
        self,
        *,
        now_utc: datetime,
    ) -> PaperTradingGoldenPathResult:
        correlation_id = str(uuid4())

        def _run_pipeline_chain() -> None:
            run_market_ingestion_cycle(now_utc=now_utc)

            latest_raw_bars = self._get_active_daily_raw_bars_dataset(now_utc=now_utc)

            if latest_raw_bars is None:
                raise RuntimeError("No raw_bars dataset version found after ingestion")

            run_feature_pipeline_cycle(
                now_utc=now_utc,
                price_basis=PriceBasis.RAW,
                dataset_version_id=latest_raw_bars.dataset_version_id,
                symbols=self._dataset_symbols(latest_raw_bars),
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
            job_name="paper_trading_intraday_tick",
            trigger_type="scheduler",
            correlation_id=correlation_id,
            input_summary_json={
                "mode": "intraday_tick",
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

    def run(
        self,
        *,
        now_utc: datetime,
    ) -> PaperTradingGoldenPathResult:
        correlation_id = str(uuid4())

        def _run_pipeline_chain() -> None:
            run_market_ingestion_cycle(now_utc=now_utc)

            latest_raw_bars = self._get_active_daily_raw_bars_dataset(now_utc=now_utc)

            if latest_raw_bars is None:
                raise RuntimeError("No raw_bars dataset version found after ingestion")

            run_feature_pipeline_cycle(
                now_utc=now_utc,
                price_basis=PriceBasis.RAW,
                dataset_version_id=latest_raw_bars.dataset_version_id,
                symbols=self._dataset_symbols(latest_raw_bars),
                start_date=latest_raw_bars.date_coverage_start,
                end_date=latest_raw_bars.date_coverage_end,
                include_returns=True,
                include_volatility=False,
                include_moving_average=False,
                include_liquidity=False,
                include_regime=False,
            )

            run_trading_cycle(now_utc=now_utc)

            run_corporate_action_ingestion_cycle()

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
                    "corporate_action_ingestion_cycle",
                ],
            },
            job=_run_pipeline_chain,
        )

        return PaperTradingGoldenPathResult(
            correlation_id=correlation_id,
        )

    def run_eod_maintenance(
        self,
        *,
        now_utc: datetime,
    ) -> PaperTradingGoldenPathResult:
        correlation_id = str(uuid4())

        def _run_pipeline_chain() -> None:
            latest_raw_bars = self._get_active_daily_raw_bars_dataset(
                now_utc=now_utc,
            )

            if latest_raw_bars is None:
                raise RuntimeError("No active daily raw_bars dataset found for EOD maintenance")

            # Capture all values before any cycle closes the session
            raw_bars_version_id = latest_raw_bars.dataset_version_id
            raw_bars_symbols = self._dataset_symbols(latest_raw_bars)
            raw_bars_start = latest_raw_bars.date_coverage_start
            raw_bars_end = latest_raw_bars.date_coverage_end
            raw_bars_interval = latest_raw_bars.interval
            raw_bars_coverage = latest_raw_bars.symbol_coverage

            run_corporate_action_ingestion_cycle(
                source_raw_bars_dataset_version_id=raw_bars_version_id,
            )

            adjusted_bars_version_id = generate_dataset_version("adjusted_bars")
            self.session.add(
                DatasetVersions(
                    dataset_version_id=adjusted_bars_version_id,
                    dataset_name="adjusted_bars",
                    created_at=now_utc,
                    source="corporate_action_ingestion",
                    price_basis=PriceBasis.ADJUSTED,
                    interval=raw_bars_interval,
                    schema_version="1.0.0",
                    symbol_coverage=raw_bars_coverage,
                    date_coverage_start=raw_bars_start,
                    date_coverage_end=raw_bars_end,
                    validation_status="validated",
                    checksum=None,
                    source_dataset_version=raw_bars_version_id,
                    source_manifest={
                        "source_raw_bars_version": raw_bars_version_id,
                        "pipeline": "corporate_action_adjustment",
                    },
                    metadata_json={
                        "price_basis": PriceBasis.ADJUSTED.value,
                        "trading_date": raw_bars_end.isoformat() if raw_bars_end else None,
                    },
                )
            )
            self.session.flush()

            run_feature_pipeline_cycle(
                now_utc=now_utc,
                price_basis=PriceBasis.ADJUSTED,
                dataset_version_id=adjusted_bars_version_id,
                symbols=raw_bars_symbols,
                start_date=raw_bars_start,
                end_date=raw_bars_end,
                include_returns=True,
                include_volatility=False,
                include_moving_average=False,
                include_liquidity=False,
                include_regime=False,
            )

            features_version_id = generate_dataset_version("features")
            self.session.add(
                DatasetVersions(
                    dataset_version_id=features_version_id,
                    dataset_name="features",
                    created_at=now_utc,
                    source="feature_pipeline",
                    price_basis=PriceBasis.ADJUSTED,
                    interval=raw_bars_interval,
                    schema_version="1.0.0",
                    symbol_coverage=raw_bars_coverage,
                    date_coverage_start=raw_bars_start,
                    date_coverage_end=raw_bars_end,
                    validation_status="validated",
                    checksum=None,
                    source_dataset_version=adjusted_bars_version_id,
                    source_manifest={
                        "source_adjusted_bars_version": adjusted_bars_version_id,
                        "pipeline": "feature_pipeline",
                    },
                    metadata_json={
                        "price_basis": PriceBasis.ADJUSTED.value,
                        "source_adjusted_bars_version": adjusted_bars_version_id,
                    },
                )
            )
            self.session.flush()

        self.runner.run(
            job_name="paper_trading_eod_maintenance",
            trigger_type="scheduler",
            correlation_id=correlation_id,
            input_summary_json={
                "mode": "eod_maintenance",
                "now_utc": now_utc.isoformat(),
                "steps": [
                    "corporate_action_ingestion_cycle",
                    "feature_pipeline_cycle",
                ],
            },
            job=_run_pipeline_chain,
        )

        return PaperTradingGoldenPathResult(
            correlation_id=correlation_id,
        )
