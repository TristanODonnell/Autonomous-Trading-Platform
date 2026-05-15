from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from autonomous_trading_platform.contracts.common.enums import UniverseStatus
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.universe_version_service import (
    UniverseVersionService,
)


class StubUniverseVersionRepository:
    def __init__(self) -> None:
        self.inserted_versions: list[UniverseVersion] = []
        self.inserted_members: list[list[UniverseMember]] = []
        self.retire_calls: list[datetime] = []
        self.activate_calls: list[str] = []

    def insert_version(self, row: UniverseVersion) -> None:
        self.inserted_versions.append(row)

    def insert_members(self, rows: list[UniverseMember]) -> None:
        self.inserted_members.append(rows)

    def retire_active_version(self, retired_at: datetime) -> None:
        self.retire_calls.append(retired_at)

    def activate_version(self, version_id: str) -> UniverseVersion:
        self.activate_calls.append(version_id)
        return self.inserted_versions[-1]

    def get_by_version_id(self, version_id: str) -> UniverseVersion | None:
        for v in self.inserted_versions:
            if v.universe_version_id == version_id:
                return v
        return None

    def get_members(self, version_id: str) -> list[UniverseMember]:
        for batch in self.inserted_members:
            if batch and batch[0].universe_version_id == version_id:
                return batch
        return []


@pytest.fixture
def repository() -> StubUniverseVersionRepository:
    return StubUniverseVersionRepository()


@pytest.fixture
def service(repository: StubUniverseVersionRepository) -> UniverseVersionService:
    return UniverseVersionService(repository=cast(UniverseVersionRepository, repository))


class TestUniverseVersionService:
    def test_build_version_produces_candidate_status(self, service: UniverseVersionService) -> None:
        version, _ = service.build_version(
            name="test",
            effective_from=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["AAPL", "MSFT"],
        )
        assert version.status == UniverseStatus.CANDIDATE

    def test_build_version_normalizes_and_sorts_symbols(
        self, service: UniverseVersionService
    ) -> None:
        _, members = service.build_version(
            name="test",
            effective_from=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["MSFT", "aapl", "NVDA"],
        )
        symbols = [m.symbol for m in members]
        assert symbols == ["AAPL", "MSFT", "NVDA"]

    def test_build_version_deduplicates_symbols(self, service: UniverseVersionService) -> None:
        _, members = service.build_version(
            name="test",
            effective_from=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["AAPL", "MSFT", "AAPL"],
        )
        assert len(members) == 2

    def test_build_version_assigns_sequential_rank(self, service: UniverseVersionService) -> None:
        _, members = service.build_version(
            name="test",
            effective_from=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["MSFT", "AAPL"],
        )
        ranks = [m.rank for m in members]
        assert ranks == [0, 1]

    def test_config_hash_is_order_independent(self, service: UniverseVersionService) -> None:
        v1, _ = service.build_version(
            name="a",
            effective_from=datetime(2025, 1, 15, tzinfo=UTC),
            symbols=["AAPL", "MSFT", "NVDA"],
        )
        v2, _ = service.build_version(
            name="b",
            effective_from=datetime(2025, 1, 15, tzinfo=UTC),
            symbols=["NVDA", "AAPL", "MSFT"],
        )
        assert v1.config_hash == v2.config_hash

    def test_config_hash_differs_when_membership_changes(
        self, service: UniverseVersionService
    ) -> None:
        v1, _ = service.build_version(
            name="a",
            effective_from=datetime(2025, 1, 15, tzinfo=UTC),
            symbols=["AAPL", "MSFT"],
        )
        v2, _ = service.build_version(
            name="b",
            effective_from=datetime(2025, 1, 15, tzinfo=UTC),
            symbols=["AAPL", "AMZN"],
        )
        assert v1.config_hash != v2.config_hash

    def test_build_version_enforces_utc_on_effective_from(
        self, service: UniverseVersionService
    ) -> None:
        eastern = timezone(timedelta(hours=-5))
        local_dt = datetime(2025, 1, 15, 9, 30, tzinfo=eastern)

        version, _ = service.build_version(
            name="test",
            effective_from=local_dt,
            symbols=["AAPL"],
        )
        assert version.effective_from.tzinfo == UTC
        assert version.effective_from == datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

    def test_build_version_assigns_naive_datetime_utc(
        self, service: UniverseVersionService
    ) -> None:
        naive_dt = datetime(2025, 1, 15, 14, 30)
        version, _ = service.build_version(
            name="test",
            effective_from=naive_dt,
            symbols=["AAPL"],
        )
        assert version.effective_from.tzinfo == UTC

    def test_save_version_calls_insert_on_repository(
        self,
        service: UniverseVersionService,
        repository: StubUniverseVersionRepository,
    ) -> None:
        version, members = service.build_version(
            name="test",
            effective_from=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["AAPL", "MSFT"],
        )
        service.save_version(version, members)

        assert len(repository.inserted_versions) == 1
        assert len(repository.inserted_members) == 1
        assert repository.inserted_versions[0] is version

    def test_build_and_activate_retires_then_inserts_then_activates(
        self,
        service: UniverseVersionService,
        repository: StubUniverseVersionRepository,
    ) -> None:
        effective_from = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
        version, members = service.build_and_activate_version(
            name="test",
            effective_from=effective_from,
            symbols=["AAPL", "MSFT"],
        )

        assert repository.retire_calls == [effective_from]
        assert len(repository.inserted_versions) == 1
        assert len(repository.inserted_members) == 1
        assert repository.activate_calls == [version.universe_version_id]

    def test_version_id_is_unique_per_build(self, service: UniverseVersionService) -> None:
        v1, _ = service.build_version(
            name="a",
            effective_from=datetime(2025, 1, 15, tzinfo=UTC),
            symbols=["AAPL"],
        )
        v2, _ = service.build_version(
            name="b",
            effective_from=datetime(2025, 1, 15, tzinfo=UTC),
            symbols=["AAPL"],
        )
        assert v1.universe_version_id != v2.universe_version_id
