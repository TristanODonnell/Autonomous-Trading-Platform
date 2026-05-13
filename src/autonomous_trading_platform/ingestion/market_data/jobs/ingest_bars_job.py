from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.ingestion.market_data.clients import (
    alpaca_market_data_client as client,
)
from autonomous_trading_platform.ingestion.market_data.services.bar_ingestion_service import (
    BarIngestionService,
)
from autonomous_trading_platform.ingestion.market_data.services.incremental_ingestion_checkpoint_service import (
    IncrementalIngestionCheckpointService,
)
from autonomous_trading_platform.ingestion.market_data.services.ingestion_quality_recorder_service import (
    IngestionQualityRecorderService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ingestion_batch_size,
    ingestion_job_duration,
    ingestion_job_failures,
    ingestion_job_runs,
    missing_bars,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.mappers import bars_to_arrow
from autonomous_trading_platform.storage.parquet.paths import get_data_root
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.models.missing_bar_incidents import MissingBarIncidents
from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import SymbolDateCoverage

logger = get_logger(__name__)

INGEST_BARS_JOB_METRICS = JobMetricSet(
    runs=ingestion_job_runs,
    failures=ingestion_job_failures,
    duration=ingestion_job_duration,
)


class IngestBarsJob:
    def __init__(
        self,
        expected_symbols: set[str],
        session: Session,
        run_id: str,
        audit_logger: AuditLoggingService,
        ingestion_run_id: str,
        dataset_version_id: str,
    ) -> None:
        self.session = session
        self.ingestion_run_id = ingestion_run_id
        self.dataset_version_id = dataset_version_id
        self.expected_symbols = expected_symbols
        self.received_symbols: set[str] = set()
        self.completed_cycle_bars: list[MarketBar] = []
        self.current_cycle_timestamp: datetime | None = None
        self.ingestion_service = BarIngestionService(
            session=session,
            run_id=run_id,
            audit_logger=audit_logger,
        )
        self.run_id = run_id
        self.audit_logger = audit_logger

        self.checkpoint_service = IncrementalIngestionCheckpointService(
            session=session,
            audit_logger=audit_logger,
            component="ingestion.ingest_bars_job",
        )
        self.ingestion_quality_recorder_service = IngestionQualityRecorderService(session)

    async def on_provider_bar(self, provider_bar) -> None:
        five_min_bar = await self.ingestion_service.handle_minute_bar(provider_bar)

        if five_min_bar is None:
            return

        cycle_timestamp = five_min_bar.timestamp

        if self.current_cycle_timestamp is None:
            self.current_cycle_timestamp = cycle_timestamp

        if cycle_timestamp > self.current_cycle_timestamp:
            self.finalize_cycle(self.current_cycle_timestamp)
            self.received_symbols.clear()
            self.current_cycle_timestamp = cycle_timestamp

        self.completed_cycle_bars.append(five_min_bar)
        self.received_symbols.add(five_min_bar.symbol)

    def finalize_cycle(self, cycle_timestamp: datetime) -> None:
        component = "ingestion.ingest_bars_job"

        missing_symbols = self.expected_symbols - self.received_symbols
        symbols_to_evaluate = sorted(self.expected_symbols & self.received_symbols)
        incidents: list[MissingBarIncidents] = []
        if missing_symbols:
            incidents = []
            for symbol in missing_symbols:
                incident = MissingBarIncidents(
                    incident_id=(
                        f"{self.dataset_version_id}:{symbol}:{cycle_timestamp.isoformat()}:missing_bar"
                    ),
                    symbol=symbol,
                    bar_timestamp=cycle_timestamp,
                    dataset_version=self.dataset_version_id,
                    run_id=self.run_id,
                    ingestion_run_id=self.ingestion_run_id,
                    incident_type="missing_bar",
                    severity="warning",
                    message=f"Missing expected 5-minute bar for {symbol} at {cycle_timestamp.isoformat()}",
                    created_at=datetime.now(UTC),
                    resolved_flag=False,
                )
                incidents.append(incident)
                missing_bars.add(
                    1,
                    {
                        "component": component,
                        "symbol": symbol,
                    },
                )
                self.audit_logger.record_bar_missing(
                    run_id=self.run_id,
                    symbol=symbol,
                    cycle_timestamp=cycle_timestamp,
                )

        coverage_rows: list[SymbolDateCoverage] = []
        for symbol in sorted(self.expected_symbols):
            was_received = symbol in self.received_symbols
            coverage_rows.append(
                SymbolDateCoverage(
                    coverage_id=f"{self.dataset_version_id}:{symbol}:{cycle_timestamp.isoformat()}",
                    symbol=symbol,
                    date=cycle_timestamp.date(),
                    dataset_version=self.dataset_version_id,
                    expected_bar_count=1,
                    actual_bar_count=1 if was_received else 0,
                    completeness_status="complete" if was_received else "missing",
                    gap_summary=None
                    if was_received
                    else f"Missing expected 5-minute bar at {cycle_timestamp.isoformat()}",
                    updated_at=datetime.now(UTC),
                )
            )

        bars = list(self.completed_cycle_bars)
        if bars:
            table = bars_to_arrow(bars)
            write_table(
                table=table,
                dataset=RAW_BARS_DATASET,
                base_path=get_data_root(),
                dataset_version=self.dataset_version_id,
                allow_existing=True,
            )

        self.ingestion_quality_recorder_service.record_symbol_date_quality(
            coverage_rows=coverage_rows,
            incidents=incidents,
        )
        self.completed_cycle_bars.clear()

        if self.expected_symbols:
            missing_ratio = len(missing_symbols) / len(self.expected_symbols)
        else:
            missing_ratio = 0

        if missing_ratio > 0.2:
            self.audit_logger.record_sla_breach(
                run_id=self.run_id,
                component="market_ingestion",
                message="Missing bar ratio exceeded SLA threshold",
                metadata={
                    "cycle_timestamp": cycle_timestamp.isoformat(),
                    "missing_ratio": missing_ratio,
                    "missing_count": len(missing_symbols),
                    "expected_count": len(self.expected_symbols),
                    "threshold": 0.2,
                    "missing_symbols": sorted(missing_symbols),
                },
            )

            raise RuntimeError(f"Too many missing bars ({missing_ratio:.2%}) at {cycle_timestamp}")

        # TODO TEMP: evaluation hook
        if symbols_to_evaluate:
            print(
                f"[EVALUATION_TRIGGER] {cycle_timestamp.isoformat()} "
                f"symbols={len(symbols_to_evaluate)}"
            )

    async def _process_symbol_bars(self, symbol_bars) -> None:
        for provider_bar in symbol_bars:
            await self.on_provider_bar(provider_bar)

    def run_once(self, start: datetime, end: datetime) -> None:
        component = "ingestion.ingest_bars_job"
        job = "ingest_bars"
        job_start = perf_counter()
        cycle_timestamp = end
        checkpoint = None

        record_job_started(
            logger=logger,
            metrics=INGEST_BARS_JOB_METRICS,
            job=job,
            component=component,
            run_id=self.run_id,
        )

        try:
            with start_span(
                "ingest_bars_job.run",
                timespan=SpanTimespan.JOB,
            ) as job_span:
                job_span.set_attribute("ratp.run_id", self.run_id)
                job_span.set_attribute("ratp.component", component)
                job_span.set_attribute("ratp.job", job)
                job_span.set_attribute("ratp.symbol_count", len(self.expected_symbols))
                job_span.set_attribute("ratp.start", start.isoformat())
                job_span.set_attribute("ratp.end", end.isoformat())

                checkpoint = self.checkpoint_service.get_or_create_checkpoint(
                    ingestion_run_id=self.ingestion_run_id,
                    dataset_version_id=self.dataset_version_id,
                    cycle_timestamp=cycle_timestamp,
                )

                can_run, reason = self.checkpoint_service.can_start_cycle(checkpoint)
                if not can_run:
                    logger.info(
                        "Skipping incremental ingestion cycle",
                        extra={
                            "run_id": self.run_id,
                            "cycle_timestamp": cycle_timestamp.isoformat(),
                            "reason": reason,
                        },
                    )
                    return

                checkpoint = self.checkpoint_service.mark_in_progress(checkpoint)

                response = client.fetch_minute_bars(
                    symbols=sorted(self.expected_symbols),
                    start=start,
                    end=end,
                )

                ingestion_batch_size.record(
                    sum(len(response.data.get(symbol, [])) for symbol in self.expected_symbols),
                    {
                        "component": component,
                        "job": job,
                    },
                )

                async def _run() -> None:
                    for symbol in sorted(self.expected_symbols):
                        symbol_bars = response.data.get(symbol, [])
                        await self._process_symbol_bars(symbol_bars)

                asyncio.run(_run())

                if self.current_cycle_timestamp is not None:
                    self.finalize_cycle(self.current_cycle_timestamp)

                checkpoint = self.checkpoint_service.mark_completed(
                    checkpoint,
                    last_successful_bar_timestamp=self.current_cycle_timestamp,
                )

            duration = perf_counter() - job_start
            record_job_completed(
                logger=logger,
                metrics=INGEST_BARS_JOB_METRICS,
                job=job,
                component=component,
                run_id=self.run_id,
                duration_seconds=duration,
            )

        except Exception as exc:
            if checkpoint is not None:
                self.checkpoint_service.mark_failed(checkpoint, exc)
            duration = perf_counter() - job_start
            record_job_failed(
                logger=logger,
                metrics=INGEST_BARS_JOB_METRICS,
                job=job,
                component=component,
                run_id=self.run_id,
                exc=exc,
                duration_seconds=duration,
            )
            raise
