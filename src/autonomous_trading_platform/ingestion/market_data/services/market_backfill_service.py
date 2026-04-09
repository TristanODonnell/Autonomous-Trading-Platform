from __future__ import annotations

from datetime import datetime
from time import perf_counter

from sqlalchemy.orm import Session

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
    backfill_symbol_failures,
    backfill_throughput,
    historical_bars_backfilled,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService

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
    ) -> None:
        self.session = session
        self.historical_client = historical_client
        self.run_id = run_id
        self.audit_logger = audit_logger
        self.bar_ingestion_service = BarIngestionService(
            session,
            run_id=run_id,
            audit_logger=audit_logger,
        )

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
            processed_bars = 0

            for provider_bar in bars:
                try:
                    await self.bar_ingestion_service.handle_minute_bar(provider_bar)
                    processed_bars += 1
                    historical_bars_backfilled.add(
                        1,
                        {
                            "component": component,
                            "symbol": provider_bar.symbol,
                        },
                    )
                except ValueError as exc:
                    message = str(exc)
                    if "Incomplete prior bucket detected" in message:
                        logger.warning(
                            "market_backfill_service_retry_incomplete_bucket run_id=%s component=%s symbol=%s error=%s",
                            self.run_id,
                            component,
                            provider_bar.symbol,
                            message,
                        )
                        self.bar_ingestion_service.aggregator.drop_incomplete_buckets_for_symbol(
                            provider_bar.symbol
                        )
                        await self.bar_ingestion_service.handle_minute_bar(provider_bar)
                        processed_bars += 1
                        historical_bars_backfilled.add(
                            1,
                            {
                                "component": component,
                                "symbol": provider_bar.symbol,
                            },
                        )
                        continue

                    backfill_symbol_failures.add(
                        1,
                        {
                            "component": component,
                            "symbol": provider_bar.symbol,
                            "failure_class": "value_error",
                        },
                    )
                    record_operation_failed(
                        logger=logger,
                        event="market_backfill_service_bar_failed",
                        run_id=self.run_id,
                        component=component,
                        exc=exc,
                        symbol=provider_bar.symbol,
                        failure_class="value_error",
                    )
                    raise
                except Exception as exc:
                    backfill_symbol_failures.add(
                        1,
                        {
                            "component": component,
                            "symbol": provider_bar.symbol,
                            "failure_class": "unknown",
                        },
                    )
                    record_operation_failed(
                        logger=logger,
                        event="market_backfill_service_bar_failed",
                        run_id=self.run_id,
                        component=component,
                        exc=exc,
                        symbol=provider_bar.symbol,
                        failure_class="unknown",
                    )
                    raise

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
