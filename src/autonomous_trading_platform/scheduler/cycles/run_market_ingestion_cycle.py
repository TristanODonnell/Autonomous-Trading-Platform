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
from autonomous_trading_platform.runtime.services.daily_dataset_version_resolver_service import (
    DailyDatasetVersionResolverService,
)
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.runtime.services.ingestion_run_registration_service import (
    IngestionRunRegistrationService,
)
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
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
    symbols_override: list[str] | None = None,
    *,
    enforce_lateness: bool = True,
    trigger_type: str = "scheduler",
    actor: str | None = None,
) -> dict[str, object]:
    """
    Entry point for the Airflow DAG.

    Pass symbols_override to bypass UniverseMembershipService — used by
    HistoricalIngestionReplayOrchestrator so the replay can target a
    specific symbol set instead of the live universe snapshot.

    Pass enforce_lateness=False for historical replay — bars fetched via
    HTTP for past timestamps are not subject to streaming lateness checks.
    """

    if now_utc is None:
        now_utc = datetime.now(UTC)

    cycle_wall_start = perf_counter()

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)
    dataset_registration_service = DatasetRegistrationService(session=session)
    ingestion_run_registration_service = IngestionRunRegistrationService(session=session)
    daily_dataset_resolver_service = DailyDatasetVersionResolverService(
        session=session,
        dataset_registration_service=dataset_registration_service,
    )
    run_id = uuid.uuid4()
    component = "scheduler.run_market_ingestion_cycle"

    runtime_job_run_repository = RuntimeJobRunRepository(session)
    job_run_id = str(run_id)
    job_started_at = now_utc

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
                job_name="market_ingestion_cycle",
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
                    "trigger_type": trigger_type,
                    "actor": actor,
                },
                output_summary_json=output_summary_json,
            )
        )

    _save_runtime_job_run(
        status="running",
        completed_at=None,
        error_message=None,
        output_summary_json=None,
    )

    ingestion_run_id = uuid.uuid4()
    ingestion_run: IngestionRun | None = None
    dataset_version: DatasetVersion | None = None
    dataset_version_id: str | None = None
    base_metadata: dict[str, object] = {}

    record_cycle_started(
        logger=logger, metrics=INGESTION_CYCLE_METRICS, component=component, run_id=str(run_id)
    )

    try:
        resolved_universe_version_id: str | None = None
        resolved_universe_source: str | None = None
        resolved_universe_member_count: int | None = None

        if symbols_override is not None:
            if not symbols_override:
                raise RuntimeError("symbols_override was provided but is empty")
            expected_symbols = set(symbols_override)
        else:
            version_repository = UniverseVersionRepository(session)
            ticker_lifecycle_repository = TickerLifecycleRepository(session)
            ticker_lifecycle_service = TickerLifecycleService(ticker_lifecycle_repository)

            membership_service = UniverseMembershipService(
                repository=version_repository,
                ticker_lifecycle_service=ticker_lifecycle_service,
            )

            expected_symbols = set(membership_service.get_query_symbols_for_date(now_utc.date()))

            if not expected_symbols:
                raise RuntimeError(
                    f"No active universe symbols found for {now_utc.date().isoformat()}"
                )

            active_version = version_repository.get_active_version(now_utc)
            if active_version is not None:
                resolved_universe_version_id = active_version.universe_version_id
                resolved_universe_source = active_version.source
                resolved_universe_member_count = len(expected_symbols)

        cycle_end = floor_to_five_minutes(now_utc)
        cycle_start = cycle_end - timedelta(minutes=5)

        trading_date = cycle_end.date()

        base_metadata = {
            "cycle_start": cycle_start.isoformat(),
            "cycle_end": cycle_end.isoformat(),
            "expected_symbols": sorted(expected_symbols),
            "trigger_type": trigger_type,
            "actor": actor,
        }

        dataset_version = daily_dataset_resolver_service.get_or_create_active_daily_dataset(
            dataset_name=RAW_BARS_DATASET.dataset_key,
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            trading_date=trading_date,
            created_at=now_utc,
            source="alpaca",
            schema_version=RAW_BARS_DATASET.schema_version,
            symbol_coverage=len(expected_symbols),
            date_coverage_start=trading_date,
            date_coverage_end=trading_date,
            source_manifest={
                "pipeline": "market_ingestion",
                "cycle_start": cycle_start.isoformat(),
                "cycle_end": cycle_end.isoformat(),
                "symbols": sorted(expected_symbols),
            },
            metadata_json={
                **base_metadata,
                "dataset_type": "incremental_market_bars",
            },
        )

        dataset_version_id = dataset_version.dataset_version_id

        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.INGESTION,
            created_at=now_utc,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id="baseline_strategy",
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal("10000.00"),
            interval=BarInterval.FIVE_MIN,
            price_basis=PriceBasis.RAW,
            governance_state=GovernanceState.APPROVED_RESEARCH,
            start_date=cycle_start.date(),
            end_date=cycle_end.date(),
            dataset_version=str(dataset_version_id),
            universe_version=resolved_universe_version_id or "v1",
            universe_version_id=resolved_universe_version_id,
            universe_source=resolved_universe_source,
            universe_member_count=resolved_universe_member_count,
            git_commit="dev",
            python_version=platform.python_version(),
            notes="5-minute market bar ingestion cycle",
        )
        manifest_service.save(manifest)
        base_metadata = {
            **base_metadata,
            "manifest_run_type": manifest.run_type.value,
            "manifest_interval": manifest.interval.value,
            "dataset_version_id": str(dataset_version_id),
        }
        # TODO may need to change some field defaults later
        ingestion_run_contract = IngestionRun(
            ingestion_run_id=str(ingestion_run_id),
            created_at=now_utc,
            run_timestamp=cycle_end,
            run_type=RunType.INGESTION,
            source="alpaca",
            dataset_version=str(dataset_version_id),
            status="running",
            started_at=now_utc,
            completed_at=None,
            error_message=None,
            row_count=None,
            file_count=None,
        )
        ingestion_run = ingestion_run_registration_service.register(ingestion_run_contract)

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
                enforce_lateness=enforce_lateness,
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
            if dataset_version is not None:
                dataset_version.validation_status = "validated"
                dataset_version = dataset_registration_service.save(dataset_version)
            _save_runtime_job_run(
                status="completed",
                completed_at=datetime.now(UTC),
                error_message=None,
                output_summary_json={
                    "dataset_version_id": str(dataset_version_id),
                    "ingestion_run_id": str(ingestion_run_id),
                    "expected_symbol_count": len(expected_symbols),
                    "last_successful_step": "ingest_bars",
                },
            )
            ingestion_run.completed_at = datetime.now(UTC)
            ingestion_run = ingestion_run_registration_service.save(ingestion_run)
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
                metrics=INGESTION_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )
            return {
                "run_id": str(run_id),
                "runtime_job_run_id": job_run_id,
                "ingestion_run_id": str(ingestion_run_id),
                "dataset_version_id": str(dataset_version_id),
                "expected_symbol_count": len(expected_symbols),
                "cycle_start": cycle_start.isoformat(),
                "cycle_end": cycle_end.isoformat(),
                "symbols": sorted(expected_symbols),
                "trigger_type": trigger_type,
                "actor": actor,
                "last_successful_step": "ingest_bars",
            }
    except Exception as exc:
        if ingestion_run is not None:
            ingestion_run.status = "failed"
            ingestion_run.completed_at = datetime.now(UTC)
            ingestion_run.error_message = str(exc)
            ingestion_run = ingestion_run_registration_service.save(ingestion_run)

        if dataset_version is not None and dataset_version.validation_status == "unvalidated":
            dataset_version.validation_status = "failed"
            dataset_version = dataset_registration_service.save(dataset_version)

        total_duration = perf_counter() - cycle_wall_start
        _save_runtime_job_run(
            status="failed",
            completed_at=datetime.now(UTC),
            error_message=str(exc),
            output_summary_json={
                "dataset_version_id": str(dataset_version_id),
                "ingestion_run_id": str(ingestion_run_id),
            },
        )
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
