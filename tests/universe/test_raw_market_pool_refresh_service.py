from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolMembership,
    RawMarketPoolSnapshot,
    RawMarketSymbol,
)
from autonomous_trading_platform.universe.services.raw_market_pool_refresh_service import (
    RawMarketPoolRefreshService,
)
from autonomous_trading_platform.universe.types import RawSymbolRecord

_NOW = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
_EARLIER = datetime(2025, 1, 14, 14, 30, tzinfo=UTC)


def _make_record(
    symbol: str,
    is_tradable: bool = True,
    status: str = "active",
    asset_type: str = "us_equity",
    exchange: str | None = "NASDAQ",
) -> RawSymbolRecord:
    return RawSymbolRecord(
        symbol=symbol,
        asset_type=asset_type,
        status=status,
        is_tradable=is_tradable,
        exchange=exchange,
    )


class StubProvider:
    def __init__(self, records: list[RawSymbolRecord], source: str = "alpaca") -> None:
        self._records = records
        self._source = source

    @property
    def source_name(self) -> str:
        return self._source

    def fetch_symbols(self) -> list[RawSymbolRecord]:
        return list(self._records)


class StubRawMarketPoolRepository:
    def __init__(self) -> None:
        self.snapshots: list[RawMarketPoolSnapshot] = []
        self.memberships: list[RawMarketPoolMembership] = []
        self.symbols: dict[str, RawMarketSymbol] = {}

    def get_latest_complete_snapshot(self) -> RawMarketPoolSnapshot | None:
        complete = [s for s in self.snapshots if s.is_complete]
        return complete[-1] if complete else None

    def get_snapshot_by_id(self, snapshot_id: str) -> RawMarketPoolSnapshot | None:
        for s in self.snapshots:
            if s.snapshot_id == snapshot_id:
                return s
        return None

    def get_symbols_for_snapshot(self, snapshot_id: str) -> list[str]:
        return [m.symbol for m in self.memberships if m.snapshot_id == snapshot_id]

    def insert_snapshot(self, snapshot: RawMarketPoolSnapshot) -> None:
        self.snapshots.append(snapshot)

    def insert_memberships(self, memberships: list[RawMarketPoolMembership]) -> None:
        self.memberships.extend(memberships)

    def mark_snapshot_complete(self, snapshot_id: str) -> None:
        for s in self.snapshots:
            if s.snapshot_id == snapshot_id:
                s.is_complete = True

    def get_raw_market_symbol(self, symbol: str) -> RawMarketSymbol | None:
        return self.symbols.get(symbol)

    def upsert_raw_market_symbol(self, row: RawMarketSymbol) -> None:
        self.symbols[row.symbol] = row


@pytest.fixture
def repo() -> StubRawMarketPoolRepository:
    return StubRawMarketPoolRepository()


def _make_service(
    repo: StubRawMarketPoolRepository,
    records: list[RawSymbolRecord],
) -> RawMarketPoolRefreshService:
    return RawMarketPoolRefreshService(repo, StubProvider(records))


class TestRefresh:
    def test_creates_snapshot_and_memberships(self, repo: StubRawMarketPoolRepository) -> None:
        records = [_make_record("AAPL"), _make_record("MSFT")]
        service = _make_service(repo, records)

        snapshot, new_syms, delisted = service.refresh("daily", _NOW)

        assert snapshot.is_complete is True
        assert snapshot.symbol_count == 2
        assert snapshot.source == "alpaca"
        assert snapshot.cadence == "daily"
        assert len(repo.memberships) == 2
        assert {m.symbol for m in repo.memberships} == {"AAPL", "MSFT"}

    def test_all_symbols_new_on_first_refresh(self, repo: StubRawMarketPoolRepository) -> None:
        records = [_make_record("AAPL"), _make_record("MSFT")]
        service = _make_service(repo, records)

        _snapshot, new_syms, delisted = service.refresh("daily", _NOW)

        assert set(new_syms) == {"AAPL", "MSFT"}
        assert delisted == []

    def test_detects_new_symbol_vs_previous_snapshot(
        self, repo: StubRawMarketPoolRepository
    ) -> None:
        # First refresh
        service1 = _make_service(repo, [_make_record("AAPL"), _make_record("MSFT")])
        service1.refresh("daily", _EARLIER)

        # Second refresh: GOOG added
        service2 = _make_service(
            repo, [_make_record("AAPL"), _make_record("MSFT"), _make_record("GOOG")]
        )
        _snap, new_syms, delisted = service2.refresh("daily", _NOW)

        assert new_syms == ["GOOG"]
        assert delisted == []

    def test_detects_delisted_symbol(self, repo: StubRawMarketPoolRepository) -> None:
        service1 = _make_service(repo, [_make_record("AAPL"), _make_record("MSFT")])
        service1.refresh("daily", _EARLIER)

        service2 = _make_service(repo, [_make_record("AAPL")])
        _snap, new_syms, delisted = service2.refresh("daily", _NOW)

        assert delisted == ["MSFT"]
        assert new_syms == []

    def test_normalizes_symbol_to_uppercase(self, repo: StubRawMarketPoolRepository) -> None:
        records = [_make_record("aapl"), _make_record("msft")]
        service = _make_service(repo, records)
        service.refresh("daily", _NOW)

        symbols = {m.symbol for m in repo.memberships}
        assert symbols == {"AAPL", "MSFT"}

    def test_deduplicates_duplicate_symbols(self, repo: StubRawMarketPoolRepository) -> None:
        records = [_make_record("AAPL"), _make_record("aapl"), _make_record("AAPL")]
        service = _make_service(repo, records)
        service.refresh("daily", _NOW)

        assert len(repo.memberships) == 1
        assert repo.memberships[0].symbol == "AAPL"

    def test_skips_empty_symbols(self, repo: StubRawMarketPoolRepository) -> None:
        records = [_make_record("AAPL"), _make_record("  ")]
        service = _make_service(repo, records)
        service.refresh("daily", _NOW)

        assert len(repo.memberships) == 1

    def test_upserts_raw_market_symbol_with_first_seen_preserved(
        self, repo: StubRawMarketPoolRepository
    ) -> None:
        service = _make_service(repo, [_make_record("AAPL")])
        service.refresh("daily", _EARLIER)

        service2 = _make_service(repo, [_make_record("AAPL")])
        service2.refresh("daily", _NOW)

        sym = repo.symbols["AAPL"]
        assert sym.first_seen == _EARLIER
        assert sym.last_seen == _NOW

    def test_new_symbol_first_seen_equals_last_seen(
        self, repo: StubRawMarketPoolRepository
    ) -> None:
        service = _make_service(repo, [_make_record("AAPL")])
        service.refresh("daily", _NOW)

        sym = repo.symbols["AAPL"]
        assert sym.first_seen == _NOW
        assert sym.last_seen == _NOW

    def test_stores_tradable_flag_in_membership(self, repo: StubRawMarketPoolRepository) -> None:
        records = [_make_record("AAPL", is_tradable=True), _make_record("ZZZZ", is_tradable=False)]
        service = _make_service(repo, records)
        service.refresh("daily", _NOW)

        by_symbol = {m.symbol: m for m in repo.memberships}
        assert by_symbol["AAPL"].is_tradable is True
        assert by_symbol["ZZZZ"].is_tradable is False

    def test_snapshot_metadata_contains_lifecycle_counts(
        self, repo: StubRawMarketPoolRepository
    ) -> None:
        service = _make_service(repo, [_make_record("AAPL")])
        service.refresh("daily", _EARLIER)

        service2 = _make_service(repo, [_make_record("AAPL"), _make_record("GOOG")])
        snap, _, _ = service2.refresh("daily", _NOW)

        assert snap.metadata_json is not None
        assert snap.metadata_json["new_symbol_count"] == 1
        assert snap.metadata_json["delisted_symbol_count"] == 0


class TestNormalizeAndDeduplicate:
    def test_sorts_output_by_symbol(self) -> None:
        records = [_make_record("MSFT"), _make_record("AAPL"), _make_record("GOOG")]
        result = RawMarketPoolRefreshService._normalize_and_deduplicate(records)
        assert [r.symbol for r in result] == ["AAPL", "GOOG", "MSFT"]

    def test_first_occurrence_wins_for_duplicates(self) -> None:
        records = [
            _make_record("AAPL", exchange="NASDAQ"),
            _make_record("AAPL", exchange="NYSE"),
        ]
        result = RawMarketPoolRefreshService._normalize_and_deduplicate(records)
        assert len(result) == 1
        assert result[0].exchange == "NASDAQ"
