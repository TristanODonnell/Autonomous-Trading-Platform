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
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
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
from autonomous_trading_platform.storage.parquet.datasets import (
    CORPORATE_ACTIONS_DATASET,
    RAW_BARS_DATASET,
)
from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
    ParquetBarRepository,
)
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

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


def run_corporate_action_ingestion_cycle(
    *,
    source_raw_bars_dataset_version_id: str | None = None,
) -> None:
    """
    Entry point for the Airflow DAG.

    When called from a parent orchestrator that already resolved the active raw_bars
    dataset, pass ``source_raw_bars_dataset_version_id`` to skip the internal lookup
    and use the exact version the caller wants processed.
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
    component = "scheduler.run_corporate_action_ingestion_cycle"

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
                job_name="corporate_action_ingestion_cycle",
                parent_job_run_id=None,
                status=status,
                trigger_type="scheduler",
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
                    "dataset_name": CORPORATE_ACTIONS_DATASET.dataset_key,
                    "price_basis": PriceBasis.RAW.value,
                    "interval": BarInterval.ONE_DAY.value,
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

    ingestion_run: IngestionRun | None = None
    dataset_version: DatasetVersion | None = None
    manifest: RunManifest | None = None
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

        corporate_actions_dataset_version_id = generate_dataset_version("corporate_actions")
        adjusted_bars_dataset_version_id = generate_dataset_version("adjusted_bars")
        dataset_version_id = corporate_actions_dataset_version_id

        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.CORPORATE_ACTION_INGESTION,
            created_at=now_utc,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id="baseline_strategy",
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal("10000.00"),
            interval=BarInterval.ONE_DAY,
            price_basis=PriceBasis.RAW,
            governance_state=GovernanceState.APPROVED_RESEARCH,
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

        if source_raw_bars_dataset_version_id is not None:
            _resolved_raw_bars_version_id = source_raw_bars_dataset_version_id
        else:
            source_raw_dataset = dataset_registration_service.get_latest_validated_dataset(
                dataset_name=RAW_BARS_DATASET.dataset_key,
                price_basis=PriceBasis.RAW,
            )
            if source_raw_dataset is None:
                raise ValueError("No validated raw bars dataset version found.")
            _resolved_raw_bars_version_id = source_raw_dataset.dataset_version_id

        source_raw_bars_dataset_version_id = _resolved_raw_bars_version_id

        dataset_version_contract = DatasetVersion(
            dataset_version_id=dataset_version_id,
            dataset_name=CORPORATE_ACTIONS_DATASET.dataset_key,
            created_at=now_utc,
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.ONE_DAY,
            schema_version=CORPORATE_ACTIONS_DATASET.schema_version,
            symbol_coverage=None,
            date_coverage_start=cycle_start.date(),
            date_coverage_end=cycle_end.date(),
            validation_status="unvalidated",
            checksum=None,
            source_dataset_version=str(source_raw_bars_dataset_version_id),
            source_manifest={
                "pipeline": "corporate_actions_ingestion",
                "ingestion_run_id": str(ingestion_run_id),
                "cycle_start": cycle_start.isoformat(),
                "cycle_end": cycle_end.isoformat(),
                "source_raw_bars_dataset_version_id": str(source_raw_bars_dataset_version_id),
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

                    bar_repository = ParquetBarRepository()
                    job = IngestCorporateActionsJob(
                        session=session,
                        run_id=str(run_id),
                        audit_logger=audit_logger,
                        cycle_timestamp=cycle_end,
                        ingestion_run_id=str(ingestion_run_id),
                        dataset_version_id=str(corporate_actions_dataset_version_id),
                        adjusted_bars_dataset_version_id=str(adjusted_bars_dataset_version_id),
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

            _save_runtime_job_run(
                status="completed",
                completed_at=datetime.now(UTC),
                error_message=None,
                output_summary_json={
                    "dataset_version_id": str(corporate_actions_dataset_version_id),
                    "corporate_actions_dataset_version_id": str(
                        corporate_actions_dataset_version_id
                    ),
                    "adjusted_bars_dataset_version_id": str(adjusted_bars_dataset_version_id),
                    "ingestion_run_id": str(ingestion_run_id),
                    "source_raw_bars_dataset_version_id": str(source_raw_bars_dataset_version_id),
                    "last_successful_step": "ingest_corporate_actions",
                },
            )

            manifest.status = "completed"
            manifest.current_step = None
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
                metrics=CORPORATE_ACTION_INGESTION_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )

    except Exception as exc:
        _save_runtime_job_run(
            status="failed",
            completed_at=datetime.now(UTC),
            error_message=str(exc),
            output_summary_json={
                "dataset_version_id": str(dataset_version_id)
                if "dataset_version_id" in locals()
                else None,
                "ingestion_run_id": str(ingestion_run_id),
            },
        )

        if ingestion_run is not None:
            ingestion_run.status = "failed"
            ingestion_run.completed_at = datetime.now(UTC)
            ingestion_run.error_message = str(exc)

            ingestion_run = ingestion_run_registration_service.save(ingestion_run)

        if dataset_version is not None:
            dataset_version.validation_status = "failed"
            dataset_version = dataset_registration_service.save(dataset_version)

        if manifest is not None:
            manifest.status = "failed"
            manifest.error_message = str(exc)
            manifest_service.save(manifest)

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
