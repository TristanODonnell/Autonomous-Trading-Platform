from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alpaca.data.models.bars import Bar
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    BarQualityFlag,
    MarketSession,
    PriceBasis,
)
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.ingestion.helpers.bar_identity import build_bar_id
from autonomous_trading_platform.ingestion.helpers.session import classify_market_session
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService

from .bar_aggregation_service import BarAggregationService
from .bar_validation_service import BarValidationService


@dataclass(frozen=True)
class NextBarDecision:
    should_schedule_evaluation: bool
    reason: str
    bar: MarketBar | None = None


# A completed 5-minute bar validated more than this long after its window ended is
# flagged LATE and audited (BAR_LATE). It is still stored. Overridable with
# INGESTION_LATE_BAR_SECONDS. Keep it above the trading cycle's ingestion grace (60 s)
# so a healthy day is quiet and the flag means "the pipeline is slow", not "normal".
DEFAULT_LATE_BAR_ALLOWED_SECONDS = 120
LATE_BAR_ALLOWED_SECONDS_ENV = "INGESTION_LATE_BAR_SECONDS"


def resolve_late_bar_allowed_delay() -> timedelta:
    raw = os.getenv(LATE_BAR_ALLOWED_SECONDS_ENV)
    if raw is None or not raw.strip():
        return timedelta(seconds=DEFAULT_LATE_BAR_ALLOWED_SECONDS)
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError(f"{LATE_BAR_ALLOWED_SECONDS_ENV} must be a number of seconds") from exc
    if seconds < 0:
        raise ValueError(f"{LATE_BAR_ALLOWED_SECONDS_ENV} must be >= 0, got {seconds}")
    return timedelta(seconds=seconds)


class BarIngestionService:
    """
    Handle incoming provider minute bars and convert them into the
    platform's canonical MarketBar contract.
    """

    def __init__(
        self,
        session: Session,
        run_id: str,
        audit_logger: AuditLoggingService,
        *,
        aggregator: BarAggregationService | None = None,
        validation_service: BarValidationService | None = None,
        now_provider: Callable[[], datetime] | None = None,
        enforce_lateness: bool = True,
        late_bar_allowed_delay: timedelta | None = None,
    ) -> None:
        self.enforce_lateness = enforce_lateness
        self.late_bar_allowed_delay = (
            late_bar_allowed_delay
            if late_bar_allowed_delay is not None
            else resolve_late_bar_allowed_delay()
        )
        self.last_bar_by_symbol: dict[str, MarketBar] = {}
        self.aggregator = aggregator or BarAggregationService()
        self.validation_service = validation_service or BarValidationService()
        self.session = session
        self.run_id = run_id
        self.audit_logger = audit_logger
        self.now_provider = now_provider or (lambda: datetime.now(UTC))

        self.next_bar_decision: NextBarDecision = NextBarDecision(
            should_schedule_evaluation=False,
            reason="service_initialized",
            bar=None,
        )

    async def handle_minute_bar(self, provider_bar: Bar) -> MarketBar | None:
        with start_span(
            "bar_ingestion_service.ingest_bar",
            timespan=SpanTimespan.STEP,
        ) as ingest_span:
            ingest_span.set_attribute("ratp.run_id", self.run_id)
            ingest_span.set_attribute("ratp.symbol", provider_bar.symbol)
            minute_bar = self._convert_provider_bar(provider_bar)
            if minute_bar.session != MarketSession.REGULAR:
                self.next_bar_decision = NextBarDecision(
                    should_schedule_evaluation=False,
                    reason="outside_regular_session",
                    bar=minute_bar,
                )
                return None
            five_min_bar = self.aggregator.add_minute_bar(minute_bar)
            if five_min_bar is None:
                self.next_bar_decision = NextBarDecision(
                    should_schedule_evaluation=False,
                    reason="incomplete_bar",
                    bar=None,
                )
                return None

        with start_span(
            "bar_ingestion_service.validate_bar",
            timespan=SpanTimespan.STEP,
        ) as validation_span:
            validation_span.set_attribute("ratp.run_id", self.run_id)
            validation_span.set_attribute("ratp.symbol", five_min_bar.symbol)
            validation_span.set_attribute("ratp.bar_timestamp", five_min_bar.timestamp.isoformat())
            validation_result = self.validation_service.validate_bar(five_min_bar)
            if not validation_result.ok:
                self.next_bar_decision = NextBarDecision(
                    should_schedule_evaluation=False,
                    reason="invalid_bar",
                    bar=five_min_bar,
                )
                return None

            if self.enforce_lateness:
                is_late = self.validation_service.is_late_bar(
                    five_min_bar,
                    now_utc=self.now_provider(),
                    allowed_delay=self.late_bar_allowed_delay,
                )
            else:
                is_late = False
            if is_late:
                five_min_bar.quality_flags.append(BarQualityFlag.LATE)
                self.audit_logger.record_bar_late(
                    run_id=self.run_id,
                    symbol=five_min_bar.symbol,
                    bar_end_timestamp=five_min_bar.end_timestamp,
                )

            previous_bar = self.last_bar_by_symbol.get(five_min_bar.symbol)
            reference_close = previous_bar.close if previous_bar else None

            is_suspected_outlier = self.validation_service.is_suspected_outlier(
                five_min_bar,
                reference_close,
            )
            if is_suspected_outlier:
                five_min_bar.quality_flags.append(BarQualityFlag.SUSPECTED_OUTLIER)

                if reference_close is not None:
                    self.audit_logger.record_bar_outlier(
                        run_id=self.run_id,
                        symbol=five_min_bar.symbol,
                        cycle_timestamp=five_min_bar.timestamp,
                        reference_close=reference_close,
                        observed_close=five_min_bar.close,
                    )

        with start_span(
            "bar_ingestion_service.complete_bar",
            timespan=SpanTimespan.STEP,
        ) as complete_bar_span:
            complete_bar_span.set_attribute("ratp.run_id", self.run_id)
            complete_bar_span.set_attribute("ratp.symbol", five_min_bar.symbol)
            complete_bar_span.set_attribute(
                "ratp.bar_timestamp", five_min_bar.timestamp.isoformat()
            )

            # A late bar is still what the market did in its window; only our fetch was
            # slow. It is kept (flagged LATE, audited above) so the record stays complete.
            # Whether it is fresh enough to trade on is the trading cycle's call
            # (check_ingestion_readiness_job), not ingestion's. Before 2026-10-15 late
            # bars were dropped here, and once every symbol of a tick was late the cycle
            # had nothing to finalize: no missing-bar record, no error, no Parquet write.
            self.last_bar_by_symbol[five_min_bar.symbol] = five_min_bar

            self.next_bar_decision = NextBarDecision(
                should_schedule_evaluation=not is_late,
                reason="late_bar" if is_late else "complete_valid_bar",
                bar=five_min_bar,
            )
            return five_min_bar

    @staticmethod
    def _convert_provider_bar(provider_bar: Bar) -> MarketBar:
        """
        Convert a provider-specific minute bar into the platform's
        canonical MarketBar model.
        """

        ts = provider_bar.timestamp

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        timestamp_utc = ts.astimezone(UTC)
        ingested_at = datetime.now(UTC)

        return MarketBar(
            bar_id=build_bar_id(
                symbol=provider_bar.symbol,
                timestamp=timestamp_utc,
                interval=BarInterval.ONE_MIN,
                price_basis=PriceBasis.RAW,
            ),
            timestamp=timestamp_utc,
            end_timestamp=timestamp_utc + timedelta(minutes=1),
            interval=BarInterval.ONE_MIN,
            symbol=provider_bar.symbol,
            open=Decimal(str(provider_bar.open)),
            high=Decimal(str(provider_bar.high)),
            low=Decimal(str(provider_bar.low)),
            close=Decimal(str(provider_bar.close)),
            volume=int(provider_bar.volume),
            vwap=Decimal(str(provider_bar.vwap)) if provider_bar.vwap is not None else None,
            trade_count=int(provider_bar.trade_count)
            if provider_bar.trade_count is not None
            else None,
            price_basis=PriceBasis.RAW,
            adjustment_factor=Decimal("1"),
            source="alpaca",
            ingested_at=ingested_at,
            quality_flags=[],
            session=classify_market_session(timestamp_utc),
        )
