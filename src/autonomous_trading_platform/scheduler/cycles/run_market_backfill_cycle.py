from __future__ import annotations

import asyncio
import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.ingestion.market_data.clients.alpaca_historical_bars_client import (
    AlpacaHistoricalBarsClient,
)
from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
    get_stock_historical_client,
)
from autonomous_trading_platform.ingestion.market_data.jobs.backfill_market_bars_job import (
    BackfillMarketBarsJob,
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
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from src.db import get_session

logger = get_logger(__name__)


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
    market_backfill_cycle_runs.add(
        1,
        {
            "component": component,
            "status": "completed",
        },
    )
    market_backfill_cycle_duration.record(
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
    market_backfill_cycle_failures.add(
        1,
        {
            "component": component,
            "failure_class": "unknown",
        },
    )
    market_backfill_cycle_runs.add(
        1,
        {
            "component": component,
            "status": "failed",
        },
    )
    market_backfill_cycle_duration.record(
        duration_seconds,
        {
            "component": component,
            "status": "failed",
        },
    )


def _record_step_started(*, step: str, component: str, run_id: str) -> None:
    logger.info(
        "ingestion_cycle_step_started run_id=%s component=%s step=%s",
        run_id,
        component,
        step,
    )
    market_backfill_cycle_step_runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "started",
        },
    )


def _record_step_completed(
    *,
    step: str,
    component: str,
    run_id: str,
    duration_seconds: float,
) -> None:
    logger.info(
        "ingestion_cycle_step_completed run_id=%s component=%s step=%s duration_seconds=%.6f",
        run_id,
        component,
        step,
        duration_seconds,
    )
    market_backfill_cycle_step_runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "completed",
        },
    )
    market_backfill_cycle_step_duration.record(
        duration_seconds,
        {
            "component": component,
            "step": step,
            "status": "completed",
        },
    )


def _record_step_failed(
    *,
    step: str,
    component: str,
    run_id: str,
    exc: Exception,
    duration_seconds: float,
) -> None:
    logger.exception(
        "ingestion_cycle_step_failed run_id=%s component=%s step=%s duration_seconds=%.6f error=%s",
        run_id,
        component,
        step,
        duration_seconds,
        str(exc),
    )
    market_backfill_cycle_step_runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "failed",
        },
    )
    market_backfill_cycle_duration.record(
        duration_seconds,
        {
            "component": component,
            "step": step,
            "status": "failed",
        },
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

    run_id = uuid.uuid4()
    component = "scheduler.run_market_backfill_cycle"
    _record_cycle_started(component=component, run_id=str(run_id))
    try:
        # TODO: Replace with universe symbols
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

        with start_span("market_backfill_cycle") as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.cycle_start", start.isoformat())
            cycle_span.set_attribute("ratp.cycle_end", end.isoformat())
            cycle_span.set_attribute("ratp.expected_symbol_count", len(symbols))

            audit_logger.record_run_started(
                run_id=str(run_id),
                component="market_backfill",
                metadata=base_metadata,
            )
            raw_client = get_stock_historical_client()
            historical_client = AlpacaHistoricalBarsClient(raw_client)

            job = BackfillMarketBarsJob(
                session=session,
                historical_client=historical_client,
                run_id=str(run_id),
                audit_logger=audit_logger,
            )
            asyncio.run(
                job.run(
                    symbols=symbols,
                    start=start,
                    end=end,
                )
            )
            audit_logger.record_run_completed(
                run_id=str(run_id),
                component="market_backfill",
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
            component="market_backfill",
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
