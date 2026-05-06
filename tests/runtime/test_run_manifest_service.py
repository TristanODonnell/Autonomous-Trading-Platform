from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
    RunType,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.db import get_engine
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.runtime.services.run_manifest_service import (
    RunManifestService,
)
from autonomous_trading_platform.storage.sor.models.base import Base

DEFAULT_RUN_ID = UUID("00000000-0000-0000-0000-000000000402")


@pytest.fixture
def db_session() -> Session:
    engine = get_engine()
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    session.execute(text("TRUNCATE TABLE run_manifests CASCADE"))
    session.commit()

    try:
        yield session
    finally:
        session.close()


def make_run_manifest(
    *,
    run_id: UUID = DEFAULT_RUN_ID,
    run_type: RunType = RunType.BACKTEST,
    environment: str = "paper",
    broker: Literal["alpaca"] = "alpaca",
    broker_account_id: str = "paper-account-1",
    strategy_id: str = "strategy-alpha",
    strategy_version: str = "1.0.0",
    strategy_config: dict[str, Any] | None = None,
    capital_bucket: Decimal = Decimal("100000.00"),
    interval: BarInterval = BarInterval.FIVE_MIN,
    start_date: date = date(2025, 1, 1),
    end_date: date | None = date(2025, 1, 31),
    dataset_version: str = "bars-v1",
    price_basis: PriceBasis = PriceBasis.RAW,
    universe_version: str = "universe-v1",
    cost_model: dict[str, Any] | None = None,
    fill_model: dict[str, Any] | None = None,
    random_seed: int | None = 42,
    git_commit: str = "abc123def456",
    docker_image: str | None = "retail-autonomous-trading-platform:latest",
    python_version: str | None = "3.11.9",
    dependency_lock_hash: str | None = "lock-hash-001",
    notes: str | None = "integration test manifest",
    created_at: datetime = datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
    bar_timestamp: datetime | None = None,
    status: str | None = None,
    current_step: str | None = None,
    last_successful_step: str | None = None,
    error_message: str | None = None,
    governance_state: GovernanceState = GovernanceState.APPROVED_RESEARCH,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        run_type=run_type,
        created_at=created_at,
        environment=environment,
        broker=broker,
        broker_account_id=broker_account_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_config=strategy_config if strategy_config is not None else {"rebalance": "5m"},
        capital_bucket=capital_bucket,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        dataset_version=dataset_version,
        price_basis=price_basis,
        universe_version=universe_version,
        cost_model=cost_model,
        fill_model=fill_model,
        random_seed=random_seed,
        git_commit=git_commit,
        docker_image=docker_image,
        python_version=python_version,
        dependency_lock_hash=dependency_lock_hash,
        notes=notes,
        bar_timestamp=bar_timestamp,
        status=status,
        current_step=current_step,
        last_successful_step=last_successful_step,
        error_message=error_message,
        governance_state=governance_state,
    )


class FakeRunManifestRepository:
    def __init__(self) -> None:
        self.add_calls: list[RunManifest] = []
        self.upsert_calls: list[RunManifest] = []
        self.by_run_id: dict[UUID, RunManifest] = {}

    def add(self, manifest: RunManifest) -> RunManifest:
        self.add_calls.append(manifest)
        self.by_run_id[manifest.run_id] = manifest
        return manifest

    def upsert(self, manifest: RunManifest) -> RunManifest:
        self.upsert_calls.append(manifest)
        self.by_run_id[manifest.run_id] = manifest
        return manifest

    def get_by_run_id(self, run_id: UUID) -> RunManifest | None:
        return self.by_run_id.get(run_id)

    def to_contract(self, row: RunManifest) -> RunManifest:
        return row


class FakeSession:
    def __init__(self) -> None:
        self.begin_called = 0
        self.commit_called = 0
        self.rollback_called = 0

    def begin(self) -> None:
        self.begin_called += 1

    def commit(self) -> None:
        self.commit_called += 1

    def rollback(self) -> None:
        self.rollback_called += 1


class FakeSorUnitOfWork:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.run_manifests = FakeRunManifestRepository()

    def __enter__(self) -> FakeSorUnitOfWork:
        self.session.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()


@pytest.fixture
def manifest() -> RunManifest:
    return make_run_manifest()


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


class TestRunManifestServiceCurrentBehavior:
    def test_save_adds_manifest_and_commits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
        manifest: RunManifest,
    ) -> None:
        captured_uows: list[FakeSorUnitOfWork] = []

        def fake_uow_factory(fake_session: FakeSession) -> FakeSorUnitOfWork:
            uow = FakeSorUnitOfWork(fake_session)
            captured_uows.append(uow)
            return uow

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.run_manifest_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = RunManifestService(session)

        result = service.save(manifest)

        assert result == manifest
        assert len(captured_uows) == 1
        assert captured_uows[0].run_manifests.upsert_calls == [manifest]
        assert session.begin_called == 1
        assert session.commit_called == 1
        assert session.rollback_called == 0

    def test_save_rolls_back_when_repository_add_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: FakeSession,
        manifest: RunManifest,
    ) -> None:
        class ExplodingRunManifestRepository(FakeRunManifestRepository):
            def upsert(self, manifest: RunManifest) -> RunManifest:
                raise RuntimeError("database write failed")

        class ExplodingSorUnitOfWork(FakeSorUnitOfWork):
            def __init__(self, session: FakeSession) -> None:
                self.session = session
                self.run_manifests = ExplodingRunManifestRepository()

        def fake_uow_factory(fake_session: FakeSession) -> ExplodingSorUnitOfWork:
            return ExplodingSorUnitOfWork(fake_session)

        monkeypatch.setattr(
            "autonomous_trading_platform.runtime.services.run_manifest_service.SorUnitOfWork",
            fake_uow_factory,
        )

        service = RunManifestService(session)

        with pytest.raises(RuntimeError, match="database write failed"):
            service.save(manifest)

        assert session.begin_called == 1
        assert session.commit_called == 0
        assert session.rollback_called == 1


class TestRunManifestServiceTodoBehavior:
    def test_save_same_run_twice_does_not_create_duplicates_and_returns_existing_row(
        self,
        db_session: Session,
    ) -> None:
        first = make_run_manifest()
        second = make_run_manifest()

        service = RunManifestService(session=db_session)
        saved_first = service.save(first)
        saved_second = service.save(second)

        assert saved_first is not None
        assert saved_second is not None
        assert saved_first.model_dump() == saved_second.model_dump()

    def test_same_run_id_returns_existing_manifest(
        self,
        db_session: Session,
    ) -> None:
        run_id = UUID("00000000-0000-0000-0000-000000000450")

        manifest_one = make_run_manifest(
            run_id=run_id,
            created_at=datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
            environment="paper",
            governance_state=GovernanceState.APPROVED_RESEARCH,
        )
        manifest_two = make_run_manifest(
            run_id=run_id,
            created_at=datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
            environment="paper",
            governance_state=GovernanceState.APPROVED_RESEARCH,
        )

        service = RunManifestService(session=db_session)
        saved_one = service.save(manifest_one)
        saved_two = service.save(manifest_two)

        assert saved_one is not None
        assert saved_two is not None
        assert saved_one.model_dump() == saved_two.model_dump()

    def test_save_new_run_manifest_persists_all_expected_fields(
        self,
        db_session: Session,
    ) -> None:
        manifest = make_run_manifest()

        service = RunManifestService(session=db_session)
        saved = service.save(manifest)
        assert saved.price_basis == manifest.price_basis
        assert saved.run_id == manifest.run_id
        assert saved.run_type == manifest.run_type
        assert saved.environment == manifest.environment
        assert saved.broker == manifest.broker
        assert saved.broker_account_id == manifest.broker_account_id
        assert saved.strategy_id == manifest.strategy_id
        assert saved.strategy_version == manifest.strategy_version
        assert saved.strategy_config == manifest.strategy_config
        assert saved.capital_bucket == manifest.capital_bucket
        assert saved.interval == manifest.interval
        assert saved.start_date == manifest.start_date
        assert saved.end_date == manifest.end_date
        assert saved.dataset_version == manifest.dataset_version
        assert saved.universe_version == manifest.universe_version
        assert saved.cost_model == manifest.cost_model
        assert saved.fill_model == manifest.fill_model
        assert saved.random_seed == manifest.random_seed
        assert saved.git_commit == manifest.git_commit
        assert saved.docker_image == manifest.docker_image
        assert saved.python_version == manifest.python_version
        assert saved.dependency_lock_hash == manifest.dependency_lock_hash
        assert saved.notes == manifest.notes
        assert saved.created_at == manifest.created_at
        assert saved.bar_timestamp == manifest.bar_timestamp
        assert saved.status == manifest.status
        assert saved.current_step == manifest.current_step
        assert saved.last_successful_step == manifest.last_successful_step
        assert saved.error_message == manifest.error_message
        assert saved.governance_state == manifest.governance_state

    def test_multiple_distinct_manifests_can_be_saved(
        self,
        db_session: Session,
    ) -> None:
        manifests = [
            make_run_manifest(
                run_id=UUID(f"00000000-0000-0000-0000-00000000052{i}"),
                environment=f"paper-{i}",
                governance_state=GovernanceState.APPROVED_RESEARCH,
            )
            for i in range(3)
        ]

        service = RunManifestService(session=db_session)

        saved = [service.save(manifest) for manifest in manifests]

        assert len(saved) == 3
        assert {item.run_id for item in saved} == {m.run_id for m in manifests}
