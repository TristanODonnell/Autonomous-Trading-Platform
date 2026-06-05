from __future__ import annotations

import asyncio
import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.contracts.runtime.ingestion_run import IngestionRun
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.ingestion.market_data.clients.alpaca_historical_bars_client import (
    AlpacaHistoricalBarsClient,
)
from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
    get_stock_historical_client,
)
from autonomous_trading_platform.ingestion.market_data.jobs.backfill_market_bars_job import (
    BackfillMarketBarsJob,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    CycleMetricSet,
    StepMetricSet,
    record_cycle_completed,
    record_cycle_failed,
    record_cycle_started,
    record_step_completed,
    record_step_failed,
    record_step_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    market_backfill_cycle_duration,
    market_backfill_cycle_failures,
    market_backfill_cycle_runs,
    market_backfill_cycle_step_duration,
    market_backfill_cycle_step_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.runtime.services.ingestion_run_registration_service import (
    IngestionRunRegistrationService,
)
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

logger = get_logger(__name__)
MARKET_BACKFILL_CYCLE_METRICS = CycleMetricSet(
    runs=market_backfill_cycle_runs,
    failures=market_backfill_cycle_failures,
    duration=market_backfill_cycle_duration,
)
MARKET_BACKFILL_STEP_METRICS = StepMetricSet(
    runs=market_backfill_cycle_step_runs,
    duration=market_backfill_cycle_step_duration,
)


def run_market_backfill_cycle(
    symbols: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    trigger_type: str = "scheduler",
    actor: str | None = None,
) -> dict[str, object]:
    """
    Entry point for the Airflow historical market-data backfill DAG.
    """
    now = datetime.now(UTC)
    cycle_wall_start = perf_counter()

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)
    dataset_registration_service = DatasetRegistrationService(session=session)
    ingestion_run_registration_service = IngestionRunRegistrationService(session=session)

    run_id = uuid.uuid4()
    ingestion_run_id = uuid.uuid4()

    dataset_version_id = generate_dataset_version("raw_bars")
    ingestion_run: IngestionRun | None = None
    dataset_version: DatasetVersion | None = None
    component = "scheduler.run_market_backfill_cycle"
    runtime_job_run_repository = RuntimeJobRunRepository(session)
    job_run_id = str(run_id)
    job_started_at = now

    def _save_runtime_job_run(
        *,
        status: str,
        completed_at: datetime | None,
        error_message: str | None,
        output_summary_json: dict | None,
    ) -> None:
        runtime_job_run_repository.save(
            RuntimeJobRun(
                job_run_id=job_run_id,
                job_name="market_backfill_cycle",
                parent_job_run_id=None,
                status=status,
                trigger_type=trigger_type,
                started_at=job_started_at,
                completed_at=completed_at,
                duration_ms=(
                    int((perf_counter() - cycle_wall_start) * 1000)
                    if completed_at is not None
                    else None
                ),
                error_message=error_message,
                correlation_id=str(run_id),
                input_summary_json={
                    "component": component,
                    "dataset_name": RAW_BARS_DATASET.dataset_key,
                    "price_basis": PriceBasis.RAW.value,
                    "interval": BarInterval.FIVE_MIN.value,
                    "symbols": symbols,
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None,
                    "trigger_type": trigger_type,
                    "actor": actor,
                },
                output_summary_json=output_summary_json,
            )
        )

    base_metadata: dict[str, object] = {}

    record_cycle_started(
        logger=logger,
        metrics=MARKET_BACKFILL_CYCLE_METRICS,
        component=component,
        run_id=str(run_id),
    )

    try:
        if symbols is None:
            symbols = [
                "SPY",
                "AAPL",
                "MSFT",
                "NVDA",
                "AMZN",
                "META",
                "GOOGL",
                "TSLA",
            ]

        if end is None:
            end = now
        if start is None:
            start = end - timedelta(days=30)

        _save_runtime_job_run(
            status="running",
            completed_at=None,
            error_message=None,
            output_summary_json=None,
        )
        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.BACKFILL,
            dataset_version=str(dataset_version_id),
            governance_state=GovernanceState.APPROVED_RESEARCH,
            created_at=end,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id="baseline_strategy",
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal("10000.00"),
            interval=BarInterval.FIVE_MIN,
            start_date=start.date(),
            end_date=end.date(),
            price_basis=PriceBasis.RAW,
            universe_version="v1",
            git_commit="dev",
            python_version=platform.python_version(),
            notes="Historical market bar backfill cycle",
        )
        manifest_service.save(manifest)

        base_metadata = {
            "pipeline": "market_backfill",
            "symbols": symbols,
            "backfill_start": start.isoformat(),
            "backfill_end": end.isoformat(),
            "manifest_run_type": manifest.run_type.value,
            "manifest_interval": manifest.interval.value,
            "trigger_type": trigger_type,
            "actor": actor,
        }

        with start_span("market_backfill_cycle.run", timespan=SpanTimespan.CYCLE) as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.cycle_start", start.isoformat())
            cycle_span.set_attribute("ratp.cycle_end", end.isoformat())
            cycle_span.set_attribute("ratp.expected_symbol_count", len(symbols))

            audit_logger.record_run_started(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            dataset_version_contract = DatasetVersion(
                dataset_version_id=dataset_version_id,
                dataset_name=RAW_BARS_DATASET.dataset_key,
                created_at=now,
                source="alpaca",
                price_basis=PriceBasis.RAW,
                interval=BarInterval.FIVE_MIN,
                schema_version=RAW_BARS_DATASET.schema_version,
                symbol_coverage=len(symbols),
                date_coverage_start=start.date(),
                date_coverage_end=end.date(),
                validation_status="unvalidated",
                checksum=None,
                source_manifest={
                    "pipeline": "market_backfill",
                    "ingestion_run_id": str(ingestion_run_id),
                    "backfill_start": start.isoformat(),
                    "backfill_end": end.isoformat(),
                    "symbols": symbols,
                },
                metadata_json={
                    **base_metadata,
                    "ingestion_run_id": str(ingestion_run_id),
                    "dataset_type": "historical_backfill",
                },
            )
            dataset_version = dataset_registration_service.register(dataset_version_contract)

            ingestion_run_contract = IngestionRun(
                ingestion_run_id=str(ingestion_run_id),
                created_at=now,
                run_timestamp=end,
                run_type=RunType.BACKFILL,
                source="alpaca",
                dataset_version=dataset_version_id,
                status="running",
                started_at=now,
                completed_at=None,
                error_message=None,
                row_count=None,
                file_count=None,
            )
            ingestion_run = ingestion_run_registration_service.register(ingestion_run_contract)

            raw_client = get_stock_historical_client()
            historical_client = AlpacaHistoricalBarsClient(raw_client)

            job = BackfillMarketBarsJob(
                session=session,
                historical_client=historical_client,
                run_id=str(run_id),
                audit_logger=audit_logger,
                ingestion_run_id=str(ingestion_run_id),
                dataset_version_id=str(dataset_version_id),
            )

            step = "backfill_market_bars"
            record_step_started(
                logger=logger,
                metrics=MARKET_BACKFILL_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()
            try:
                with start_span(
                    "market_backfill_cycle.backfill_market_bars", timespan=SpanTimespan.STEP
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)
                    step_span.set_attribute("ratp.expected_symbol_count", len(symbols))

                    asyncio.run(
                        job.run(
                            symbols=symbols,
                            start=start,
                            end=end,
                        )
                    )

                step_duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=MARKET_BACKFILL_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=step_duration,
                )
            except Exception as exc:
                step_duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=MARKET_BACKFILL_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=step_duration,
                )
                raise

            ingestion_run.status = "completed"
            ingestion_run.completed_at = datetime.now(UTC)
            _save_runtime_job_run(
                status="completed",
                completed_at=datetime.now(UTC),
                error_message=None,
                output_summary_json={
                    "dataset_version_id": str(dataset_version_id),
                    "ingestion_run_id": str(ingestion_run_id),
                    "expected_symbol_count": len(symbols),
                    "last_successful_step": "backfill_market_bars",
                },
            )

            ingestion_run = ingestion_run_registration_service.save(ingestion_run)

            if dataset_version is not None:
                dataset_version.validation_status = "validated"
                dataset_version = dataset_registration_service.save(dataset_version)

            manifest.status = "completed"
            manifest.error_message = None
            manifest_service.save(manifest)

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            total_duration = perf_counter() - cycle_wall_start
            record_cycle_completed(
                logger=logger,
                metrics=MARKET_BACKFILL_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )
            return {
                "run_id": str(run_id),
                "runtime_job_run_id": job_run_id,
                "ingestion_run_id": str(ingestion_run_id),
                "dataset_version_id": str(dataset_version_id),
                "expected_symbol_count": len(symbols),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "symbols": symbols,
                "trigger_type": trigger_type,
                "actor": actor,
                "last_successful_step": "backfill_market_bars",
            }

    except Exception as exc:
        _save_runtime_job_run(
            status="failed",
            completed_at=datetime.now(UTC),
            error_message=str(exc),
            output_summary_json={
                "dataset_version_id": str(dataset_version_id),
                "ingestion_run_id": str(ingestion_run_id),
            },
        )
        if ingestion_run is not None:
            ingestion_run.status = "failed"
            ingestion_run.completed_at = datetime.now(UTC)
            ingestion_run.error_message = str(exc)

            ingestion_run = ingestion_run_registration_service.save(ingestion_run)

        if dataset_version is not None and dataset_version.validation_status == "unvalidated":
            dataset_version.validation_status = "failed"
            dataset_version = dataset_registration_service.save(dataset_version)

        total_duration = perf_counter() - cycle_wall_start
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        record_cycle_failed(
            logger=logger,
            metrics=MARKET_BACKFILL_CYCLE_METRICS,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=total_duration,
        )
        raise
    finally:
        session.close()
