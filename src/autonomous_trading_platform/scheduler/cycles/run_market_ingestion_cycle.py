import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job import (
    IngestBarsJob,
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
    ingestion_cycle_duration,
    ingestion_cycle_failures,
    ingestion_cycle_runs,
    ingestion_cycle_step_duration,
    ingestion_cycle_step_runs,
)
from autonomous_trading_platform.observability.telemetry import setup_telemetry
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.repositories.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
    TickerLifecycleService,
)
from autonomous_trading_platform.universe.services.universe_membership_service import (
    UniverseMembershipService,
)

logger = get_logger(__name__)
INGESTION_CYCLE_METRICS = CycleMetricSet(
    runs=ingestion_cycle_runs, failures=ingestion_cycle_failures, duration=ingestion_cycle_duration
)
INGESTION_STEP_METRICS = StepMetricSet(
    runs=ingestion_cycle_step_runs,
    duration=ingestion_cycle_step_duration,
)


def floor_to_five_minutes(timestamp: datetime) -> datetime:
    minute = (timestamp.minute // 5) * 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def run_market_ingestion_cycle(
    now_utc: datetime | None = None,
) -> None:
    """
    Entry point for the Airflow DAG.
    """

    if now_utc is None:
        now_utc = datetime.now(UTC)

    cycle_wall_start = perf_counter()

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)

    run_id = uuid.uuid4()
    ingestion_run_id = uuid.uuid4()
    ingestion_run: IngestionRuns | None = None
    dataset_version_id = uuid.uuid4()
    component = "scheduler.run_market_ingestion_cycle"
    base_metadata: dict[str, object] = {}

    record_cycle_started(
        logger=logger, metrics=INGESTION_CYCLE_METRICS, component=component, run_id=str(run_id)
    )

    try:
        snapshot_repository = UniverseSnapshotRepository(session)
        ticker_lifecycle_repository = TickerLifecycleRepository(session)
        ticker_lifecycle_service = TickerLifecycleService(ticker_lifecycle_repository)

        membership_service = UniverseMembershipService(
            repository=snapshot_repository,
            ticker_lifecycle_service=ticker_lifecycle_service,
        )

        expected_symbols = set(membership_service.get_query_symbols_for_date(now_utc.date()))

        if not expected_symbols:
            raise RuntimeError(f"No active universe symbols found for {now_utc.date().isoformat()}")

        cycle_end = floor_to_five_minutes(now_utc)
        cycle_start = cycle_end - timedelta(minutes=5)

        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.BACKTEST,
            created_at=now_utc,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id="baseline_strategy",
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal("10000.00"),
            interval=BarInterval.FIVE_MIN,
            start_date=cycle_start.date(),
            end_date=cycle_end.date(),
            dataset_version="v1",
            universe_version="v1",
            git_commit="dev",
            python_version=platform.python_version(),
            notes="5-minute market bar ingestion cycle",
        )
        manifest_service.save(manifest)
        base_metadata = {
            "cycle_start": cycle_start.isoformat(),
            "cycle_end": cycle_end.isoformat(),
            "expected_symbols": sorted(expected_symbols),
            "manifest_run_type": manifest.run_type.value,
            "manifest_interval": manifest.interval.value,
        }
        # TODO may need to change some field defaults later
        ingestion_run = IngestionRuns(
            ingestion_run_id=str(ingestion_run_id),
            created_at=now_utc,
            run_timestamp=cycle_end,
            run_type=RunType.INGESTION,
            source="alpaca",
            dataset_version=1,
            status="running",
            started_at=now_utc,
            completed_at=None,
            error_message=None,
            row_count=None,
            file_count=None,
        )
        with SorUnitOfWork(session) as uow:
            uow.ingestion_runs.insert(ingestion_run)

        # TODO may need to change some field defaults later
        dataset_version = DatasetVersions(
            dataset_version_id=str(dataset_version_id),
            dataset_name="market_bars",
            created_at=now_utc,
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="bars_schema_v1",
            symbol_coverage=len(expected_symbols),
            date_coverage_start=cycle_start.date(),
            date_coverage_end=cycle_end.date(),
            validation_status="unvalidated",
            checksum=None,
            source_manifest={
                "pipeline": "market_ingestion",
                "ingestion_run_id": str(ingestion_run_id),
                "cycle_start": cycle_start.isoformat(),
                "cycle_end": cycle_end.isoformat(),
                "symbols": sorted(expected_symbols),
            },
            metadata_json={
                **base_metadata,
                "dataset_type": "incremental_market_bars",
            },
        )
        with SorUnitOfWork(session) as uow:
            uow.dataset_versions.insert(dataset_version)

        with start_span("market_ingestion_cycle.run", timespan=SpanTimespan.CYCLE) as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.cycle_start", cycle_start.isoformat())
            cycle_span.set_attribute("ratp.cycle_end", cycle_end.isoformat())
            cycle_span.set_attribute("ratp.expected_symbol_count", len(expected_symbols))

            audit_logger.record_run_started(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            job = IngestBarsJob(
                expected_symbols=expected_symbols,
                session=session,
                run_id=str(run_id),
                audit_logger=audit_logger,
                ingestion_run_id=str(ingestion_run_id),
                dataset_version_id=str(dataset_version_id),
            )

            step = "ingest_bars"
            record_step_started(
                logger=logger,
                metrics=INGESTION_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()
            try:
                with start_span(
                    "market_ingestion_cycle.ingest_bars", timespan=SpanTimespan.STEP
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)
                    step_span.set_attribute("ratp.expected_symbol_count", len(expected_symbols))

                    job.run_once(start=cycle_start, end=cycle_end)

                step_duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=INGESTION_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=step_duration,
                )
            except Exception as exc:
                step_duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=INGESTION_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=step_duration,
                    exc=exc,
                )
                raise

            ingestion_run.status = "completed"
            ingestion_run.completed_at = datetime.now(UTC)

            with SorUnitOfWork(session) as uow:
                uow.ingestion_runs.upsert(ingestion_run)

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            total_duration = perf_counter() - cycle_wall_start
            record_cycle_completed(
                logger=logger,
                metrics=INGESTION_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )
    except Exception as exc:
        if ingestion_run is not None:
            ingestion_run.status = "failed"
            ingestion_run.completed_at = datetime.now(UTC)
            ingestion_run.error_message = str(exc)

            with SorUnitOfWork(session) as uow:
                uow.ingestion_runs.upsert(ingestion_run)

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
            metrics=INGESTION_CYCLE_METRICS,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=total_duration,
        )
        raise
    finally:
        session.close()


if __name__ == "__main__":
    setup_telemetry("scheduler-market-ingestion-cycle")
    run_market_ingestion_cycle()
