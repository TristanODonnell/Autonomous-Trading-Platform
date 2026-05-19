from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.universe.services.universe_resolution_service import (
    NoActiveUniverseError,
    UniverseResolutionService,
)

_T0 = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
_T1 = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)


def _make_version(
    *,
    version_id: str = "ver-001",
    status: str = UniverseStatus.ACTIVE,
    source: str = UniverseSource.CUSTOM,
    effective_from: datetime = _T0,
    effective_to: datetime | None = None,
) -> UniverseVersion:
    return UniverseVersion(
        universe_version_id=version_id,
        name="test_universe",
        source=source,
        created_at=_T0,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        rebalance_reason=None,
        config_hash="hash123",
    )


def _make_members(version_id: str, symbols: list[str]) -> list[UniverseMember]:
    return [
        UniverseMember(
            universe_version_id=version_id,
            symbol=sym,
            rank=i + 1,
            score=None,
            included_reason="test",
            excluded_reason=None,
        )
        for i, sym in enumerate(symbols)
    ]


class StubUniverseVersionRepository:
    def __init__(
        self,
        active_version: UniverseVersion | None = None,
        members: list[UniverseMember] | None = None,
    ) -> None:
        self._active_version = active_version
        self._members = members or []

    def get_active_version(self, as_of: datetime) -> UniverseVersion | None:
        return self._active_version

    def get_included_symbols(self, version_id: str) -> list[str]:
        return [m.symbol for m in self._members if m.excluded_reason is None]

    def get_included_members(self, version_id: str) -> list[UniverseMember]:
        return [m for m in self._members if m.excluded_reason is None]


@pytest.fixture
def active_version() -> UniverseVersion:
    return _make_version(version_id="ver-active")


@pytest.fixture
def members(active_version: UniverseVersion) -> list[UniverseMember]:
    return _make_members(active_version.universe_version_id, ["AAPL", "MSFT", "GOOG"])


@pytest.fixture
def repo_with_active(
    active_version: UniverseVersion, members: list[UniverseMember]
) -> StubUniverseVersionRepository:
    return StubUniverseVersionRepository(active_version=active_version, members=members)


@pytest.fixture
def repo_empty() -> StubUniverseVersionRepository:
    return StubUniverseVersionRepository(active_version=None, members=[])


@pytest.fixture
def service_with_active(
    repo_with_active: StubUniverseVersionRepository,
) -> UniverseResolutionService:
    return UniverseResolutionService(repo_with_active)


@pytest.fixture
def service_empty(repo_empty: StubUniverseVersionRepository) -> UniverseResolutionService:
    return UniverseResolutionService(repo_empty)


class TestResolveActive:
    def test_returns_version_when_active(
        self,
        service_with_active: UniverseResolutionService,
        active_version: UniverseVersion,
    ) -> None:
        result = service_with_active.resolve_active(_T0)
        assert result.universe_version_id == active_version.universe_version_id

    def test_raises_when_no_active(self, service_empty: UniverseResolutionService) -> None:
        with pytest.raises(NoActiveUniverseError):
            service_empty.resolve_active(_T0)

    def test_error_message_contains_timestamp(
        self, service_empty: UniverseResolutionService
    ) -> None:
        with pytest.raises(NoActiveUniverseError, match="2025-01-15"):
            service_empty.resolve_active(_T0)


class TestResolveActiveOrNone:
    def test_returns_version_when_active(
        self,
        service_with_active: UniverseResolutionService,
        active_version: UniverseVersion,
    ) -> None:
        result = service_with_active.resolve_active_or_none(_T0)
        assert result is not None
        assert result.universe_version_id == active_version.universe_version_id

    def test_returns_none_when_no_active(self, service_empty: UniverseResolutionService) -> None:
        result = service_empty.resolve_active_or_none(_T0)
        assert result is None


class TestResolveActiveVersionId:
    def test_returns_id_when_active(
        self,
        service_with_active: UniverseResolutionService,
        active_version: UniverseVersion,
    ) -> None:
        result = service_with_active.resolve_active_version_id(_T0)
        assert result == active_version.universe_version_id

    def test_returns_none_when_no_active(self, service_empty: UniverseResolutionService) -> None:
        result = service_empty.resolve_active_version_id(_T0)
        assert result is None


class TestResolveActiveMembers:
    def test_returns_included_symbols(self, service_with_active: UniverseResolutionService) -> None:
        result = service_with_active.resolve_active_members(_T0)
        assert set(result) == {"AAPL", "MSFT", "GOOG"}

    def test_returns_empty_when_no_active(self, service_empty: UniverseResolutionService) -> None:
        result = service_empty.resolve_active_members(_T0)
        assert result == []

    def test_excluded_members_not_returned(self) -> None:
        version = _make_version(version_id="ver-excl")
        members = [
            UniverseMember(
                universe_version_id="ver-excl",
                symbol="AAPL",
                rank=1,
                included_reason="test",
                excluded_reason=None,
            ),
            UniverseMember(
                universe_version_id="ver-excl",
                symbol="DELISTED",
                rank=None,
                included_reason=None,
                excluded_reason="delisted",
            ),
        ]
        repo = StubUniverseVersionRepository(active_version=version, members=members)
        service = UniverseResolutionService(repo)
        result = service.resolve_active_members(_T0)
        assert result == ["AAPL"]
        assert "DELISTED" not in result


class TestResolveForDate:
    def test_resolves_at_start_of_day(self, service_with_active: UniverseResolutionService) -> None:
        result = service_with_active.resolve_for_date(_T0.date())
        assert result is not None

    def test_raises_when_no_active(self, service_empty: UniverseResolutionService) -> None:
        with pytest.raises(NoActiveUniverseError):
            service_empty.resolve_for_date(_T0.date())


class TestResolveForSimulationWindow:
    def test_uses_start_of_window(self, service_with_active: UniverseResolutionService) -> None:
        result = service_with_active.resolve_for_simulation_window(start=_T0.date(), end=_T1.date())
        assert result is not None

    def test_raises_when_no_universe_at_start(
        self, service_empty: UniverseResolutionService
    ) -> None:
        with pytest.raises(NoActiveUniverseError):
            service_empty.resolve_for_simulation_window(start=_T0.date(), end=_T1.date())


class TestResolveMemberCount:
    def test_returns_count(self, service_with_active: UniverseResolutionService) -> None:
        result = service_with_active.resolve_member_count(_T0)
        assert result == 3

    def test_returns_zero_when_no_active(self, service_empty: UniverseResolutionService) -> None:
        result = service_empty.resolve_member_count(_T0)
        assert result == 0


class TestAssertActiveUniverseExists:
    def test_passes_when_active(self, service_with_active: UniverseResolutionService) -> None:
        service_with_active.assert_active_universe_exists(_T0)

    def test_raises_when_no_active(self, service_empty: UniverseResolutionService) -> None:
        with pytest.raises(NoActiveUniverseError):
            service_empty.assert_active_universe_exists(_T0)

    def test_raises_for_non_active_status(self) -> None:
        version = _make_version(version_id="ver-cand", status=UniverseStatus.CANDIDATE)
        repo = StubUniverseVersionRepository(active_version=version, members=[])
        service = UniverseResolutionService(repo)
        with pytest.raises(NoActiveUniverseError, match="candidate"):
            service.assert_active_universe_exists(_T0)

    def test_raises_for_retired_status(self) -> None:
        version = _make_version(version_id="ver-ret", status=UniverseStatus.RETIRED)
        repo = StubUniverseVersionRepository(active_version=version, members=[])
        service = UniverseResolutionService(repo)
        with pytest.raises(NoActiveUniverseError, match="retired"):
            service.assert_active_universe_exists(_T0)
