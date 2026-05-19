from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolMembership,
    RawMarketPoolSnapshot,
    RawMarketSymbol,
)
from autonomous_trading_platform.storage.sor.services.raw_market_pool_query_service import (
    RawMarketPoolQueryService,
)

_T1 = datetime(2025, 1, 14, 14, 30, tzinfo=UTC)
_T2 = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
_T3 = datetime(2025, 1, 16, 14, 30, tzinfo=UTC)


def _snap(
    snapshot_id: str, captured_at: datetime, is_complete: bool = True
) -> RawMarketPoolSnapshot:
    return RawMarketPoolSnapshot(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        source="alpaca",
        symbol_count=0,
        cadence="daily",
        is_complete=is_complete,
        metadata_json=None,
    )


def _membership(
    snapshot_id: str,
    symbol: str,
    is_tradable: bool = True,
    asset_type: str = "us_equity",
    exchange: str | None = "NASDAQ",
) -> RawMarketPoolMembership:
    return RawMarketPoolMembership(
        snapshot_id=snapshot_id,
        symbol=symbol,
        is_tradable=is_tradable,
        status="active",
        asset_type=asset_type,
        exchange=exchange,
    )


class StubRawMarketPoolRepository:
    def __init__(self) -> None:
        self.snapshots: list[RawMarketPoolSnapshot] = []
        self.memberships: list[RawMarketPoolMembership] = []
        self.symbols: dict[str, RawMarketSymbol] = {}

    def get_latest_complete_snapshot(self) -> RawMarketPoolSnapshot | None:
        complete = [s for s in self.snapshots if s.is_complete]
        return complete[-1] if complete else None

    def get_latest_complete_snapshot_as_of(self, as_of: datetime) -> RawMarketPoolSnapshot | None:
        complete = [s for s in self.snapshots if s.is_complete and s.captured_at <= as_of]
        return complete[-1] if complete else None

    def get_memberships_filtered(
        self,
        snapshot_id: str,
        *,
        asset_type: str | None = None,
        exchange: str | None = None,
        tradable_only: bool = False,
    ) -> list[RawMarketPoolMembership]:
        result = [m for m in self.memberships if m.snapshot_id == snapshot_id]
        if asset_type is not None:
            result = [m for m in result if m.asset_type == asset_type]
        if exchange is not None:
            result = [m for m in result if m.exchange == exchange]
        if tradable_only:
            result = [m for m in result if m.is_tradable]
        return sorted(result, key=lambda m: m.symbol)

    def get_raw_market_symbol(self, symbol: str) -> RawMarketSymbol | None:
        return self.symbols.get(symbol)


@pytest.fixture
def repo() -> StubRawMarketPoolRepository:
    r = StubRawMarketPoolRepository()
    snap1 = _snap("snap-1", _T1)
    snap2 = _snap("snap-2", _T2)
    r.snapshots.extend([snap1, snap2])
    r.memberships.extend(
        [
            _membership("snap-1", "AAPL"),
            _membership("snap-1", "MSFT"),
            _membership("snap-2", "AAPL"),
            _membership("snap-2", "MSFT"),
            _membership("snap-2", "GOOG"),
            _membership("snap-2", "ZZZZ", is_tradable=False),
            _membership("snap-2", "BTC", asset_type="crypto", exchange=None),
        ]
    )
    return r


@pytest.fixture
def service(repo: StubRawMarketPoolRepository) -> RawMarketPoolQueryService:
    return RawMarketPoolQueryService(repo)


class TestGetLatestSnapshot:
    def test_returns_most_recent_complete(self, service: RawMarketPoolQueryService) -> None:
        snap = service.get_latest_snapshot()
        assert snap is not None
        assert snap.snapshot_id == "snap-2"

    def test_returns_none_when_no_snapshots(self) -> None:
        svc = RawMarketPoolQueryService(StubRawMarketPoolRepository())
        assert svc.get_latest_snapshot() is None


class TestGetSnapshotAsOf:
    def test_returns_snapshot_at_exact_time(self, service: RawMarketPoolQueryService) -> None:
        snap = service.get_snapshot_as_of(_T2)
        assert snap is not None
        assert snap.snapshot_id == "snap-2"

    def test_returns_previous_snapshot_before_newer(
        self, service: RawMarketPoolQueryService
    ) -> None:
        snap = service.get_snapshot_as_of(_T1)
        assert snap is not None
        assert snap.snapshot_id == "snap-1"

    def test_returns_none_before_any_snapshot(self, service: RawMarketPoolQueryService) -> None:
        before = datetime(2025, 1, 1, tzinfo=UTC)
        assert service.get_snapshot_as_of(before) is None

    def test_historical_reconstruction_uses_correct_snapshot(
        self, service: RawMarketPoolQueryService
    ) -> None:
        symbols_at_t1 = service.get_active_tradable_symbols(_T1)
        symbols_at_t2 = service.get_active_tradable_symbols(_T2)

        assert set(symbols_at_t1) == {"AAPL", "MSFT"}
        assert "GOOG" in symbols_at_t2
        assert "GOOG" not in symbols_at_t1


class TestGetActiveTradableSymbols:
    def test_excludes_non_tradable(self, service: RawMarketPoolQueryService) -> None:
        symbols = service.get_active_tradable_symbols(_T2)
        assert "ZZZZ" not in symbols

    def test_filters_by_asset_type(self, service: RawMarketPoolQueryService) -> None:
        equities = service.get_active_tradable_symbols(_T2, asset_type="us_equity")
        assert "BTC" not in equities

        crypto = service.get_active_tradable_symbols(_T2, asset_type="crypto")
        assert crypto == ["BTC"]

    def test_filters_by_exchange(self, service: RawMarketPoolQueryService) -> None:
        nasdaq = service.get_active_tradable_symbols(_T2, exchange="NASDAQ")
        assert all(m in nasdaq for m in ["AAPL", "MSFT", "GOOG"])

    def test_returns_sorted_symbols(self, service: RawMarketPoolQueryService) -> None:
        symbols = service.get_active_tradable_symbols(_T2)
        assert symbols == sorted(symbols)

    def test_returns_empty_when_no_snapshots(self) -> None:
        svc = RawMarketPoolQueryService(StubRawMarketPoolRepository())
        assert svc.get_active_tradable_symbols() == []


class TestIsSymbolEligible:
    def test_eligible_tradable_symbol(self, service: RawMarketPoolQueryService) -> None:
        assert service.is_symbol_eligible("AAPL") is True

    def test_ineligible_non_tradable_symbol(self, service: RawMarketPoolQueryService) -> None:
        assert service.is_symbol_eligible("ZZZZ") is False

    def test_ineligible_unknown_symbol(self, service: RawMarketPoolQueryService) -> None:
        assert service.is_symbol_eligible("UNKNOWN") is False

    def test_as_of_historical(self, service: RawMarketPoolQueryService) -> None:
        assert service.is_symbol_eligible("GOOG", _T1) is False
        assert service.is_symbol_eligible("GOOG", _T2) is True


class TestGetSymbolCount:
    def test_returns_count_of_tradable_symbols(self, service: RawMarketPoolQueryService) -> None:
        count = service.get_symbol_count(_T2)
        # snap-2 has AAPL, MSFT, GOOG tradable + BTC tradable, ZZZZ non-tradable
        assert count == 4
