from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import UniverseSource
from autonomous_trading_platform.storage.sor.services.raw_market_pool_query_service import (
    RawMarketPoolQueryService,
)
from autonomous_trading_platform.universe.services.universe_candidate_builder import (
    UniverseCandidateBuilder,
)
from autonomous_trading_platform.universe.types import CandidateGenerationConfig


class UniverseSelectionService:
    """
    Thin facade over UniverseCandidateBuilder that returns (symbols, metadata) tuples.

    Delegates all candidate-generation logic to UniverseCandidateBuilder — no
    duplicate selection path lives here.
    """

    def __init__(
        self,
        session: Session,
        raw_pool_query_service: RawMarketPoolQueryService,
    ) -> None:
        self._builder = UniverseCandidateBuilder(session, raw_pool_query_service)

    def select_symbols(
        self,
        *,
        as_of: datetime,
        min_price: Decimal = Decimal("1.00"),
        min_average_daily_dollar_volume: Decimal = Decimal("5000000"),
        max_symbols: int = 500,
        lookback_days: int = 20,
        asset_type: str = "us_equity",
    ) -> tuple[list[str], dict[str, Any]]:
        as_of = _ensure_utc(as_of)
        config = CandidateGenerationConfig(
            as_of=as_of,
            lookback_days=lookback_days,
            min_price=min_price,
            min_average_daily_dollar_volume=min_average_daily_dollar_volume,
            max_symbols=max_symbols,
            asset_type=asset_type,
        )
        result = self._builder.build_candidate(
            config=config,
            name=f"selection_{as_of.date().isoformat()}",
            source=UniverseSource.CUSTOM,
        )
        symbols = sorted(m.symbol for m in result.included_members)
        return symbols, result.generation_metadata


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
