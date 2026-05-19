from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolMembership,
    RawMarketPoolSnapshot,
    RawMarketSymbol,
)
from autonomous_trading_platform.universe.services.raw_market_pool_refresh_service import (
    RawMarketPoolRefreshService,
)
from autonomous_trading_platform.universe.types import RawSymbolRecord

_D1 = datetime(2025, 1, 13, 14, 30, tzinfo=UTC)
_D2 = datetime(2025, 1, 14, 14, 30, tzinfo=UTC)
_D3 = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)


def _record(
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
    def __init__(self, records: list[RawSymbolRecord]) -> None:
        self._records = records

    @property
    def source_name(self) -> str:
        return "alpaca"

    def fetch_symbols(self) -> list[RawSymbolRecord]:
        return list(self._records)


class StubRepo:
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


def _make_service(repo: StubRepo, records: list[RawSymbolRecord]) -> RawMarketPoolRefreshService:
    return RawMarketPoolRefreshService(repo, StubProvider(records))


class TestIPOTracking:
    def test_new_symbol_detected_as_ipo(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("AAPL"), _record("MSFT")]).refresh("daily", _D1)

        _snap, new_syms, _delisted = _make_service(
            repo, [_record("AAPL"), _record("MSFT"), _record("NEWCO")]
        ).refresh("daily", _D2)

        assert "NEWCO" in new_syms

    def test_ipo_first_seen_set_to_refresh_time(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("AAPL")]).refresh("daily", _D1)
        _make_service(repo, [_record("AAPL"), _record("NEWCO")]).refresh("daily", _D2)

        assert repo.symbols["NEWCO"].first_seen == _D2

    def test_existing_symbol_first_seen_preserved_after_ipo(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("AAPL")]).refresh("daily", _D1)
        _make_service(repo, [_record("AAPL"), _record("NEWCO")]).refresh("daily", _D2)

        assert repo.symbols["AAPL"].first_seen == _D1


class TestDelistingTracking:
    def test_missing_symbol_detected_as_delisted(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("AAPL"), _record("DEAD")]).refresh("daily", _D1)

        _snap, new_syms, delisted = _make_service(repo, [_record("AAPL")]).refresh("daily", _D2)

        assert "DEAD" in delisted

    def test_delisted_symbol_not_in_new_snapshot_memberships(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("AAPL"), _record("DEAD")]).refresh("daily", _D1)
        snap2, _, _ = _make_service(repo, [_record("AAPL")]).refresh("daily", _D2)

        snap2_symbols = repo.get_symbols_for_snapshot(snap2.snapshot_id)
        assert "DEAD" not in snap2_symbols

    def test_delisted_symbol_last_seen_not_updated_after_removal(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("AAPL"), _record("DEAD")]).refresh("daily", _D1)
        _make_service(repo, [_record("AAPL")]).refresh("daily", _D2)

        # Symbol remains in master registry but last_seen stays at D1
        sym = repo.symbols.get("DEAD")
        assert sym is not None
        assert sym.last_seen == _D1


class TestHaltedSymbols:
    def test_halted_non_tradable_symbol_stored(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("HALT", is_tradable=False, status="halted")]).refresh(
            "daily", _D1
        )

        sym = repo.symbols.get("HALT")
        assert sym is not None
        assert sym.is_tradable is False
        assert sym.status == "halted"

    def test_halted_symbol_appears_in_snapshot_memberships(self) -> None:
        repo = StubRepo()
        snap, _, _ = _make_service(
            repo, [_record("AAPL"), _record("HALT", is_tradable=False, status="halted")]
        ).refresh("daily", _D1)

        syms = repo.get_symbols_for_snapshot(snap.snapshot_id)
        assert "HALT" in syms

    def test_symbol_reinstated_after_halt(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("HALT", is_tradable=False, status="halted")]).refresh(
            "daily", _D1
        )
        _make_service(repo, [_record("HALT", is_tradable=True, status="active")]).refresh(
            "daily", _D2
        )

        sym = repo.symbols["HALT"]
        assert sym.is_tradable is True
        assert sym.status == "active"
        assert sym.last_seen == _D2


class TestStatusTransitions:
    def test_status_updates_on_refresh(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("SYM", status="active")]).refresh("daily", _D1)
        _make_service(repo, [_record("SYM", status="inactive")]).refresh("daily", _D2)

        assert repo.symbols["SYM"].status == "inactive"

    def test_tradable_flag_updates_on_refresh(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("SYM", is_tradable=True)]).refresh("daily", _D1)
        _make_service(repo, [_record("SYM", is_tradable=False)]).refresh("daily", _D2)

        assert repo.symbols["SYM"].is_tradable is False

    def test_last_seen_advances_each_refresh(self) -> None:
        repo = StubRepo()
        _make_service(repo, [_record("AAPL")]).refresh("daily", _D1)
        _make_service(repo, [_record("AAPL")]).refresh("daily", _D2)
        _make_service(repo, [_record("AAPL")]).refresh("daily", _D3)

        assert repo.symbols["AAPL"].last_seen == _D3


class TestSymbolLifecycleSummary:
    def test_multi_day_lifecycle(self) -> None:
        repo = StubRepo()

        # Day 1: AAPL, MSFT, DEAD
        _make_service(repo, [_record("AAPL"), _record("MSFT"), _record("DEAD")]).refresh(
            "daily", _D1
        )
        # Day 2: AAPL, MSFT, NEWCO (DEAD delisted, NEWCO IPO)
        _make_service(repo, [_record("AAPL"), _record("MSFT"), _record("NEWCO")]).refresh(
            "daily", _D2
        )

        assert repo.symbols["AAPL"].first_seen == _D1
        assert repo.symbols["DEAD"].first_seen == _D1
        assert repo.symbols["DEAD"].last_seen == _D1
        assert repo.symbols["NEWCO"].first_seen == _D2
        assert repo.symbols["NEWCO"].last_seen == _D2
