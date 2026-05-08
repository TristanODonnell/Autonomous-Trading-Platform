from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest

from autonomous_trading_platform.contracts.runtime.ticker_lifecycle_event import (
    TickerLifecycleEventType,
)
from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
    TickerLifecycleService,
)


@dataclass
class StubTickerLifecycleEvent:
    symbol: str
    event_type: TickerLifecycleEventType
    successor_symbol: str | None = None


class StubTickerLifecycleRepository:
    def __init__(self) -> None:
        self.events_by_symbol: dict[str, StubTickerLifecycleEvent | None] = {}
        self.calls: list[tuple[str, datetime]] = []

    def get_latest_event_for_symbol_as_of(
        self,
        symbol: str,
        as_of: datetime,
    ) -> StubTickerLifecycleEvent | None:
        self.calls.append((symbol, as_of))
        return self.events_by_symbol.get(symbol)


@pytest.fixture
def repository() -> StubTickerLifecycleRepository:
    return StubTickerLifecycleRepository()


@pytest.fixture
def service(
    repository: StubTickerLifecycleRepository,
) -> TickerLifecycleService:
    return TickerLifecycleService(repository=cast(TickerLifecycleRepository, repository))


class TestTickerLifecycleService:
    def test_get_successor_symbol_uses_repository_for_rename_event(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
        repository.events_by_symbol["FB"] = StubTickerLifecycleEvent(
            symbol="FB",
            event_type=TickerLifecycleEventType.RENAME,
            successor_symbol="META",
        )

        successor = service.get_successor_symbol("FB", as_of)

        assert successor == "META"
        assert repository.calls == [("FB", as_of)]

    def test_get_successor_symbol_uses_repository_for_merger_event(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
        repository.events_by_symbol["ATVI"] = StubTickerLifecycleEvent(
            symbol="ATVI",
            event_type=TickerLifecycleEventType.MERGER,
            successor_symbol="MSFT",
        )

        successor = service.get_successor_symbol("ATVI", as_of)

        assert successor == "MSFT"

    def test_get_successor_symbol_returns_none_when_no_event_exists(
        self,
        service: TickerLifecycleService,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        successor = service.get_successor_symbol("AAPL", as_of)

        assert successor is None

    def test_get_successor_symbol_returns_none_for_delisting_event(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
        repository.events_by_symbol["OLD"] = StubTickerLifecycleEvent(
            symbol="OLD",
            event_type=TickerLifecycleEventType.DELISTING,
            successor_symbol=None,
        )

        successor = service.get_successor_symbol("OLD", as_of)

        assert successor is None

    def test_is_delisted_returns_true_for_delisting_event(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
        repository.events_by_symbol["OLD"] = StubTickerLifecycleEvent(
            symbol="OLD",
            event_type=TickerLifecycleEventType.DELISTING,
            successor_symbol=None,
        )

        assert service.is_delisted("OLD", as_of) is True

    def test_is_delisted_returns_false_when_no_event_exists(
        self,
        service: TickerLifecycleService,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        assert service.is_delisted("AAPL", as_of) is False

    def test_resolve_symbol_returns_same_symbol_when_no_replacement_found(
        self,
        service: TickerLifecycleService,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        resolved = service.resolve_symbol("AAPL", as_of)

        assert resolved == "AAPL"

    def test_resolve_symbol_chain_handles_rename_then_merger(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        repository.events_by_symbol["ABC"] = StubTickerLifecycleEvent(
            symbol="ABC",
            event_type=TickerLifecycleEventType.RENAME,
            successor_symbol="XYZ",
        )
        repository.events_by_symbol["XYZ"] = StubTickerLifecycleEvent(
            symbol="XYZ",
            event_type=TickerLifecycleEventType.MERGER,
            successor_symbol="MSFT",
        )

        resolved = service.resolve_symbol_chain("ABC", as_of)

        assert resolved == "MSFT"
        assert repository.calls == [
            ("ABC", as_of),
            ("XYZ", as_of),
            ("MSFT", as_of),
        ]

    def test_resolve_symbol_chain_handles_successor_event_chain(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        repository.events_by_symbol["OLD1"] = StubTickerLifecycleEvent(
            symbol="OLD1",
            event_type=TickerLifecycleEventType.SUCCESSOR,
            successor_symbol="OLD2",
        )
        repository.events_by_symbol["OLD2"] = StubTickerLifecycleEvent(
            symbol="OLD2",
            event_type=TickerLifecycleEventType.SUCCESSOR,
            successor_symbol="NEW",
        )

        resolved = service.resolve_symbol_chain("OLD1", as_of)

        assert resolved == "NEW"

    def test_resolve_symbol_chain_stops_on_self_referential_successor(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        repository.events_by_symbol["AAPL"] = StubTickerLifecycleEvent(
            symbol="AAPL",
            event_type=TickerLifecycleEventType.RENAME,
            successor_symbol="AAPL",
        )

        resolved = service.resolve_symbol_chain("AAPL", as_of)

        assert resolved == "AAPL"

    def test_resolve_symbol_chain_stops_on_cycle(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        repository.events_by_symbol["AAA"] = StubTickerLifecycleEvent(
            symbol="AAA",
            event_type=TickerLifecycleEventType.RENAME,
            successor_symbol="BBB",
        )
        repository.events_by_symbol["BBB"] = StubTickerLifecycleEvent(
            symbol="BBB",
            event_type=TickerLifecycleEventType.MERGER,
            successor_symbol="AAA",
        )

        resolved = service.resolve_symbol_chain("AAA", as_of)

        assert resolved == "AAA"

    def test_resolve_symbol_for_universe_cleanup_returns_none_for_delisted_symbol(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
        repository.events_by_symbol["DEAD"] = StubTickerLifecycleEvent(
            symbol="DEAD",
            event_type=TickerLifecycleEventType.DELISTING,
            successor_symbol=None,
        )

        assert service.resolve_symbol_for_universe_cleanup("DEAD", as_of) is None

    def test_service_cleans_universe_list_by_replacing_and_removing_symbols(
        self,
        service: TickerLifecycleService,
        repository: StubTickerLifecycleRepository,
    ) -> None:
        as_of = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

        repository.events_by_symbol["FB"] = StubTickerLifecycleEvent(
            symbol="FB",
            event_type=TickerLifecycleEventType.RENAME,
            successor_symbol="META",
        )
        repository.events_by_symbol["OLD"] = StubTickerLifecycleEvent(
            symbol="OLD",
            event_type=TickerLifecycleEventType.DELISTING,
            successor_symbol=None,
        )

        # Placeholder future API example:
        cleaned = service.resolve_universe_symbols(["FB", "AAPL", "OLD"], as_of)

        assert cleaned == ["META", "AAPL"]
