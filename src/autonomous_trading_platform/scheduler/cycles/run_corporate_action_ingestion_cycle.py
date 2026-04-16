from __future__ import annotations

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
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.ingestion.corporate_actions.jobs.ingest_corporate_actions_job import (
    IngestCorporateActionsJob,
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
    corporate_action_ingestion_cycle_duration,
    corporate_action_ingestion_cycle_failures,
    corporate_action_ingestion_cycle_runs,
    corporate_action_ingestion_cycle_step_duration,
    corporate_action_ingestion_cycle_step_runs,
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
from autonomous_trading_platform.storage.parquet.parquet_bar_repository import ParquetBarRepository
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version

logger = get_logger(__name__)
CORPORATE_ACTION_INGESTION_CYCLE_METRICS = CycleMetricSet(
    runs=corporate_action_ingestion_cycle_runs,
    failures=corporate_action_ingestion_cycle_failures,
    duration=corporate_action_ingestion_cycle_duration,
)
CORPORATE_ACTION_INGESTION_STEP_METRICS = StepMetricSet(
    runs=corporate_action_ingestion_cycle_step_runs,
    duration=corporate_action_ingestion_cycle_step_duration,
)


def run_corporate_action_ingestion_cycle() -> None:
    """
    Entry point for the Airflow DAG.
    """
    now_utc = datetime.now(UTC)
    cycle_wall_start = perf_counter()

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)
    dataset_registration_service = DatasetRegistrationService(session=session)
    ingestion_run_registration_service = IngestionRunRegistrationService(session=session)
    run_id = uuid.uuid4()
    ingestion_run_id = uuid.uuid4()
    dataset_version_id = generate_dataset_version("adjusted_bars")
    ingestion_run: IngestionRun | None = None
    dataset_version: DatasetVersion | None = None
    component = "scheduler.run_corporate_action_ingestion_cycle"
    base_metadata: dict[str, object] = {}

    record_cycle_started(
        logger=logger,
        metrics=CORPORATE_ACTION_INGESTION_CYCLE_METRICS,
        component=component,
        run_id=str(run_id),
    )

    try:
        cycle_end = now_utc
        cycle_start = cycle_end - timedelta(days=1)

        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.PAPER,
            created_at=now_utc,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id="baseline_strategy",
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal("10000.00"),
            interval=BarInterval.ONE_DAY,
            start_date=cycle_start.date(),
            end_date=cycle_end.date(),
            dataset_version=str(dataset_version_id),
            universe_version="v1",
            git_commit="dev",
            python_version=platform.python_version(),
            notes="Daily corporate actions ingestion cycle",
        )
        manifest_service.save(manifest)

        base_metadata = {
            "run_id": str(run_id),
            "ingestion_run_id": str(ingestion_run_id),
            "dataset_version_id": dataset_version_id,
            "cycle_start": cycle_start.isoformat(),
            "cycle_end": cycle_end.isoformat(),
            "pipeline": "corporate_actions_ingestion",
            "manifest_run_type": manifest.run_type.value,
        }

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

        dataset_version_contract = DatasetVersion(
            dataset_version_id=dataset_version_id,
            dataset_name="corporate_actions",
            created_at=now_utc,
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.ONE_DAY,
            schema_version="corporate_actions_schema_v1",
            symbol_coverage=None,
            date_coverage_start=cycle_start.date(),
            date_coverage_end=cycle_end.date(),
            validation_status="unvalidated",
            checksum=None,
            source_manifest={
                "pipeline": "corporate_actions_ingestion",
                "ingestion_run_id": str(ingestion_run_id),
                "cycle_start": cycle_start.isoformat(),
                "cycle_end": cycle_end.isoformat(),
            },
            metadata_json={
                **base_metadata,
                "dataset_type": "corporate_actions",
            },
        )
        dataset_version = dataset_registration_service.register(dataset_version_contract)

        with start_span(
            "corporate_action_ingestion_cycle.run", timespan=SpanTimespan.CYCLE
        ) as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.ingestion_run_id", str(ingestion_run_id))
            cycle_span.set_attribute("ratp.dataset_version_id", str(dataset_version_id))
            cycle_span.set_attribute("ratp.cycle_start", cycle_start.isoformat())
            cycle_span.set_attribute("ratp.cycle_end", cycle_end.isoformat())

            audit_logger.record_run_started(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            step = "ingest_corporate_actions"
            record_step_started(
                logger=logger,
                metrics=CORPORATE_ACTION_INGESTION_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()
            try:
                with start_span(
                    "corporate_action_ingestion_cycle.ingest_corporate_actions",
                    timespan=SpanTimespan.STEP,
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.ingestion_run_id", str(ingestion_run_id))
                    step_span.set_attribute("ratp.dataset_version_id", str(dataset_version_id))
                    step_span.set_attribute("ratp.step", step)

                    source_raw_dataset = dataset_registration_service.get_latest_validated_dataset(
                        dataset_name="bars",
                        price_basis=PriceBasis.RAW,
                    )

                    if source_raw_dataset is None:
                        raise ValueError("No validated raw bars dataset version found.")

                    source_raw_bars_dataset_version_id = source_raw_dataset.dataset_version_id

                    bar_repository = ParquetBarRepository()
                    job = IngestCorporateActionsJob(
                        session=session,
                        run_id=str(run_id),
                        audit_logger=audit_logger,
                        cycle_timestamp=cycle_end,
                        ingestion_run_id=str(ingestion_run_id),
                        dataset_version_id=str(dataset_version_id),
                        bar_repository=bar_repository,
                        source_raw_bars_dataset_version_id=source_raw_bars_dataset_version_id,
                    )
                    job.ingest_corporate_actions_job()

                step_duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=CORPORATE_ACTION_INGESTION_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=step_duration,
                )
            except Exception as exc:
                step_duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=CORPORATE_ACTION_INGESTION_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=step_duration,
                )
                raise

            ingestion_run.status = "completed"
            ingestion_run.completed_at = datetime.now(UTC)
            ingestion_run = ingestion_run_registration_service.save(ingestion_run)

            dataset_version.validation_status = "validated"
            dataset_version = dataset_registration_service.save(dataset_version)

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            total_duration = perf_counter() - cycle_wall_start
            record_cycle_completed(
                logger=logger,
                metrics=CORPORATE_ACTION_INGESTION_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )

    except Exception as exc:
        if ingestion_run is not None:
            ingestion_run.status = "failed"
            ingestion_run.completed_at = datetime.now(UTC)
            ingestion_run.error_message = str(exc)

            ingestion_run = ingestion_run_registration_service.save(ingestion_run)

        if dataset_version is not None:
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
            metrics=CORPORATE_ACTION_INGESTION_CYCLE_METRICS,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=total_duration,
        )
        raise
    finally:
        session.close()
