import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job import (
    IngestBarsJob,
)
from autonomous_trading_platform.observability.lifecycle import (
    StepMetricSet,
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
from autonomous_trading_platform.storage.sor.repositories.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)
from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
    TickerLifecycleService,
)
from autonomous_trading_platform.universe.services.universe_membership_service import (
    UniverseMembershipService,
)
from src.db import get_session

logger = get_logger(__name__)
INGESTION_STEP_METRICS = StepMetricSet(
    runs=ingestion_cycle_step_runs,
    duration=ingestion_cycle_step_duration,
)


def _record_cycle_started(*, component: str, run_id: str) -> None:
    logger.info(
        "ingestion_cycle_started run_id=%s component=%s",
        run_id,
        component,
    )


def _record_cycle_completed(*, component: str, run_id: str, duration_seconds: float) -> None:
    logger.info(
        "ingestion_cycle_completed run_id=%s component=%s duration_seconds=%.6f",
        run_id,
        component,
        duration_seconds,
    )
    ingestion_cycle_runs.add(
        1,
        {
            "component": component,
            "status": "completed",
        },
    )
    ingestion_cycle_duration.record(
        duration_seconds,
        {
            "component": component,
            "status": "completed",
        },
    )


def _record_cycle_failed(
    *,
    component: str,
    run_id: str,
    exc: Exception,
    duration_seconds: float,
) -> None:
    logger.exception(
        "ingestion_cycle_failed run_id=%s component=%s duration_seconds=%.6f error=%s",
        run_id,
        component,
        duration_seconds,
        str(exc),
    )
    ingestion_cycle_failures.add(
        1,
        {
            "component": component,
            "failure_class": "unknown",
        },
    )
    ingestion_cycle_runs.add(
        1,
        {
            "component": component,
            "status": "failed",
        },
    )
    ingestion_cycle_duration.record(
        duration_seconds,
        {
            "component": component,
            "status": "failed",
        },
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
    component = "scheduler.run_market_ingestion_cycle"

    _record_cycle_started(component=component, run_id=str(run_id))

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

        with start_span("market_ingestion_cycle.run") as cycle_span:
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
                with start_span("market_ingestion_cycle.ingest_bars") as step_span:
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

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            total_duration = perf_counter() - cycle_wall_start
            _record_cycle_completed(
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )
    except Exception as exc:
        total_duration = perf_counter() - cycle_wall_start
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        _record_cycle_failed(
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
