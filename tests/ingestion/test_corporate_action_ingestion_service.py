from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from autonomous_trading_platform.contracts.common.enums import CorporateActionType, PriceBasis
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service import (
    CorporateActionIngestionService,
)
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from src.db import get_engine
from tests.utilities.factories import make_minute_bar


@pytest.fixture
def db_session():
    engine = get_engine()
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    # CLEAN TABLES
    session.execute(text("TRUNCATE TABLE market_bars CASCADE"))
    session.execute(text("TRUNCATE TABLE corporate_actions CASCADE"))
    session.commit()

    try:
        yield session
    finally:
        session.close()


@dataclass
class FakeValidationResult:
    ok: bool
    violations: list[str]


@dataclass(frozen=True)
class FakeCorporateAction:
    action_id: str
    symbol: str
    effective_date: date
    action_type: str


@dataclass(frozen=True)
class FakeUpsertResult:
    entity: object
    created: bool


class FakeAuditLogger:
    def __init__(self) -> None:
        self.parse_failed_calls: list[dict[str, Any]] = []
        self.validation_failed_calls: list[dict[str, Any]] = []
        self.adjustment_applied_calls: list[dict[str, Any]] = []

    def record_corporate_action_parse_failed(
        self,
        *,
        run_id: str,
        symbol: str,
        cycle_timestamp: datetime,
    ) -> None:
        self.parse_failed_calls.append(
            {
                "run_id": run_id,
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp,
            }
        )

    def record_corporate_action_validation_failed(
        self,
        *,
        run_id: str,
        symbol: str,
        cycle_timestamp: datetime,
    ) -> None:
        self.validation_failed_calls.append(
            {
                "run_id": run_id,
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp,
            }
        )

    def record_corporate_action_adjustment_applied(
        self,
        *,
        run_id: str,
        symbol: str,
        cycle_timestamp: datetime,
    ) -> None:
        self.adjustment_applied_calls.append(
            {
                "run_id": run_id,
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp,
            }
        )


class FakeNormalizationService:
    def __init__(
        self,
        *,
        parsed_actions: list[FakeCorporateAction | ValueError],
    ) -> None:
        self.parsed_actions = list(parsed_actions)
        self.calls: list[dict[str, object]] = []

    def parse_alpaca_corporate_action(
        self,
        raw_action: dict[str, object],
    ) -> FakeCorporateAction:
        self.calls.append(raw_action)

        if not self.parsed_actions:
            raise AssertionError("No more seeded normalization results.")

        result = self.parsed_actions.pop(0)
        if isinstance(result, ValueError):
            raise result
        return result


class FakeValidationService:
    def __init__(
        self,
        *,
        validation_results: list[FakeValidationResult] | None = None,
    ) -> None:
        self.validation_results = (
            list(validation_results)
            if validation_results is not None
            else [FakeValidationResult(ok=True, violations=[])]
        )
        self.calls: list[FakeCorporateAction] = []

    def validate(self, action: FakeCorporateAction) -> FakeValidationResult:
        self.calls.append(action)
        if len(self.validation_results) == 1:
            return self.validation_results[0]
        return self.validation_results.pop(0)


class FakeAdjustmentService:
    def __init__(
        self,
        *,
        supported_results: list[bool] | None = None,
        adjusted_bars_results: list[list[object]] | None = None,
    ) -> None:
        self.supported_results = (
            list(supported_results) if supported_results is not None else [False]
        )
        self.adjusted_bars_results = (
            list(adjusted_bars_results) if adjusted_bars_results is not None else [[]]
        )

        self.supports_calls: list[FakeCorporateAction] = []
        self.apply_calls: list[tuple[FakeCorporateAction, list[object]]] = []

    def supports_adjustment(self, action: FakeCorporateAction) -> bool:
        self.supports_calls.append(action)
        if len(self.supported_results) == 1:
            return self.supported_results[0]
        return self.supported_results.pop(0)

    def apply_action_to_bars(
        self,
        action: FakeCorporateAction,
        raw_bars: list[object],
    ) -> list[object]:
        self.apply_calls.append((action, list(raw_bars)))
        if len(self.adjusted_bars_results) == 1:
            return self.adjusted_bars_results[0]
        return self.adjusted_bars_results.pop(0)


class FakeCorporateActionsRepository:
    def __init__(self, parent: RecordingUnitOfWork) -> None:
        self.parent = parent

    def upsert(self, action: object) -> FakeUpsertResult:
        if self.parent.raise_on_action_upsert:
            raise self.parent.raise_on_action_upsert

        action_id = getattr(action, "action_id", None)
        if action_id in self.parent.persisted_actions_by_id:
            existing = self.parent.persisted_actions_by_id[action_id]
            return FakeUpsertResult(entity=existing, created=False)

        self.parent.persisted_actions.append(action)
        if action_id is not None:
            self.parent.persisted_actions_by_id[action_id] = action

        return FakeUpsertResult(entity=action, created=True)


class FakeMarketBarsRepository:
    def __init__(
        self,
        parent: RecordingUnitOfWork,
        *,
        raw_bars_by_key: dict[tuple[str, date], list[object]] | None = None,
    ) -> None:
        self.parent = parent
        self.raw_bars_by_key = raw_bars_by_key or {}
        self.get_raw_bars_calls: list[dict[str, object]] = []

    def get_raw_bars_before_date(
        self,
        *,
        symbol: str,
        effective_date: date,
    ) -> list[object]:
        self.get_raw_bars_calls.append(
            {
                "symbol": symbol,
                "effective_date": effective_date,
            }
        )
        return list(self.raw_bars_by_key.get((symbol, effective_date), []))

    def upsert(self, bar: object) -> None:
        if self.parent.raise_on_bar_upsert:
            raise self.parent.raise_on_bar_upsert

        bar_id = getattr(bar, "bar_id", None)
        if bar_id in self.parent.persisted_bars_by_id:
            return

        self.parent.persisted_bars.append(bar)
        if bar_id is not None:
            self.parent.persisted_bars_by_id[bar_id] = bar


class RecordingUnitOfWork:
    instances: list[RecordingUnitOfWork] = []
    raw_bars_by_key: dict[tuple[str, date], list[object]] = {}

    def __init__(
        self,
        session: object,
        *,
        raise_on_action_upsert: Exception | None = None,
        raise_on_bar_upsert: Exception | None = None,
    ) -> None:
        self.session = session
        self.raise_on_action_upsert = raise_on_action_upsert
        self.raise_on_bar_upsert = raise_on_bar_upsert

        self.persisted_actions: list[object] = []
        self.persisted_actions_by_id: dict[object, object] = {}
        self.persisted_bars: list[object] = []
        self.persisted_bars_by_id: dict[object, object] = {}

        self.entered = False
        self.exited = False
        self.committed = False
        self.rolled_back = False

        self.corporate_actions = FakeCorporateActionsRepository(self)
        self.market_bars = FakeMarketBarsRepository(
            self,
            raw_bars_by_key=type(self).raw_bars_by_key,
        )

        RecordingUnitOfWork.instances.append(self)

    def __enter__(self) -> RecordingUnitOfWork:
        self.entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        self.exited = True
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True
        return False


@pytest.fixture(autouse=True)
def clear_recording_uow_instances() -> None:
    RecordingUnitOfWork.instances.clear()
    RecordingUnitOfWork.raw_bars_by_key = {}


@pytest.fixture
def audit_logger() -> FakeAuditLogger:
    return FakeAuditLogger()


@pytest.fixture
def session() -> object:
    return object()


@pytest.fixture
def cycle_timestamp() -> datetime:
    return datetime(2025, 1, 15, 14, 35, tzinfo=UTC)


@pytest.fixture
def service(
    session: object,
    audit_logger: FakeAuditLogger,
    cycle_timestamp: datetime,
) -> CorporateActionIngestionService:
    return CorporateActionIngestionService(
        session=session,
        run_id="run-test-001",
        audit_logger=audit_logger,
        cycle_timestamp=cycle_timestamp,
    )


def test_ingest_corporate_actions_normalizes_validates_stores_and_adjusts_affected_bars(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
    audit_logger: FakeAuditLogger,
    cycle_timestamp: datetime,
) -> None:
    """
    Integration-ish orchestration test:
    - multiple provider payloads flow through ingest_corporate_actions
    - valid actions are normalized, validated, and persisted
    - adjustment-supported actions fetch raw bars and persist adjusted bars
    - parse failures and validation failures are logged and skipped
    """
    raw_payload = {
        "corporate_actions": [
            {"id": "ca-001", "symbol": "AAPL"},
            {"id": "ca-parse-fail", "symbol": "MSFT"},
            {"id": "ca-invalid", "symbol": "TSLA"},
            {"id": "ca-002", "symbol": "NVDA"},
        ]
    }

    action_1 = FakeCorporateAction(
        action_id="ca-001",
        symbol="AAPL",
        effective_date=date(2025, 1, 10),
        action_type="forward_split",
    )
    action_invalid = FakeCorporateAction(
        action_id="ca-invalid",
        symbol="TSLA",
        effective_date=date(2025, 1, 12),
        action_type="cash_dividend",
    )
    action_2 = FakeCorporateAction(
        action_id="ca-002",
        symbol="NVDA",
        effective_date=date(2025, 1, 14),
        action_type="cash_dividend",
    )

    raw_bar_aapl_1 = make_minute_bar(
        timestamp=datetime(2025, 1, 9, 15, 30, tzinfo=UTC),
        symbol="AAPL",
        close_price="100.00",
        volume=100,
    )
    raw_bar_aapl_2 = make_minute_bar(
        timestamp=datetime(2025, 1, 9, 15, 31, tzinfo=UTC),
        symbol="AAPL",
        close_price="101.00",
        volume=120,
    )
    adjusted_bar_aapl_1 = make_minute_bar(
        timestamp=datetime(2025, 1, 9, 15, 30, tzinfo=UTC),
        symbol="AAPL",
        close_price="25.00",
        volume=400,
    )
    adjusted_bar_aapl_2 = make_minute_bar(
        timestamp=datetime(2025, 1, 9, 15, 31, tzinfo=UTC),
        symbol="AAPL",
        close_price="25.25",
        volume=480,
    )

    RecordingUnitOfWork.raw_bars_by_key = {
        ("AAPL", date(2025, 1, 10)): [raw_bar_aapl_1, raw_bar_aapl_2],
    }

    service.normalization_service = FakeNormalizationService(
        parsed_actions=[
            action_1,
            ValueError("parse failed"),
            action_invalid,
            action_2,
        ]
    )
    service.validation_service = FakeValidationService(
        validation_results=[
            FakeValidationResult(ok=True, violations=[]),
            FakeValidationResult(ok=False, violations=["invalid action"]),
            FakeValidationResult(ok=True, violations=[]),
        ]
    )
    service.adjustment_service = FakeAdjustmentService(
        supported_results=[True, False],
        adjusted_bars_results=[[adjusted_bar_aapl_1, adjusted_bar_aapl_2]],
    )

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.client.fetch_corporate_actions",
        lambda: raw_payload,
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.SorUnitOfWork",
        RecordingUnitOfWork,
    )

    service.ingest_corporate_actions()

    assert len(RecordingUnitOfWork.instances) == 1
    uow = RecordingUnitOfWork.instances[0]

    assert uow.entered is True
    assert uow.exited is True
    assert uow.committed is True
    assert uow.rolled_back is False

    assert uow.persisted_actions == [action_1, action_2]
    assert uow.persisted_bars == [adjusted_bar_aapl_1, adjusted_bar_aapl_2]

    assert uow.market_bars.get_raw_bars_calls == [
        {
            "symbol": "AAPL",
            "effective_date": date(2025, 1, 10),
        }
    ]

    assert audit_logger.parse_failed_calls == [
        {
            "run_id": "run-test-001",
            "symbol": "MSFT",
            "cycle_timestamp": cycle_timestamp,
        }
    ]
    assert audit_logger.validation_failed_calls == [
        {
            "run_id": "run-test-001",
            "symbol": "TSLA",
            "cycle_timestamp": cycle_timestamp,
        }
    ]
    assert audit_logger.adjustment_applied_calls == [
        {
            "run_id": "run-test-001",
            "symbol": "AAPL",
            "cycle_timestamp": cycle_timestamp,
        }
    ]


def test_ingest_corporate_actions_commits_transaction_on_success(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
) -> None:
    raw_payload = {
        "corporate_actions": [
            {"id": "ca-001", "symbol": "AAPL"},
        ]
    }

    action = FakeCorporateAction(
        action_id="ca-001",
        symbol="AAPL",
        effective_date=date(2025, 1, 10),
        action_type="cash_dividend",
    )

    service.normalization_service = FakeNormalizationService(parsed_actions=[action])
    service.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])]
    )
    service.adjustment_service = FakeAdjustmentService(
        supported_results=[False],
    )

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.client.fetch_corporate_actions",
        lambda: raw_payload,
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.SorUnitOfWork",
        RecordingUnitOfWork,
    )

    service.ingest_corporate_actions()

    assert len(RecordingUnitOfWork.instances) == 1
    uow = RecordingUnitOfWork.instances[0]

    assert uow.entered is True
    assert uow.exited is True
    assert uow.committed is True
    assert uow.rolled_back is False
    assert uow.persisted_actions == [action]
    assert uow.persisted_bars == []


def test_ingest_corporate_actions_rolls_back_transaction_on_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
) -> None:
    raw_payload = {
        "corporate_actions": [
            {"id": "ca-001", "symbol": "AAPL"},
        ]
    }

    action = FakeCorporateAction(
        action_id="ca-001",
        symbol="AAPL",
        effective_date=date(2025, 1, 10),
        action_type="cash_dividend",
    )

    service.normalization_service = FakeNormalizationService(parsed_actions=[action])
    service.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])]
    )
    service.adjustment_service = FakeAdjustmentService(
        supported_results=[False],
    )

    failure = RuntimeError("simulated database failure")

    class FailingRecordingUnitOfWork(RecordingUnitOfWork):
        def __init__(self, session: object) -> None:
            super().__init__(session, raise_on_action_upsert=failure)

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.client.fetch_corporate_actions",
        lambda: raw_payload,
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.SorUnitOfWork",
        FailingRecordingUnitOfWork,
    )

    with pytest.raises(RuntimeError, match="simulated database failure"):
        service.ingest_corporate_actions()

    assert len(RecordingUnitOfWork.instances) == 1
    uow = RecordingUnitOfWork.instances[0]

    assert uow.entered is True
    assert uow.exited is True
    assert uow.committed is False
    assert uow.rolled_back is True
    assert uow.persisted_actions == []
    assert uow.persisted_bars == []


def test_reprocessing_same_payload_does_not_duplicate_entries_when_repositories_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
) -> None:
    """
    Integration-ish idempotency test:
    - the same payload is ingested twice
    - fake repositories enforce upsert semantics by identity key
    - stored actions and adjusted bars are not duplicated
    """
    raw_payload = {
        "corporate_actions": [
            {"id": "ca-001", "symbol": "AAPL"},
        ]
    }

    action = FakeCorporateAction(
        action_id="ca-001",
        symbol="AAPL",
        effective_date=date(2025, 1, 10),
        action_type="forward_split",
    )

    raw_bar = make_minute_bar(
        timestamp=datetime(2025, 1, 9, 15, 30, tzinfo=UTC),
        symbol="AAPL",
        close_price="100.00",
        volume=100,
    )
    adjusted_bar = make_minute_bar(
        timestamp=datetime(2025, 1, 9, 15, 30, tzinfo=UTC),
        symbol="AAPL",
        close_price="25.00",
        volume=400,
    )

    RecordingUnitOfWork.raw_bars_by_key = {
        ("AAPL", date(2025, 1, 10)): [raw_bar],
    }

    persisted_actions_by_id: dict[object, object] = {}
    persisted_bars_by_id: dict[object, object] = {}

    class SharedRecordingUnitOfWork(RecordingUnitOfWork):
        def __init__(self, session: object) -> None:
            super().__init__(session)
            self.persisted_actions_by_id = persisted_actions_by_id
            self.persisted_bars_by_id = persisted_bars_by_id
            self.persisted_actions = list(persisted_actions_by_id.values())
            self.persisted_bars = list(persisted_bars_by_id.values())

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.client.fetch_corporate_actions",
        lambda: raw_payload,
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.SorUnitOfWork",
        SharedRecordingUnitOfWork,
    )

    # first pass
    service.normalization_service = FakeNormalizationService(parsed_actions=[action])
    service.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])]
    )
    service.adjustment_service = FakeAdjustmentService(
        supported_results=[True],
        adjusted_bars_results=[[adjusted_bar]],
    )
    service.ingest_corporate_actions()

    # second pass of same payload
    service.normalization_service = FakeNormalizationService(parsed_actions=[action])
    service.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])]
    )
    service.adjustment_service = FakeAdjustmentService(
        supported_results=[True],
        adjusted_bars_results=[[adjusted_bar]],
    )
    service.ingest_corporate_actions()

    assert len(RecordingUnitOfWork.instances) == 2

    assert list(persisted_actions_by_id.values()) == [action]
    assert list(persisted_bars_by_id.values()) == [adjusted_bar]


def test_reprocessing_same_payload_is_idempotent_against_real_storage(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_payload = {
        "corporate_actions": [
            {"id": "ca-001", "symbol": "AAPL"},
        ]
    }

    test_id = uuid4().hex
    raw_ts = datetime(2025, 1, 9, 15, 30, 17, tzinfo=UTC)

    action = CorporateAction(
        action_id=f"ca-{test_id}",
        symbol="AAPL",
        action_type=CorporateActionType.SPLIT_FORWARD,
        effective_date=date(2025, 1, 10),
        currency="USD",
        source="alpaca",
        ingested_at=datetime(2025, 1, 10, tzinfo=UTC),
    )

    raw_bar = MarketBar(
        bar_id=f"bar-raw-{test_id}",
        symbol="AAPL",
        timestamp=raw_ts,
        end_timestamp=datetime(2025, 1, 9, 15, 35, 17, tzinfo=UTC),
        interval="1m",
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=100,
        price_basis=PriceBasis.RAW,
        adjustment_factor=Decimal("1.0"),
        source="alpaca",
        ingested_at=datetime.now(tz=UTC),
        market_session="regular",
    )

    adjusted_bar = MarketBar(
        bar_id=f"bar-adjusted-{test_id}",
        symbol="AAPL",
        timestamp=raw_bar.timestamp,
        end_timestamp=raw_bar.end_timestamp,
        interval="1m",
        open=Decimal("25"),
        high=Decimal("25"),
        low=Decimal("25"),
        close=Decimal("25"),
        volume=400,
        price_basis=PriceBasis.ADJUSTED,
        adjustment_factor=Decimal("0.25"),
        source="adjusted",
        ingested_at=datetime.now(tz=UTC),
        market_session="regular",
    )

    db_session.add(raw_bar)
    db_session.commit()

    service = CorporateActionIngestionService(
        session=db_session,
        run_id="run-test-001",
        audit_logger=FakeAuditLogger(),
        cycle_timestamp=datetime(2025, 1, 10, 14, 35, tzinfo=UTC),
    )

    monkeypatch.setattr(
        "autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service.client.fetch_corporate_actions",
        lambda: raw_payload,
    )

    service.normalization_service = FakeNormalizationService(parsed_actions=[action])
    service.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])]
    )
    service.adjustment_service = FakeAdjustmentService(
        supported_results=[True],
        adjusted_bars_results=[[adjusted_bar]],
    )

    service.ingest_corporate_actions()

    action_count_1 = (
        db_session.query(CorporateAction)
        .filter(CorporateAction.action_id == f"ca-{test_id}")
        .count()
    )
    bar_count_1 = (
        db_session.query(MarketBar)
        .filter(
            MarketBar.symbol == "AAPL",
            MarketBar.timestamp == raw_bar.timestamp,
        )
        .count()
    )

    service.normalization_service = FakeNormalizationService(parsed_actions=[action])
    service.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])]
    )
    service.adjustment_service = FakeAdjustmentService(
        supported_results=[True],
        adjusted_bars_results=[[adjusted_bar]],
    )

    service.ingest_corporate_actions()

    action_count_2 = (
        db_session.query(CorporateAction)
        .filter(CorporateAction.action_id == f"ca-{test_id}")
        .count()
    )
    bar_count_2 = (
        db_session.query(MarketBar)
        .filter(
            MarketBar.symbol == "AAPL",
            MarketBar.timestamp == raw_bar.timestamp,
        )
        .count()
    )

    assert action_count_2 == action_count_1
    assert bar_count_2 == bar_count_1
