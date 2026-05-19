from __future__ import annotations

from datetime import UTC, datetime

from alpaca.trading.client import TradingClient

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.raw_market_pool_repository import (
    RawMarketPoolRepository,
)
from autonomous_trading_platform.universe.services.market_calendar_service import (
    MarketCalendarService,
)
from autonomous_trading_platform.universe.services.raw_market_pool_refresh_service import (
    RawMarketPoolRefreshService,
)
from autonomous_trading_platform.universe.types import RawSymbolProvider, RawSymbolRecord


class AlpacaRawSymbolProvider:
    """Fetches the full tradable symbol list from the Alpaca broker API."""

    def __init__(self, client: TradingClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "alpaca"

    def fetch_symbols(self) -> list[RawSymbolRecord]:
        assets = self._client.get_all_assets()
        records: list[RawSymbolRecord] = []
        for asset in assets:
            symbol = getattr(asset, "symbol", None)
            if not symbol:
                continue
            records.append(
                RawSymbolRecord(
                    symbol=str(symbol),
                    asset_type=str(getattr(asset, "asset_class", "us_equity")),
                    status=str(getattr(asset, "status", "unknown")),
                    is_tradable=bool(getattr(asset, "tradable", False)),
                    exchange=str(getattr(asset, "exchange", None) or "") or None,
                    name=str(getattr(asset, "name", None) or "") or None,
                    currency=None,
                    country=None,
                    marginable=getattr(asset, "marginable", None),
                    shortable=getattr(asset, "shortable", None),
                    fractionable=getattr(asset, "fractionable", None),
                    easy_to_borrow=getattr(asset, "easy_to_borrow", None),
                    provider_symbol=str(symbol),
                    provider_asset_id=str(getattr(asset, "id", "") or "") or None,
                )
            )
        return records


def run_raw_market_pool_refresh(
    *,
    cadence: str | None = None,
    captured_at: datetime | None = None,
    force: bool = False,
) -> None:
    settings = Settings()
    effective_cadence = cadence or settings.universe_rebalance_cadence
    now = _ensure_utc(captured_at or datetime.now(UTC))

    calendar_service = MarketCalendarService()
    if not force and not calendar_service.should_refresh_today(effective_cadence, now):
        return

    api_key = settings.broker_api_key
    secret_key = settings.broker_api_secret

    trading_client = TradingClient(
        api_key,
        secret_key,
        paper=settings.trading_environment == TradingEnvironment.PAPER,
    )

    provider: RawSymbolProvider = AlpacaRawSymbolProvider(trading_client)
    session = get_session()

    raw_pool_repo = RawMarketPoolRepository(session)
    refresh_service = RawMarketPoolRefreshService(raw_pool_repo, provider)

    snapshot, new_symbols, delisted_symbols = refresh_service.refresh(
        cadence=effective_cadence,
        captured_at=now,
    )
    session.commit()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
