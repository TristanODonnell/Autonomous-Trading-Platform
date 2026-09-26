"""Survivorship fixes: point-in-time index membership, PIT screener, PIT version lookup."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.providers.point_in_time_index_provider import (
    PointInTimeIndexProvider,
    build_universe_screener,
)
from autonomous_trading_platform.universe.services.index_constituents_service import (
    load_sp500_constituents,
    parse_membership_csv,
)
from autonomous_trading_platform.universe.services.universe_history_service import (
    UniverseHistoryService,
)
from tests.utilities.universe_seeding import seed_universe_version


class TestIndexConstituents:
    def test_membership_spells_with_exclusive_end(self) -> None:
        c = parse_membership_csv(
            [
                "ticker,start_date,end_date",
                "OLD,2010-01-04,2022-06-09",
                "NEW,2022-06-09,",
                "GONE,2015-01-02,2023-03-15",
            ],
            index_name="test",
        )
        assert c.members_as_of(date(2022, 6, 8)) == ["GONE", "OLD"]
        # Rename day: the old ticker is out, the new one in.
        assert c.members_as_of(date(2022, 6, 9)) == ["GONE", "NEW"]
        assert c.was_member("GONE", date(2023, 3, 14))
        assert not c.was_member("GONE", date(2023, 3, 15))
        assert c.members_as_of(date(2009, 1, 1)) == []

    def test_rejects_empty_file(self) -> None:
        with pytest.raises(ValueError, match="No membership rows"):
            parse_membership_csv(["ticker,start_date,end_date"], index_name="x")

    def test_bundled_sp500_contains_since_delisted_names(self) -> None:
        sp500 = load_sp500_constituents()
        on_2023_01_03 = set(sp500.members_as_of(date(2023, 1, 3)))

        assert 495 <= len(on_2023_01_03) <= 510
        # Failed banks / acquired companies that Alpaca's asset list no longer knows.
        assert {"SIVB", "FRC", "ATVI", "PXD"} <= on_2023_01_03
        assert "META" in on_2023_01_03 and "FB" not in on_2023_01_03
        # SVB was removed on 2023-03-15.
        assert not sp500.was_member("SIVB", date(2023, 3, 15))
        # Companies that joined later are not in the 2023 list.
        assert "DOC" not in on_2023_01_03


class _FakeBars:
    """Stands in for Alpaca's historical data client."""

    def __init__(self, dollar_volume: dict[str, float]) -> None:
        self._dv = dollar_volume
        self.requested: list[str] = []

    def get_stock_bars(self, request):
        symbols = list(request.symbol_or_symbols)
        self.requested.extend(symbols)
        data = {
            s: [SimpleNamespace(close=100.0, volume=self._dv[s] / 100.0)]
            for s in symbols
            if s in self._dv
        }
        return SimpleNamespace(data=data)


class TestPointInTimeIndexProvider:
    def _constituents(self):
        return parse_membership_csv(
            [
                "ticker,start_date,end_date",
                "AAA,2020-01-02,",
                "DEAD,2020-01-02,2023-03-15",
                "LATER,2024-01-02,",
                "BRK.B,2020-01-02,",
            ],
            index_name="t",
        )

    def test_ranks_point_in_time_members_only(self) -> None:
        bars = _FakeBars({"AAA": 1e7, "DEAD": 5e7, "LATER": 9e9})
        provider = PointInTimeIndexProvider(
            as_of=date(2023, 1, 3),
            top_n=10,
            constituents=self._constituents(),
            data_client_factory=lambda: bars,
        )
        records = provider.fetch_symbols()

        assert [r.symbol for r in records] == ["DEAD", "AAA"]  # by dollar volume
        assert "LATER" not in bars.requested  # not a member yet — never even priced
        assert "BRK.B" not in bars.requested  # same ticker filter as the Alpaca screener

    def test_top_n_and_min_dollar_volume(self) -> None:
        bars = _FakeBars({"AAA": 1e7, "DEAD": 1e5})
        provider = PointInTimeIndexProvider(
            as_of=date(2023, 1, 3),
            top_n=1,
            constituents=self._constituents(),
            data_client_factory=lambda: bars,
        )
        assert [r.symbol for r in provider.fetch_symbols()] == ["AAA"]

    def test_factory(self) -> None:
        assert isinstance(
            build_universe_screener("sp500_point_in_time", as_of=date(2023, 1, 3), top_n=5),
            PointInTimeIndexProvider,
        )
        with pytest.raises(ValueError, match="Unknown screener source"):
            build_universe_screener("nasdaq_magic", as_of=date(2023, 1, 3), top_n=5)


class TestPointInTimeVersionLookup:
    def test_retired_versions_answer_historical_dates(self, db_session) -> None:
        jan = datetime(2023, 1, 3, tzinfo=UTC)
        feb = datetime(2023, 2, 1, tzinfo=UTC)
        v1 = seed_universe_version(db_session, symbols=["SIVB", "AAPL"], effective_from=jan)
        v2 = seed_universe_version(db_session, symbols=["AAPL", "MSFT"], effective_from=feb)
        repo = UniverseVersionRepository(db_session)
        mid_jan = datetime(2023, 1, 15, tzinfo=UTC)

        # The "current" lookup only sees the ACTIVE version...
        assert repo.get_active_version(mid_jan) is None
        # ...the point-in-time lookup finds the since-retired January universe.
        in_jan = repo.get_version_effective_at(mid_jan)
        in_feb = repo.get_version_effective_at(datetime(2023, 2, 10, tzinfo=UTC))
        assert in_jan is not None and in_jan.universe_version_id == v1
        assert in_feb is not None and in_feb.universe_version_id == v2

        history = UniverseHistoryService(version_repo=repo, rotation_repo=None)  # type: ignore[arg-type]
        assert sorted(history.get_included_symbols_as_of(mid_jan)) == ["AAPL", "SIVB"]
