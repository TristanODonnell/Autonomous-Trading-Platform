from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from autonomous_trading_platform.contracts.common.enums import BarQualityFlag
from autonomous_trading_platform.ingestion.market_data.services.bar_ingestion_service import (
    BarIngestionService,
)
from tests.utilities.factories import make_five_minute_bar


@dataclass
class FakeValidationResult:
    ok: bool
    violations: list[str]


class FakeAuditLogger:
    def __init__(self) -> None:
        self.late_calls: list[dict[str, Any]] = []
        self.outlier_calls: list[dict[str, Any]] = []

    def record_bar_late(
        self,
        *,
        run_id: str,
        symbol: str,
        bar_end_timestamp: datetime,
    ) -> None:
        self.late_calls.append(
            {
                "run_id": run_id,
                "symbol": symbol,
                "bar_end_timestamp": bar_end_timestamp,
            }
        )

    def record_bar_outlier(
        self,
        *,
        run_id: str,
        symbol: str,
        cycle_timestamp: datetime,
        reference_close: Decimal,
        observed_close: Decimal,
    ) -> None:
        self.outlier_calls.append(
            {
                "run_id": run_id,
                "symbol": symbol,
                "cycle_timestamp": cycle_timestamp,
                "reference_close": reference_close,
                "observed_close": observed_close,
            }
        )


class FakeAggregator:
    """
    Returns pre-seeded aggregated bars on specific calls to simulate a stream
    of minute bars flowing through the real ingestion service.
    """

    def __init__(self, outputs: list[object | None]) -> None:
        self._outputs = list(outputs)
        self.received_minute_bars: list[object] = []

    def add_minute_bar(self, minute_bar: object) -> object | None:
        self.received_minute_bars.append(minute_bar)
        if not self._outputs:
            return None
        return self._outputs.pop(0)


class FakeValidationService:
    def __init__(
        self,
        *,
        validation_results: list[FakeValidationResult] | None = None,
        late_results: list[bool] | None = None,
        outlier_results: list[bool] | None = None,
    ) -> None:
        self.validation_results = (
            list(validation_results)
            if validation_results is not None
            else [FakeValidationResult(ok=True, violations=[])]
        )
        self.late_results = list(late_results) if late_results is not None else [False]
        self.outlier_results = list(outlier_results) if outlier_results is not None else [False]

        self.validate_calls: list[object] = []
        self.late_calls: list[tuple[object, datetime, timedelta]] = []
        self.outlier_calls: list[tuple[object, Decimal | None]] = []

    def validate_bar(self, bar: object) -> FakeValidationResult:
        self.validate_calls.append(bar)
        if len(self.validation_results) == 1:
            return self.validation_results[0]
        return self.validation_results.pop(0)

    def is_late_bar(
        self,
        bar: object,
        *,
        now_utc: datetime,
        allowed_delay: timedelta,
    ) -> bool:
        self.late_calls.append((bar, now_utc, allowed_delay))
        if len(self.late_results) == 1:
            return self.late_results[0]
        return self.late_results.pop(0)

    def is_suspected_outlier(
        self,
        bar: object,
        reference_close: Decimal | None,
    ) -> bool:
        self.outlier_calls.append((bar, reference_close))
        if len(self.outlier_results) == 1:
            return self.outlier_results[0]
        return self.outlier_results.pop(0)


class FakeMarketBarsRepository:
    def __init__(self, parent: RecordingUnitOfWork) -> None:
        self.parent = parent

    def upsert(self, bar: object) -> None:
        if self.parent.raise_on_upsert:
            raise self.parent.raise_on_upsert
        self.parent.persisted_bars.append(bar)


class RecordingUnitOfWork:
    instances: list[RecordingUnitOfWork] = []

    def __init__(self, session: object, *, raise_on_upsert: Exception | None = None) -> None:
        self.session = session
        self.raise_on_upsert = raise_on_upsert
        self.persisted_bars: list[object] = []
        self.entered = False
        self.exited = False
        self.committed = False
        self.rolled_back = False
        self.market_bars = FakeMarketBarsRepository(self)
        RecordingUnitOfWork.instances.append(self)

    def __enter__(self) -> RecordingUnitOfWork:
        self.entered = True
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
        self.exited = True
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True
        return False


def make_provider_bar(
    *,
    symbol: str = "AAPL",
    timestamp: datetime,
    open_price: float = 100.0,
    high_price: float = 101.0,
    low_price: float = 99.0,
    close_price: float = 100.5,
    volume: int = 100,
    vwap: float | None = 100.25,
    trade_count: int | None = 10,
) -> SimpleNamespace:
    # Duck-typed provider object; BarIngestionService only reads these attributes.
    return SimpleNamespace(
        symbol=symbol,
        timestamp=timestamp,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        vwap=vwap,
        trade_count=trade_count,
    )


@pytest.fixture(autouse=True)
def clear_recording_uow_instances() -> None:
    RecordingUnitOfWork.instances.clear()


@pytest.fixture
def audit_logger() -> FakeAuditLogger:
    return FakeAuditLogger()


@pytest.fixture
def session() -> object:
    return object()


@pytest.fixture
def service(session: object, audit_logger: FakeAuditLogger) -> BarIngestionService:
    return BarIngestionService(
        session=session,
        run_id="run-test-001",
        audit_logger=cast(Any, audit_logger),
    )


def make_uow_factory(*, raise_on_upsert: Exception | None = None):
    def factory(session: object) -> RecordingUnitOfWork:
        return RecordingUnitOfWork(session, raise_on_upsert=raise_on_upsert)

    return factory


@pytest.mark.asyncio
async def test_handle_minute_bar_stream_writes_completed_bars_and_logs_late_and_outlier(
    monkeypatch: pytest.MonkeyPatch,
    service: BarIngestionService,
    audit_logger: FakeAuditLogger,
) -> None:
    """
    Integration-ish orchestration test:
    - a stream of provider bars flows through handle_minute_bar
    - only completed aggregated bars are persisted
    - late bars are logged
    - suspected outliers are logged when a reference close exists
    """
    bar_1 = make_five_minute_bar(
        timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
        close_price="100.00",
        volume=500,
    )
    bar_2 = make_five_minute_bar(
        timestamp=datetime(2025, 1, 1, 15, 35, tzinfo=UTC),
        close_price="130.00",
        volume=600,
    )

    svc = cast(Any, service)

    svc.aggregator = FakeAggregator(
        outputs=[
            None,
            None,
            None,
            None,
            bar_1,
            None,
            None,
            None,
            None,
            bar_2,
        ]
    )
    svc.validation_service = FakeValidationService(
        validation_results=[
            FakeValidationResult(ok=True, violations=[]),
            FakeValidationResult(ok=True, violations=[]),
        ],
        late_results=[False, True],
        outlier_results=[False, True],
    )

    svc.uow_factory = make_uow_factory()

    provider_stream = [
        make_provider_bar(timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC) + timedelta(minutes=i))
        for i in range(10)
    ]

    returned: list[object | None] = []
    for provider_bar in provider_stream:
        returned.append(await service.handle_minute_bar(provider_bar))

    assert len(RecordingUnitOfWork.instances) == 2

    first_uow = RecordingUnitOfWork.instances[0]
    second_uow = RecordingUnitOfWork.instances[1]

    assert first_uow.committed is True
    assert second_uow.committed is True
    assert first_uow.persisted_bars == [bar_1]
    assert second_uow.persisted_bars == [bar_2]

    # first complete bar returns the bar
    assert returned[4] == bar_1

    # late bars are persisted/logged, but service returns None afterward
    assert returned[9] is None

    assert audit_logger.late_calls == [
        {
            "run_id": "run-test-001",
            "symbol": bar_2.symbol,
            "bar_end_timestamp": bar_2.end_timestamp,
        }
    ]

    assert audit_logger.outlier_calls == [
        {
            "run_id": "run-test-001",
            "symbol": bar_2.symbol,
            "cycle_timestamp": bar_2.timestamp,
            "reference_close": Decimal("100.00"),
            "observed_close": Decimal("130.00"),
        }
    ]

    assert BarQualityFlag.LATE in bar_2.quality_flags
    assert BarQualityFlag.SUSPECTED_OUTLIER in bar_2.quality_flags

    # late bars should not advance the last_bar cache
    assert service.last_bar_by_symbol[bar_1.symbol] == bar_1


@pytest.mark.asyncio
async def test_handle_minute_bar_commits_transaction_on_success(
    monkeypatch: pytest.MonkeyPatch,
    service: BarIngestionService,
) -> None:
    completed_bar = make_five_minute_bar(
        timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
    )

    svc = cast(Any, service)

    svc.aggregator = FakeAggregator(outputs=[completed_bar])
    svc.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])],
        late_results=[False],
        outlier_results=[False],
    )

    svc.uow_factory = make_uow_factory()

    result = await service.handle_minute_bar(
        make_provider_bar(timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC))
    )

    assert result == completed_bar
    assert len(RecordingUnitOfWork.instances) == 1

    uow = RecordingUnitOfWork.instances[0]
    assert uow.entered is True
    assert uow.exited is True
    assert uow.committed is True
    assert uow.rolled_back is False
    assert uow.persisted_bars == [completed_bar]


@pytest.mark.asyncio
async def test_handle_minute_bar_rolls_back_transaction_on_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
    service: BarIngestionService,
) -> None:
    completed_bar = make_five_minute_bar(
        timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
    )

    svc = cast(Any, service)

    svc.aggregator = FakeAggregator(outputs=[completed_bar])
    svc.validation_service = FakeValidationService(
        validation_results=[FakeValidationResult(ok=True, violations=[])],
        late_results=[False],
        outlier_results=[False],
    )

    failure = RuntimeError("simulated database failure")

    svc.uow_factory = make_uow_factory(raise_on_upsert=failure)

    with pytest.raises(RuntimeError, match="simulated database failure"):
        await service.handle_minute_bar(
            make_provider_bar(timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC))
        )

    assert len(RecordingUnitOfWork.instances) == 1

    uow = RecordingUnitOfWork.instances[0]
    assert uow.entered is True
    assert uow.exited is True
    assert uow.committed is False
    assert uow.rolled_back is True
    assert uow.persisted_bars == []


@pytest.mark.asyncio
async def test_next_bar_decision_triggers_evaluation_only_on_complete_bars(
    service: BarIngestionService,
) -> None:
    bar = make_five_minute_bar(
        timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC),
    )
    svc = cast(Any, service)
    svc.aggregator = FakeAggregator(outputs=[None, bar])
    svc.validation_service = FakeValidationService(
        validation_results=[
            FakeValidationResult(ok=True, violations=[]),
        ],
        late_results=[False],
        outlier_results=[False],
    )

    svc.uow_factory = make_uow_factory()
    # 1. First call → incomplete
    result_1 = await service.handle_minute_bar(
        make_provider_bar(timestamp=datetime(2025, 1, 1, 15, 30, tzinfo=UTC))
    )

    assert result_1 is None
    assert service.next_bar_decision.should_schedule_evaluation is False
    assert service.next_bar_decision.reason == "incomplete_bar"

    # 2. Second call → complete + valid
    result_2 = await service.handle_minute_bar(
        make_provider_bar(timestamp=datetime(2025, 1, 1, 15, 31, tzinfo=UTC))
    )

    assert result_2 == bar
    assert service.next_bar_decision.should_schedule_evaluation is True
    assert service.next_bar_decision.reason == "complete_valid_bar"
    assert service.next_bar_decision.bar == bar
