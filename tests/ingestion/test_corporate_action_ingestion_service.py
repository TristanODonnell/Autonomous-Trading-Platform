from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import TracebackType
from typing import Any, Literal, cast

import pytest

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service import (
    CorporateActionIngestionService,
)

_FETCH_TARGET = (
    "autonomous_trading_platform.ingestion.corporate_actions.services."
    "corporate_action_ingestion_service.client.fetch_corporate_actions"
)
_UOW_TARGET = (
    "autonomous_trading_platform.ingestion.corporate_actions.services."
    "corporate_action_ingestion_service.SorUnitOfWork"
)


@dataclass(frozen=True)
class FakeValidationResult:
    ok: bool
    violations: list[str]


@dataclass(frozen=True)
class FakeCorporateAction:
    action_id: str
    symbol: str
    effective_date: date
    action_type: CorporateActionType
    new_symbol: str = ""


@dataclass(frozen=True)
class FakeUpsertResult:
    entity: object
    created: bool


class FakeAuditLogger:
    def __init__(self) -> None:
        self.parse_failed_calls: list[dict[str, object]] = []
        self.validation_failed_calls: list[dict[str, object]] = []
        self.manual_review_calls: list[dict[str, object]] = []

    def record_corporate_action_parse_failed(
        self, *, run_id: str, symbol: str, cycle_timestamp: datetime
    ) -> None:
        self.parse_failed_calls.append(
            {"run_id": run_id, "symbol": symbol, "cycle_timestamp": cycle_timestamp}
        )

    def record_corporate_action_validation_failed(
        self, *, run_id: str, symbol: str, cycle_timestamp: datetime
    ) -> None:
        self.validation_failed_calls.append(
            {"run_id": run_id, "symbol": symbol, "cycle_timestamp": cycle_timestamp}
        )

    def record_corporate_action_manual_review_required(
        self,
        *,
        run_id: str,
        symbol: str,
        action_type: str,
        effective_date: date,
        cycle_timestamp: datetime,
    ) -> None:
        self.manual_review_calls.append(
            {
                "run_id": run_id,
                "symbol": symbol,
                "action_type": action_type,
                "effective_date": effective_date,
                "cycle_timestamp": cycle_timestamp,
            }
        )


class FakeNormalizationService:
    def __init__(self, *, parsed_actions: list[FakeCorporateAction | ValueError]) -> None:
        self.parsed_actions = list(parsed_actions)
        self.calls: list[tuple[dict[str, object], str | None]] = []

    def parse_alpaca_corporate_action(
        self, raw_action: dict[str, object], provider_type: str | None = None
    ) -> FakeCorporateAction:
        self.calls.append((raw_action, provider_type))
        if not self.parsed_actions:
            raise AssertionError("No more seeded normalization results.")
        result = self.parsed_actions.pop(0)
        if isinstance(result, ValueError):
            raise result
        return result


class FakeValidationService:
    def __init__(self, *, validation_results: list[FakeValidationResult]) -> None:
        self.validation_results = list(validation_results)
        self.calls: list[FakeCorporateAction] = []

    def validate(self, action: FakeCorporateAction) -> FakeValidationResult:
        self.calls.append(action)
        if not self.validation_results:
            raise AssertionError("No more seeded validation results.")
        return self.validation_results.pop(0)


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


class RecordingUnitOfWork:
    instances: list[RecordingUnitOfWork] = []

    def __init__(self, session: object, *, raise_on_action_upsert: Exception | None = None) -> None:
        self.session = session
        self.raise_on_action_upsert = raise_on_action_upsert
        self.persisted_actions: list[object] = []
        self.persisted_actions_by_id: dict[object, object] = {}
        self.entered = False
        self.exited = False
        self.committed = False
        self.rolled_back = False
        self.corporate_actions = FakeCorporateActionsRepository(self)
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


@pytest.fixture
def audit_logger() -> FakeAuditLogger:
    return FakeAuditLogger()


@pytest.fixture
def cycle_timestamp() -> datetime:
    return datetime(2025, 1, 15, 14, 35, tzinfo=UTC)


@pytest.fixture
def service(
    audit_logger: FakeAuditLogger, cycle_timestamp: datetime
) -> CorporateActionIngestionService:
    return CorporateActionIngestionService(
        session=cast(Any, object()),
        run_id="run-test-001",
        audit_logger=audit_logger,
        cycle_timestamp=cycle_timestamp,
        fetch_start="2025-01-08",
        fetch_end="2025-02-14",
        fetch_symbols=["AAPL", "MSFT", "TSLA", "NVDA", "PXD"],
    )


def _patch(monkeypatch: pytest.MonkeyPatch, payload: dict, uow: type = RecordingUnitOfWork) -> dict:
    fetch_calls: dict[str, object] = {}

    def fake_fetch(**kwargs: object) -> dict:
        fetch_calls.update(kwargs)
        return payload

    monkeypatch.setattr(_FETCH_TARGET, fake_fetch)
    monkeypatch.setattr(_UOW_TARGET, uow)
    return fetch_calls


def test_ingest_tags_every_alpaca_list_stores_valid_actions_and_flags_manual_review(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
    audit_logger: FakeAuditLogger,
    cycle_timestamp: datetime,
) -> None:
    """Every Alpaca list is processed with its list key as the type; parse and
    validation failures are logged and skipped; stored actions the platform does not
    apply (mergers) are flagged for manual review; unknown lists are counted."""
    raw_payload = {
        "corporate_actions": {
            "cash_dividends": [
                {"id": "ca-001", "symbol": "AAPL"},
                {"id": "ca-parse-fail", "symbol": "MSFT"},
                {"id": "ca-invalid", "symbol": "TSLA"},
            ],
            "forward_splits": [{"id": "ca-002", "symbol": "NVDA"}],
            "stock_mergers": [{"id": "ca-003", "acquiree_symbol": "PXD"}],
            "worthless_removals": [{"id": "ca-999", "symbol": "ZZZ"}],
        }
    }
    dividend = FakeCorporateAction(
        "ca-001", "AAPL", date(2025, 1, 10), CorporateActionType.CASH_DIVIDEND
    )
    invalid = FakeCorporateAction(
        "ca-invalid", "TSLA", date(2025, 1, 12), CorporateActionType.CASH_DIVIDEND
    )
    split = FakeCorporateAction(
        "ca-002", "NVDA", date(2025, 1, 14), CorporateActionType.SPLIT_FORWARD
    )
    merger = FakeCorporateAction(
        "ca-003", "PXD", date(2025, 1, 20), CorporateActionType.MERGER_STOCK, new_symbol="XOM"
    )

    normalizer = FakeNormalizationService(
        parsed_actions=[dividend, ValueError("parse failed"), invalid, split, merger]
    )
    service.normalization_service = cast(Any, normalizer)
    service.validation_service = cast(
        Any,
        FakeValidationService(
            validation_results=[
                FakeValidationResult(ok=True, violations=[]),
                FakeValidationResult(ok=False, violations=["invalid action"]),
                FakeValidationResult(ok=True, violations=[]),
                FakeValidationResult(ok=True, violations=[]),
            ]
        ),
    )
    fetch_calls = _patch(monkeypatch, raw_payload)

    result = service.ingest_corporate_actions()

    assert fetch_calls == {
        "start": "2025-01-08",
        "end": "2025-02-14",
        "symbols": ["AAPL", "MSFT", "TSLA", "NVDA", "PXD"],
    }
    # The list key is the type signal handed to the normalizer.
    assert [call[1] for call in normalizer.calls] == [
        "cash_dividend",
        "cash_dividend",
        "cash_dividend",
        "forward_split",
        "stock_merger",
    ]

    uow = RecordingUnitOfWork.instances[0]
    assert uow.committed is True and uow.rolled_back is False
    assert uow.persisted_actions == [dividend, split, merger]
    assert result.created_actions == [dividend, split, merger]
    assert result.manual_review_actions == [merger]

    counts = result.counts.as_dict()
    assert counts == {
        "fetched": 6,
        "unsupported": 1,
        "parse_failed": 1,
        "validation_failed": 1,
        "created": 3,
        "updated": 0,
        "manual_review": 1,
        "unsupported_list_keys": ["worthless_removals"],
    }

    assert audit_logger.parse_failed_calls == [
        {"run_id": "run-test-001", "symbol": "MSFT", "cycle_timestamp": cycle_timestamp}
    ]
    assert audit_logger.validation_failed_calls == [
        {"run_id": "run-test-001", "symbol": "TSLA", "cycle_timestamp": cycle_timestamp}
    ]
    assert audit_logger.manual_review_calls == [
        {
            "run_id": "run-test-001",
            "symbol": "PXD",
            "action_type": "merger_stock",
            "effective_date": date(2025, 1, 20),
            "cycle_timestamp": cycle_timestamp,
        }
    ]


def test_ingest_counts_re_fetched_actions_as_updates_not_creates(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
) -> None:
    raw_payload = {"corporate_actions": {"cash_dividends": [{"id": "ca-001", "symbol": "AAPL"}]}}
    action = FakeCorporateAction(
        "ca-001", "AAPL", date(2025, 1, 10), CorporateActionType.CASH_DIVIDEND
    )
    shared: dict[object, object] = {}

    class SharedRecordingUnitOfWork(RecordingUnitOfWork):
        def __init__(self, session: object) -> None:
            super().__init__(session)
            self.persisted_actions_by_id = shared
            self.persisted_actions = list(shared.values())

    _patch(monkeypatch, raw_payload, uow=SharedRecordingUnitOfWork)

    for _ in range(2):
        service.normalization_service = cast(Any, FakeNormalizationService(parsed_actions=[action]))
        service.validation_service = cast(
            Any,
            FakeValidationService(
                validation_results=[FakeValidationResult(ok=True, violations=[])]
            ),
        )
        result = service.ingest_corporate_actions()

    assert list(shared.values()) == [action]
    assert result.created_actions == []
    assert result.updated_actions == [action]
    assert result.counts.created == 0 and result.counts.updated == 1


def test_ingest_rolls_back_transaction_on_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
) -> None:
    raw_payload = {"corporate_actions": {"cash_dividends": [{"id": "ca-001", "symbol": "AAPL"}]}}
    action = FakeCorporateAction(
        "ca-001", "AAPL", date(2025, 1, 10), CorporateActionType.CASH_DIVIDEND
    )
    service.normalization_service = cast(Any, FakeNormalizationService(parsed_actions=[action]))
    service.validation_service = cast(
        Any,
        FakeValidationService(validation_results=[FakeValidationResult(ok=True, violations=[])]),
    )

    class FailingRecordingUnitOfWork(RecordingUnitOfWork):
        def __init__(self, session: object) -> None:
            super().__init__(
                session, raise_on_action_upsert=RuntimeError("simulated database failure")
            )

    _patch(monkeypatch, raw_payload, uow=FailingRecordingUnitOfWork)

    with pytest.raises(RuntimeError, match="simulated database failure"):
        service.ingest_corporate_actions()

    uow = RecordingUnitOfWork.instances[0]
    assert uow.committed is False and uow.rolled_back is True
    assert uow.persisted_actions == []


def test_ingest_with_empty_payload_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
    service: CorporateActionIngestionService,
) -> None:
    _patch(monkeypatch, {"corporate_actions": {}})
    result = service.ingest_corporate_actions()
    assert result.created_actions == []
    assert result.counts.fetched == 0


def test_ingest_only_sends_fetch_parameters_that_were_given(
    monkeypatch: pytest.MonkeyPatch,
    audit_logger: FakeAuditLogger,
    cycle_timestamp: datetime,
) -> None:
    service = CorporateActionIngestionService(
        session=cast(Any, object()),
        run_id="run-test-002",
        audit_logger=audit_logger,
        cycle_timestamp=cycle_timestamp,
    )
    fetch_calls = _patch(monkeypatch, {"corporate_actions": {}})
    service.ingest_corporate_actions()
    assert fetch_calls == {}
