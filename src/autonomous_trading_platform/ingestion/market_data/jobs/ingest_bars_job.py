from __future__ import annotations

import asyncio
from datetime import datetime
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.market_data.clients import (
    alpaca_market_data_client as client,
)
from autonomous_trading_platform.ingestion.market_data.services.bar_ingestion_service import (
    BarIngestionService,
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
    ) -> None:
        self.session = session
        self.expected_symbols = expected_symbols
        self.received_symbols: set[str] = set()
        self.current_cycle_timestamp: datetime | None = None
        self.ingestion_service = BarIngestionService(
            session=session,
            run_id=run_id,
            audit_logger=audit_logger,
        )
        self.run_id = run_id
        self.audit_logger = audit_logger

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

        self.received_symbols.add(five_min_bar.symbol)

        print(five_min_bar)

    def finalize_cycle(self, cycle_timestamp: datetime) -> None:
        component = "ingestion.ingest_bars_job"

        missing_symbols = self.expected_symbols - self.received_symbols
        symbols_to_evaluate = sorted(self.expected_symbols & self.received_symbols)

        for symbol in missing_symbols:
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
