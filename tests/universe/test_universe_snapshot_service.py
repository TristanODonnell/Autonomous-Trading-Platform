from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import cast

import pytest

from autonomous_trading_platform.contracts.runtime.universe_snapshot import (
    UniverseSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)
from autonomous_trading_platform.universe.services.universe_snapshot_service import (
    UniverseSnapshotService,
)


class StubUniverseSnapshotRepository:
    def __init__(self) -> None:
        self.upsert_calls: list[UniverseSnapshot] = []
        self.close_open_snapshot_calls: list[tuple[datetime]] = []

    def upsert(self, snapshot):
        self.upsert_calls.append(snapshot)
        return snapshot

    def close_open_snapshot(self, effective_start):
        self.close_open_snapshot_calls.append(effective_start)


@pytest.fixture
def repository() -> StubUniverseSnapshotRepository:
    return StubUniverseSnapshotRepository()


@pytest.fixture
def service(
    repository: StubUniverseSnapshotRepository,
) -> UniverseSnapshotService:
    return UniverseSnapshotService(repository=cast(UniverseSnapshotRepository, repository))


class TestUniverseSnapshotService:
    def test_compute_version_depends_only_on_sorted_symbols_list(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        symbols_a = ["MSFT", "AAPL", "NVDA", "GOOG"]
        symbols_b = ["AAPL", "GOOG", "MSFT", "NVDA"]

        version_a = service._compute_version(symbols_a)
        version_b = service._compute_version(symbols_b)

        assert version_a == version_b

    def test_compute_version_changes_when_symbol_membership_changes(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        base_symbols = ["AAPL", "MSFT", "NVDA"]
        changed_symbols = ["AAPL", "MSFT", "AMZN"]

        base_version = service._compute_version(base_symbols)
        changed_version = service._compute_version(changed_symbols)

        assert base_version != changed_version

    def test_small_permutations_of_symbols_produce_same_version(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        original = ["TSLA", "AAPL", "MSFT", "NVDA", "AMD"]
        permutation_1 = ["AMD", "TSLA", "AAPL", "MSFT", "NVDA"]
        permutation_2 = ["NVDA", "AMD", "MSFT", "TSLA", "AAPL"]
        permutation_3 = ["MSFT", "AAPL", "AMD", "NVDA", "TSLA"]

        expected = service._compute_version(original)

        assert service._compute_version(permutation_1) == expected
        assert service._compute_version(permutation_2) == expected
        assert service._compute_version(permutation_3) == expected

    def test_build_snapshot_sorts_symbols_before_storing(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        snapshot = service.build_snapshot(
            snapshot_date=date(2025, 1, 15),
            effective_start=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["MSFT", "AAPL", "NVDA"],
            criteria={"min_price": 1},
            source="test",
        )

        assert snapshot.symbols == ["AAPL", "MSFT", "NVDA"]

    def test_build_snapshot_version_matches_sorted_symbols(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        symbols = ["MSFT", "AAPL", "NVDA"]

        snapshot = service.build_snapshot(
            snapshot_date=date(2025, 1, 15),
            effective_start=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=symbols,
            criteria={"min_price": 1},
            source="test",
        )

        expected_version = service._compute_version(sorted(symbols))
        assert snapshot.version == expected_version

    def test_ensure_utc_converts_aware_datetime_to_utc(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        pacific = timezone(timedelta(hours=-8))
        local_dt = datetime(2025, 1, 15, 6, 30, tzinfo=pacific)

        result = service._ensure_utc(local_dt)

        assert result.tzinfo == UTC
        assert result == datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

    def test_ensure_utc_assigns_utc_to_naive_datetime(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        naive_dt = datetime(2025, 1, 15, 14, 30)

        result = service._ensure_utc(naive_dt)

        assert result.tzinfo == UTC
        assert result == datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

    def test_build_snapshot_enforces_utc_on_effective_start(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        eastern = timezone(timedelta(hours=-5))
        local_dt = datetime(2025, 1, 15, 9, 30, tzinfo=eastern)

        snapshot = service.build_snapshot(
            snapshot_date=date(2025, 1, 15),
            effective_start=local_dt,
            symbols=["AAPL", "MSFT"],
            criteria={"min_price": 1},
            source="test",
        )

        assert snapshot.effective_start.tzinfo == UTC
        assert snapshot.effective_start == datetime(2025, 1, 15, 14, 30, tzinfo=UTC)

    def test_save_snapshot_calls_repository_upsert(
        self,
        service: UniverseSnapshotService,
        repository: StubUniverseSnapshotRepository,
    ) -> None:
        snapshot = service.build_snapshot(
            snapshot_date=date(2025, 1, 15),
            effective_start=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["AAPL", "MSFT"],
            criteria={"min_price": 1},
            source="test",
        )

        saved = service.save_snapshot(snapshot)

        assert saved is snapshot
        assert repository.upsert_calls == [snapshot]

    def test_build_and_save_snapshot_closes_open_snapshot_then_upserts(
        self,
        service: UniverseSnapshotService,
        repository: StubUniverseSnapshotRepository,
    ) -> None:
        saved = service.build_and_save_snapshot(
            snapshot_date=date(2025, 1, 15),
            effective_start=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
            symbols=["MSFT", "AAPL"],
            criteria={"min_price": 1},
            source="test",
        )

        assert repository.close_open_snapshot_calls == [saved.effective_start]
        assert repository.upsert_calls == [saved]

    def test_compute_version_treats_duplicate_or_case_variant_symbols_as_equivalent(
        self,
        service: UniverseSnapshotService,
    ) -> None:
        normalized = ["AAPL", "MSFT"]
        duplicate_or_case_variant = ["msft", "AAPL", "MSFT"]

        assert service._compute_version(normalized) == service._compute_version(
            duplicate_or_case_variant
        )
