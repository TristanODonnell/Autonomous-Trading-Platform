from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alpaca.data.models.bars import Bar
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    BarQualityFlag,
    PriceBasis,
)
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

from ..helpers.bar_identity import build_bar_id
from ..helpers.session import classify_market_session
from .bar_aggregation_service import BarAggregationService
from .bar_validation_service import BarValidationService


class BarIngestionService:
    """
    Handle incoming provider minute bars and convert them into the
    platform's canonical MarketBar contract.
    """

    def __init__(self, session: Session):
        self.aggregator = BarAggregationService()
        self.validation_service = BarValidationService()
        # store last completed 5-minute bar per symbol
        self.last_bar_by_symbol: dict[str, MarketBar] = {}
        self.session = session

    async def handle_minute_bar(self, provider_bar: Bar) -> MarketBar | None:
        minute_bar = self._convert_provider_bar(provider_bar)

        five_min_bar = self.aggregator.add_minute_bar(minute_bar)

        if five_min_bar is None:
            return None

        validation_result = self.validation_service.validate_bar(five_min_bar)

        if not validation_result.ok:
            print(validation_result.violations)
            return None

        is_late = self.validation_service.is_late_bar(
            five_min_bar,
            now_utc=datetime.now(UTC),
            allowed_delay=timedelta(seconds=30),
        )

        if is_late:
            five_min_bar.quality_flags.append(BarQualityFlag.LATE)

        previous_bar = self.last_bar_by_symbol.get(five_min_bar.symbol)
        reference_close = previous_bar.close if previous_bar else None

        is_suspected_outlier = self.validation_service.is_suspected_outlier(
            five_min_bar,
            reference_close,
        )

        if is_suspected_outlier:
            five_min_bar.quality_flags.append(BarQualityFlag.SUSPECTED_OUTLIER)

        with SorUnitOfWork(self.session) as uow:
            uow.market_bars.upsert(five_min_bar)

        if is_late:
            return None

        # update cache for next bar comparison
        self.last_bar_by_symbol[five_min_bar.symbol] = five_min_bar

        print(five_min_bar)
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
            ingested_at=datetime.now(UTC),
            quality_flags=[],
            session=classify_market_session(timestamp_utc),
        )
