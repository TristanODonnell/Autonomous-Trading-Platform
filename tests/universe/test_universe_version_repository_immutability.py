"""
Tests for immutability enforcement in UniverseVersionRepository.

These tests use a lightweight in-memory SQLite database via SQLAlchemy to
verify the repository's lifecycle guards without needing a full Postgres
migration run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    ImmutableVersionError,
    InvalidStatusTransitionError,
    UniverseVersionRepository,
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture
def repo(session: Session) -> UniverseVersionRepository:
    return UniverseVersionRepository(session)


def _make_version(version_id: str, status: str = UniverseStatus.CANDIDATE) -> UniverseVersion:
    now = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
    return UniverseVersion(
        universe_version_id=version_id,
        name=f"test_{version_id}",
        source=UniverseSource.CUSTOM,
        created_at=now,
        effective_from=now,
        effective_to=None,
        status=status,
        rebalance_reason=None,
        config_hash="abc123",
    )


def _make_member(version_id: str, symbol: str) -> UniverseMember:
    return UniverseMember(
        universe_version_id=version_id,
        symbol=symbol,
        rank=0,
        score=None,
        included_reason=None,
        excluded_reason=None,
        liquidity_metrics_json=None,
        quality_metrics_json=None,
    )


class TestUniverseVersionRepositoryLifecycle:
    def test_insert_candidate_version_succeeds(self, repo, session) -> None:
        version = _make_version("v-lifecycle-01")
        repo.insert_version(version)
        session.flush()
        fetched = repo.get_by_version_id("v-lifecycle-01")
        assert fetched is not None
        assert fetched.status == UniverseStatus.CANDIDATE

    def test_activate_candidate_succeeds(self, repo, session) -> None:
        version = _make_version("v-lifecycle-02")
        repo.insert_version(version)
        member = _make_member("v-lifecycle-02", "AAPL")
        repo.insert_members([member])
        session.flush()

        activated = repo.activate_version("v-lifecycle-02")
        assert activated.status == UniverseStatus.ACTIVE

    def test_cannot_insert_version_with_active_status_directly(self, repo) -> None:
        version = _make_version("v-lifecycle-03", status=UniverseStatus.ACTIVE)
        with pytest.raises(ValueError, match="Cannot insert a version with status 'active'"):
            repo.insert_version(version)

    def test_cannot_activate_version_without_members(self, repo, session) -> None:
        version = _make_version("v-lifecycle-04")
        repo.insert_version(version)
        session.flush()

        with pytest.raises(ValueError, match="no members"):
            repo.activate_version("v-lifecycle-04")

    def test_cannot_insert_members_into_active_version(self, repo, session) -> None:
        version = _make_version("v-lifecycle-05")
        repo.insert_version(version)
        member = _make_member("v-lifecycle-05", "MSFT")
        repo.insert_members([member])
        session.flush()
        repo.activate_version("v-lifecycle-05")
        session.flush()

        extra_member = _make_member("v-lifecycle-05", "AAPL")
        with pytest.raises(ImmutableVersionError, match="immutable after activation"):
            repo.insert_members([extra_member])

    def test_retire_active_version_sets_status_and_effective_to(self, repo, session) -> None:
        version = _make_version("v-lifecycle-06")
        repo.insert_version(version)
        member = _make_member("v-lifecycle-06", "AAPL")
        repo.insert_members([member])
        session.flush()
        repo.activate_version("v-lifecycle-06")
        session.flush()

        retire_ts = datetime(2025, 2, 1, tzinfo=UTC)
        retired = repo.retire_active_version(retire_ts)
        assert retired is not None
        assert retired.status == UniverseStatus.RETIRED
        assert retired.effective_to == retire_ts

    def test_retire_when_no_active_version_returns_none(self, repo) -> None:
        result = repo.retire_active_version(datetime(2025, 3, 1, tzinfo=UTC))
        assert result is None

    def test_invalid_transition_from_retired_raises(self, repo, session) -> None:
        version = _make_version("v-lifecycle-07")
        repo.insert_version(version)
        member = _make_member("v-lifecycle-07", "AAPL")
        repo.insert_members([member])
        session.flush()
        repo.activate_version("v-lifecycle-07")
        session.flush()
        version.status = UniverseStatus.RETIRED
        session.flush()

        with pytest.raises(InvalidStatusTransitionError):
            repo.activate_version("v-lifecycle-07")


class TestUniverseVersionRepositoryQueries:
    def test_get_active_version_returns_version_in_effective_window(self, repo, session) -> None:
        version = _make_version("v-query-01")
        repo.insert_version(version)
        member = _make_member("v-query-01", "AAPL")
        repo.insert_members([member])
        session.flush()
        repo.activate_version("v-query-01")
        session.flush()

        as_of = datetime(2025, 1, 15, 15, 0, tzinfo=UTC)
        found = repo.get_active_version(as_of)
        assert found is not None
        assert found.universe_version_id == "v-query-01"

    def test_get_active_version_returns_none_before_effective_from(self, repo, session) -> None:
        as_of = datetime(2024, 12, 1, tzinfo=UTC)
        found = repo.get_active_version(as_of)
        assert found is None

    def test_get_symbols_returns_sorted_members(self, repo, session) -> None:
        version = _make_version("v-query-02")
        repo.insert_version(version)
        members = [
            _make_member("v-query-02", "MSFT"),
            _make_member("v-query-02", "AAPL"),
        ]
        members[0].rank = 1
        members[1].rank = 0
        repo.insert_members(members)
        session.flush()

        symbols = repo.get_symbols("v-query-02")
        assert symbols == ["AAPL", "MSFT"]

    def test_get_members_orders_by_rank_then_symbol(self, repo, session) -> None:
        version = _make_version("v-query-03")
        repo.insert_version(version)
        m1 = _make_member("v-query-03", "NVDA")
        m1.rank = 2
        m2 = _make_member("v-query-03", "AAPL")
        m2.rank = 0
        m3 = _make_member("v-query-03", "MSFT")
        m3.rank = 1
        repo.insert_members([m1, m2, m3])
        session.flush()

        members = repo.get_members("v-query-03")
        assert [m.symbol for m in members] == ["AAPL", "MSFT", "NVDA"]
