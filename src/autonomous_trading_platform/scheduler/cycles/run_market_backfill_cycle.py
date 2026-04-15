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
from autonomous_trading_platform.db import get_session
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
) -> None:
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
    dataset_version_id = uuid.uuid4()
    ingestion_run: IngestionRun | None = None
    dataset_version: DatasetVersion | None = None
    component = "scheduler.run_market_backfill_cycle"
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

        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.BACKTEST,
            created_at=end,
            environment="local",
            broker="alpaca",
            broker_account_id="paper",
            strategy_id="baseline_strategy",
            strategy_version="v1",
            strategy_config={},
            capital_bucket=Decimal("10000.00"),
            interval=BarInterval.ONE_DAY,
            start_date=start.date(),
            end_date=end.date(),
            dataset_version="v1",
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

            ingestion_run_contract = IngestionRun(
                ingestion_run_id=str(ingestion_run_id),
                created_at=now,
                run_timestamp=end,
                run_type=RunType.BACKFILL,
                source="alpaca",
                dataset_version=str(dataset_version_id),
                status="running",
                started_at=now,
                completed_at=None,
                error_message=None,
                row_count=None,
                file_count=None,
            )
            ingestion_run = ingestion_run_registration_service.register(ingestion_run_contract)

            dataset_version_contract = DatasetVersion(
                dataset_version_id=str(dataset_version_id),
                dataset_name="market_bars_backfill",
                created_at=now,
                source="alpaca",
                price_basis=PriceBasis.RAW,
                interval=BarInterval.ONE_DAY,
                schema_version="bars_schema_v1",
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
                metrics=MARKET_BACKFILL_CYCLE_METRICS,
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
            metrics=MARKET_BACKFILL_CYCLE_METRICS,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=total_duration,
        )
        raise
    finally:
        session.close()
