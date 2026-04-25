from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import CheckpointScope, CheckpointStatus
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.ingestion.market_data.services.ingestion_quality_recorder_service import (
    IngestionQualityRecorderService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    record_operation_completed,
    record_operation_failed,
    record_operation_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    backfill_api_requests,
    backfill_batch_size,
    backfill_request_latency_seconds,
    backfill_throughput,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.storage.parquet.services.bar_chunk_writer_service import (
    BarChunkWriterService,
)
from autonomous_trading_platform.storage.sor.models.ingestion_checkpoint import IngestionCheckpoint
from autonomous_trading_platform.storage.sor.models.missing_bar_incidents import MissingBarIncidents
from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import SymbolDateCoverage
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

from ..clients.alpaca_historical_bars_client import AlpacaHistoricalBarsClient
from .bar_ingestion_service import BarIngestionService

logger = get_logger(__name__)


class MarketBackfillService:
    """
    Run historical market-data backfills through the normal ingestion pipeline.
    """

    def __init__(
        self,
        session: Session,
        historical_client: AlpacaHistoricalBarsClient,
        run_id: str,
        audit_logger: AuditLoggingService,
        ingestion_run_id: str,
        dataset_version_id: str,
    ) -> None:
        self.session = session
        self.historical_client = historical_client
        self.run_id = run_id
        self.audit_logger = audit_logger
        self.ingestion_run_id = ingestion_run_id
        self.dataset_version_id = dataset_version_id

        self.bar_ingestion_service = BarIngestionService(
            session,
            run_id=run_id,
            audit_logger=audit_logger,
            enforce_lateness=False,
        )
        self.ingestion_quality_recorder_service = IngestionQualityRecorderService(session)
        self.bar_chunk_writer = BarChunkWriterService(base_path="data")

    async def backfill(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> None:
        component = "ingestion.market_backfill_service"
        request_start = perf_counter()
        service_start = perf_counter()

        record_operation_started(
            logger=logger,
            event="market_backfill_service_started",
            run_id=self.run_id,
            component=component,
            symbol_count=len(symbols),
            start=start.isoformat(),
            end=end.isoformat(),
        )

        with start_span(
            name="market_backfill_service.backfill",
            timespan=SpanTimespan.STEP,
        ) as service_span:
            service_span.set_attribute("ratp.run_id", self.run_id)
            service_span.set_attribute("ratp.component", component)
            service_span.set_attribute("ratp.symbol_count", len(symbols))
            service_span.set_attribute("ratp.backfill_start", start.isoformat())
            service_span.set_attribute("ratp.backfill_end", end.isoformat())
            service_span.set_attribute("ratp.ingestion_run_id", self.ingestion_run_id)
            service_span.set_attribute("ratp.dataset_version_id", self.dataset_version_id)

            backfill_api_requests.add(
                1,
                {
                    "component": component,
                },
            )

            try:
                with start_span(
                    "market_backfill_service.fetch_bars",
                    timespan=SpanTimespan.REQUEST,
                ) as request_span:
                    request_span.set_attribute("ratp.run_id", self.run_id)
                    request_span.set_attribute("ratp.component", component)
                    request_span.set_attribute("ratp.symbol_count", len(symbols))
                    request_span.set_attribute("ratp.backfill_start", start.isoformat())
                    request_span.set_attribute("ratp.backfill_end", end.isoformat())
                    request_span.set_attribute("ratp.ingestion_run_id", self.ingestion_run_id)
                    request_span.set_attribute("ratp.dataset_version_id", self.dataset_version_id)

                    bars = self.historical_client.fetch_bars(
                        symbols=symbols,
                        start=start,
                        end=end,
                    )
            except Exception as exc:
                request_duration = perf_counter() - request_start
                backfill_request_latency_seconds.record(
                    request_duration,
                    {
                        "component": component,
                        "status": "failed",
                    },
                )
                record_operation_failed(
                    logger=logger,
                    event="market_backfill_service_fetch_failed",
                    run_id=self.run_id,
                    component=component,
                    exc=exc,
                    duration_seconds=request_duration,
                    symbol_count=len(symbols),
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
                raise

            request_duration = perf_counter() - request_start
            backfill_request_latency_seconds.record(
                request_duration,
                {
                    "component": component,
                    "status": "completed",
                },
            )

            bars = list(bars)

            backfill_batch_size.record(
                len(bars),
                {
                    "component": component,
                },
            )

            record_operation_completed(
                logger=logger,
                event="market_backfill_service_fetch_completed",
                run_id=self.run_id,
                component=component,
                bar_count=len(bars),
                duration_seconds=request_duration,
                symbol_count=len(symbols),
                start=start.isoformat(),
                end=end.isoformat(),
            )

            grouped = self._group_bars_by_symbol_date(bars)
            processed_bars = 0

            for (symbol, bar_date), chunk_bars in grouped.items():
                processed_bars += await self._process_chunk(
                    symbol=symbol,
                    bar_date=bar_date,
                    provider_bars=chunk_bars,
                )

            service_duration = perf_counter() - service_start
            throughput = processed_bars / service_duration if service_duration > 0 else 0.0

            backfill_throughput.record(
                throughput,
                {
                    "component": component,
                },
            )

            service_span.set_attribute("ratp.backfill.processed_bars", processed_bars)
            service_span.set_attribute("ratp.backfill.duration_seconds", service_duration)
            service_span.set_attribute("ratp.backfill.throughput_bars_per_second", throughput)

            record_operation_completed(
                logger=logger,
                event="market_backfill_service_completed",
                run_id=self.run_id,
                component=component,
                processed_bars=processed_bars,
                duration_seconds=service_duration,
                throughput_bars_per_second=throughput,
            )

    async def _process_chunk(
        self,
        symbol: str,
        bar_date: date,
        provider_bars: list,
    ) -> int:
        checkpoint_id = f"{self.ingestion_run_id}:{symbol}:{bar_date.isoformat()}"

        with SorUnitOfWork(self.session) as uow:
            checkpoint = uow.ingestion_checkpoints.get_backfill_checkpoint(
                ingestion_run_id=self.ingestion_run_id,
                dataset_version=self.dataset_version_id,
                symbol=symbol,
                checkpoint_date=bar_date,
            )

            if (
                checkpoint is not None
                and checkpoint.checkpoint_status == CheckpointStatus.COMPLETED
            ):
                return 0

            if checkpoint is None:
                checkpoint = IngestionCheckpoint(
                    checkpoint_id=checkpoint_id,
                    ingestion_run_id=self.ingestion_run_id,
                    dataset_version=self.dataset_version_id,
                    checkpoint_scope=CheckpointScope.BACKFILL,
                    checkpoint_status=CheckpointStatus.PENDING,
                    symbol=symbol,
                    checkpoint_date=bar_date,
                    cycle_timestamp=None,
                    last_successful_bar_timestamp=None,
                    retry_count=0,
                    error_message=None,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                uow.ingestion_checkpoints.insert(checkpoint)

            checkpoint.checkpoint_status = CheckpointStatus.IN_PROGRESS
            checkpoint.updated_at = datetime.now(UTC)

            uow.ingestion_checkpoints.upsert(checkpoint)

        processed_bars = 0
        chunk_completed_bars: list[MarketBar] = []

        try:
            for provider_bar in provider_bars:
                completed_bar = await self.bar_ingestion_service.handle_minute_bar(provider_bar)
                # print(
                #    "PROVIDER BAR:",
                #    provider_bar.symbol,
                #    provider_bar.timestamp,
                #    "COMPLETED:",
                #    completed_bar is not None,
                #    "REASON:",
                #    self.bar_ingestion_service.next_bar_decision.reason,
                # )
                if completed_bar is not None:
                    chunk_completed_bars.append(completed_bar)
                    processed_bars += 1

                    checkpoint.last_successful_bar_timestamp = completed_bar.timestamp

            self._finalize_chunk_outputs(
                symbol=symbol,
                bar_date=bar_date,
                chunk_completed_bars=chunk_completed_bars,
            )

            with SorUnitOfWork(self.session) as uow:
                completed_checkpoint = uow.ingestion_checkpoints.get_backfill_checkpoint(
                    ingestion_run_id=self.ingestion_run_id,
                    dataset_version=self.dataset_version_id,
                    symbol=symbol,
                    checkpoint_date=bar_date,
                )

                if completed_checkpoint is None:
                    raise RuntimeError(f"Checkpoint missing before completion: {checkpoint_id}")

                completed_checkpoint.checkpoint_status = CheckpointStatus.COMPLETED
                completed_checkpoint.last_successful_bar_timestamp = (
                    chunk_completed_bars[-1].timestamp if chunk_completed_bars else None
                )
                completed_checkpoint.error_message = None
                completed_checkpoint.updated_at = datetime.now(UTC)

                uow.ingestion_checkpoints.upsert(completed_checkpoint)

            return processed_bars

        except Exception as exc:
            checkpoint.checkpoint_status = CheckpointStatus.FAILED
            checkpoint.error_message = str(exc)
            checkpoint.retry_count += 1
            checkpoint.updated_at = datetime.now(UTC)

            with SorUnitOfWork(self.session) as uow:
                uow.ingestion_checkpoints.upsert(checkpoint)

            raise

    def _finalize_chunk_outputs(
        self,
        symbol: str,
        bar_date: date,
        chunk_completed_bars: list[MarketBar],
    ) -> None:
        self.bar_chunk_writer.write_backfill_chunk(
            bars=chunk_completed_bars,
            dataset_version=self.dataset_version_id,
            symbol=symbol,
            bar_date=bar_date,
        )

        coverage_rows = self._build_symbol_date_coverage_rows_for_chunk(
            symbol,
            bar_date,
            chunk_completed_bars,
        )

        incidents = self._build_missing_bar_incidents_for_chunk(
            symbol=symbol,
            bar_date=bar_date,
            bars=chunk_completed_bars,
        )

        self.ingestion_quality_recorder_service.record_symbol_date_quality(
            coverage_rows=coverage_rows,
            incidents=incidents,
        )

    def _group_bars_by_symbol_date(self, bars) -> dict[tuple[str, date], list]:
        grouped: dict[tuple[str, date], list[MarketBar]] = defaultdict(list)
        for bar in bars:
            grouped[(bar.symbol, bar.timestamp.date())].append(bar)
        return grouped

    def _build_symbol_date_coverage_rows_for_chunk(
        self,
        symbol: str,
        bar_date: date,
        bars: list[MarketBar],
    ) -> list[SymbolDateCoverage]:
        expected_timestamps = self._expected_minute_timestamps_for_date(bar_date)
        actual_timestamps = {bar.timestamp for bar in bars}

        expected_bar_count = len(expected_timestamps)
        actual_bar_count = len(actual_timestamps)

        missing_count = max(expected_bar_count - actual_bar_count, 0)

        if expected_bar_count == 0:
            completeness_status = "missing"
            gap_summary = "No expected market session for date."
        elif missing_count == 0:
            completeness_status = "complete"
            gap_summary = None
        elif actual_bar_count == 0:
            completeness_status = "missing"
            gap_summary = f"Missing all expected bars ({expected_bar_count})."
        else:
            completeness_status = "partial"
            gap_summary = f"Missing {missing_count} of {expected_bar_count} expected bars."

        return [
            SymbolDateCoverage(
                coverage_id=f"{self.dataset_version_id}:{symbol}:{bar_date.isoformat()}",
                symbol=symbol,
                date=bar_date,
                dataset_version=self.dataset_version_id,
                expected_bar_count=expected_bar_count,
                actual_bar_count=actual_bar_count,
                completeness_status=completeness_status,
                gap_summary=gap_summary,
                updated_at=datetime.now(UTC),
            )
        ]

    def _build_missing_bar_incidents_for_chunk(
        self,
        symbol: str,
        bar_date: date,
        bars: list[MarketBar],
    ) -> list[MissingBarIncidents]:
        expected_timestamps = self._expected_minute_timestamps_for_date(bar_date)
        actual_timestamps = {bar.timestamp for bar in bars}

        missing_timestamps = sorted(expected_timestamps - actual_timestamps)

        incidents: list[MissingBarIncidents] = []
        now = datetime.now(UTC)

        for ts in missing_timestamps:
            incidents.append(
                MissingBarIncidents(
                    incident_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"{self.dataset_version_id}:{symbol}:{ts.isoformat()}:missing_bar",
                        )
                    ),
                    symbol=symbol,
                    bar_timestamp=ts,
                    dataset_version=self.dataset_version_id,
                    ingestion_run_id=self.ingestion_run_id,
                    incident_type="missing_bar",
                    severity="warning",
                    message=f"Missing historical bar for {symbol} at {ts.isoformat()}",
                    created_at=now,
                    resolved_flag=False,
                )
            )

        return incidents

    def _expected_minute_timestamps_for_date(self, bar_date: date) -> set[datetime]:
        eastern = ZoneInfo("America/New_York")

        US_MARKET_HOLIDAYS_2025 = {
            date(2025, 1, 1),  # New Year's Day
            date(2025, 1, 20),  # MLK
            date(2025, 2, 17),  # Presidents Day
            date(2025, 4, 18),  # Good Friday
            date(2025, 5, 26),  # Memorial Day
            date(2025, 6, 19),  # Juneteenth
            date(2025, 7, 4),  # Independence Day
            date(2025, 9, 1),  # Labor Day
            date(2025, 11, 27),  # Thanksgiving
            date(2025, 12, 25),  # Christmas
        }

        if bar_date.weekday() >= 5 or bar_date in US_MARKET_HOLIDAYS_2025:
            return set()

        session_start = datetime.combine(
            bar_date,
            time(hour=9, minute=30),
            tzinfo=eastern,
        )
        session_end = datetime.combine(
            bar_date,
            time(hour=16, minute=0),
            tzinfo=eastern,
        )

        timestamps: set[datetime] = set()
        current = session_start

        while current < session_end:
            timestamps.add(current.astimezone(UTC))
            current += timedelta(minutes=5)

        return timestamps
