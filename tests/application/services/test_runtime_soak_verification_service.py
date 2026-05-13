from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.runtime_soak_verification_service import (
    RuntimeSoakVerificationService,
)
from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    OrderSource,
    OrderStatus,
    OrderType,
    PriceBasis,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakCheckName,
    RuntimeSoakSeverity,
    RuntimeSoakStatus,
    RuntimeSoakVerificationReport,
)
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_run_manifest,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)
from autonomous_trading_platform.storage.sor.repositories.queries.runtime_soak_verification_repository import (
    DuplicateFillGroup,
)


def _build_service(
    db_session: Session, *, stale_after: timedelta = timedelta(minutes=15)
) -> RuntimeSoakVerificationService:
    return RuntimeSoakVerificationService(
        session=db_session,
        environment="paper",
        stale_after=stale_after,
    )


def _window() -> tuple[datetime, datetime]:
    window_start = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    window_end = window_start + timedelta(minutes=30)
    return window_start, window_end


def _seed_runtime_job_run(
    db_session: Session,
    *,
    job_name: str,
    status: str,
    started_at: datetime,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
    error_message: str | None = None,
) -> RuntimeJobRuns:
    row = RuntimeJobRuns(
        job_run_id=str(uuid4()),
        job_name=job_name,
        parent_job_run_id=None,
        status=status,
        trigger_type="scheduled",
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        error_message=error_message,
        correlation_id=str(uuid4()),
        input_summary_json=None,
        output_summary_json=None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_completed_trading_manifest(
    db_session: Session,
    *,
    created_at: datetime,
    last_successful_step: str = "risk_snapshot",
    status: str = "completed",
) -> RunManifestRow:
    manifest = build_trading_run_manifest(
        run_id=uuid4(),
        now_utc=created_at,
        cycle_start=created_at - timedelta(minutes=5),
        cycle_end=created_at,
    )
    manifest.created_at = created_at
    manifest.status = status
    manifest.current_step = last_successful_step
    manifest.last_successful_step = last_successful_step

    repository = RunManifestRepository(db_session)
    return repository.add(manifest)


def _seed_healthy_runtime_window(db_session: Session) -> None:
    window_start, window_end = _window()
    for index, job_name in enumerate(
        (
            "market_ingestion_cycle",
            "feature_pipeline_cycle",
            "trading_cycle",
        )
    ):
        started_at = window_start + timedelta(minutes=5 + index)
        completed_at = started_at + timedelta(minutes=1)
        _seed_runtime_job_run(
            db_session,
            job_name=job_name,
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=60_000,
        )

    _seed_completed_trading_manifest(
        db_session,
        created_at=window_end - timedelta(minutes=1),
    )


def _seed_broker_order(
    db_session: Session,
    *,
    submitted_at: datetime,
    updated_at: datetime,
    status: OrderStatus = OrderStatus.SUBMITTED,
    filled_qty: Decimal = Decimal("0"),
    raw_broker_payload: dict | None = None,
    last_error: str | None = None,
) -> BrokerOrder:
    intent_id = uuid4()
    run_id = uuid4()
    row = BrokerOrder(
        broker_order_id=str(uuid4()),
        client_order_id=f"client-{uuid4()}",
        intent_id=intent_id,
        run_id=run_id,
        broker="alpaca",
        account_id="paper",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        extended_hours=False,
        qty=Decimal("1"),
        notional=None,
        limit_price=None,
        stop_price=None,
        status=status,
        submitted_at=submitted_at,
        updated_at=updated_at,
        filled_qty=filled_qty,
        avg_fill_price=None,
        last_error=last_error,
        raw_broker_payload=raw_broker_payload,
        requested_qty=Decimal("1"),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_order_intent(
    db_session: Session,
    *,
    run_id,
    intent_id,
    timestamp: datetime,
    idempotency_key: str,
) -> None:
    db_session.add(
        OrderIntents(
            intent_id=intent_id,
            idempotency_key=idempotency_key,
            run_id=run_id,
            strategy_id="test-strategy",
            timestamp=timestamp,
            bar_timestamp=timestamp,
            symbol="AAPL",
            side=Side.BUY,
            qty=Decimal("1"),
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=f"client-{intent_id}",
            meta=None,
        )
    )


def _seed_fill(
    db_session: Session,
    *,
    timestamp: datetime,
    broker_order_id: str,
    broker_fill_id: str,
    execution_id: str,
    idempotency_key: str,
) -> Fill:
    run_id = uuid4()
    intent_id = uuid4()
    _seed_order_intent(
        db_session,
        run_id=run_id,
        intent_id=intent_id,
        timestamp=timestamp,
        idempotency_key=idempotency_key,
    )
    row = Fill(
        fill_id=str(uuid4()),
        broker_order_id=broker_order_id,
        intent_id=intent_id,
        run_id=run_id,
        timestamp=timestamp,
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100.00"),
        fees=None,
        liquidity=None,
        venue=None,
        meta={
            "broker_fill_id": broker_fill_id,
            "execution_id": execution_id,
        },
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_cash_snapshot(
    db_session: Session,
    *,
    timestamp: datetime,
    source: OrderSource,
    cash: Decimal,
    equity: Decimal,
) -> CashSnapshot:
    row = CashSnapshot(
        snapshot_id=uuid4(),
        run_id=uuid4(),
        timestamp=timestamp,
        currency="USD",
        cash=cash,
        buying_power=cash,
        reserved_cash=Decimal("0"),
        equity=equity,
        source=source,
        capital_bucket=None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_raw_bars_dataset(
    db_session: Session,
    *,
    created_at: datetime,
    dataset_version_id: str = "raw-bars-v1",
) -> DatasetVersions:
    persisted_created_at = _sqlite_round_trip_utc(created_at)
    row = DatasetVersions(
        dataset_version_id=dataset_version_id,
        dataset_name="market_bars",
        created_at=persisted_created_at,
        source="alpaca",
        price_basis=PriceBasis.RAW,
        interval=BarInterval.FIVE_MIN,
        schema_version="v1",
        symbol_coverage=1,
        date_coverage_start=created_at.date(),
        date_coverage_end=created_at.date(),
        validation_status="validated",
        checksum="checksum",
        source_dataset_version=None,
        source_manifest={},
        metadata_json=None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_feature_dataset(
    db_session: Session,
    *,
    created_at: datetime,
    dataset_version_id: str = "feature-v1",
    source_dataset_version: str = "raw-bars-v1",
) -> FeatureDatasetVersions:
    persisted_created_at = _sqlite_round_trip_utc(created_at)
    row = FeatureDatasetVersions(
        dataset_version_id=dataset_version_id,
        feature_name="returns",
        dataset_name="feature_dataset",
        created_at=persisted_created_at,
        schema_version="v1",
        source_dataset_version=source_dataset_version,
        underlying_price_basis=PriceBasis.RAW,
        computation_parameters={},
        storage_path="/tmp/features",
        symbol_coverage=1,
        date_coverage_start=created_at.date(),
        date_coverage_end=created_at.date(),
        validation_status="validated",
        checksum="checksum",
        source_manifest={},
        metadata_json=None,
        computation_code_version="dev",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_position_snapshot(
    db_session: Session,
    *,
    timestamp: datetime,
    source: OrderSource,
    positions: dict[str, Decimal],
) -> PositionSnapshot:
    snapshot_id = uuid4()
    snapshot = PositionSnapshot(
        snapshot_id=snapshot_id,
        run_id=uuid4(),
        timestamp=timestamp,
        source=source,
    )
    snapshot.positions = [
        PositionSnapshotItem(
            snapshot_id=snapshot_id,
            symbol=symbol,
            quantity=quantity,
            avg_cost=Decimal("100.00"),
            market_price=Decimal("100.00"),
            market_value=quantity * Decimal("100.00"),
            unrealized_pnl=Decimal("0.00"),
        )
        for symbol, quantity in positions.items()
    ]
    db_session.add(snapshot)
    db_session.flush()
    return snapshot


def _seed_failure_audit_event(
    db_session: Session,
    *,
    event_timestamp: datetime,
    event_type: str = "RUN_FAILED",
) -> AuditLogRow:
    row = AuditLogRow(
        event_id=str(uuid4()),
        run_id=str(uuid4()),
        event_type=event_type,
        component="runtime",
        event_timestamp=event_timestamp,
        message="failure detected",
        event_metadata={"severity": "error"},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_runtime_control_state(
    db_session: Session,
    *,
    trading_enabled: bool = True,
    trading_paused: bool = False,
    kill_switch_enabled: bool = False,
    reason: str | None = None,
) -> RuntimeControlState:
    row = RuntimeControlState(
        control_id="global",
        trading_enabled=trading_enabled,
        trading_paused=trading_paused,
        kill_switch_enabled=kill_switch_enabled,
        trading_mode="paper",
        reason=reason,
        updated_by="tester",
        created_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 8, 12, 5, tzinfo=UTC),
    )
    db_session.merge(row)
    db_session.flush()
    return row


def _seed_risk_snapshot(
    db_session: Session,
    *,
    timestamp: datetime,
    is_blocked: bool,
) -> RiskSnapshot:
    row = RiskSnapshot(
        snapshot_id=uuid4(),
        run_id=uuid4(),
        timestamp=timestamp,
        gross_exposure=Decimal("1000"),
        net_exposure=Decimal("500"),
        leverage=1.5,
        drawdown_pct=0.01,
        limits={},
        utilization={},
        is_blocked=is_blocked,
        block_reasons=["risk_limit"] if is_blocked else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _sqlite_round_trip_utc(timestamp: datetime) -> datetime:
    local_offset = timestamp.astimezone().utcoffset() or timedelta(0)
    return timestamp + local_offset


def test_verify_returns_runtime_soak_report(db_session: Session) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert isinstance(report, RuntimeSoakVerificationReport)
    assert report.checks
    assert report.summary["total_checks"] == len(report.checks)
    assert report.window_start == window_start
    assert report.window_end == window_end


def test_failed_check_escalates_report_status(
    db_session: Session,
    monkeypatch,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    def _failing_check(*, window_start: datetime, window_end: datetime):
        return service._failed(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Data freshness failed for test coverage.",
        )

    monkeypatch.setattr(service, "_check_data_freshness", _failing_check)
    monkeypatch.setattr(
        service,
        "_check_observability_signals",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.OBSERVABILITY_SIGNALS,
            "Observability signals forced to pass for failure aggregation coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_failure_controls",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failure controls forced to pass for failure aggregation coverage.",
        ),
    )

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert report.status == RuntimeSoakStatus.FAILED
    assert report.summary["failed"] == 1
    assert len(report.failed_checks) == 1
    assert report.failed_checks[0].check_name == RuntimeSoakCheckName.DATA_FRESHNESS


def test_warning_check_produces_warning_report_status(
    db_session: Session,
    monkeypatch,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    def _warning_check(*, window_start: datetime, window_end: datetime):
        return service._warning(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Data freshness warning for test coverage.",
        )

    monkeypatch.setattr(service, "_check_data_freshness", _warning_check)
    monkeypatch.setattr(
        service,
        "_check_observability_signals",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.OBSERVABILITY_SIGNALS,
            "Observability signals forced to pass for warning aggregation coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_failure_controls",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failure controls forced to pass for warning aggregation coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_cash_position_equity_consistency",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.CASH_POSITION_EQUITY_CONSISTENCY,
            "Cash/equity consistency forced to pass for warning aggregation coverage.",
        ),
    )

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert report.status == RuntimeSoakStatus.WARNING
    assert report.summary["failed"] == 0
    assert len(report.failed_checks) == 0
    assert report.summary["warnings"] == 1


def test_all_passed_checks_produce_passed_report(
    db_session: Session,
    monkeypatch,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    monkeypatch.setattr(
        service,
        "_check_observability_signals",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.OBSERVABILITY_SIGNALS,
            "Observability signals forced to pass for passed-report coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_failure_controls",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failure controls forced to pass for passed-report coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_cash_position_equity_consistency",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.CASH_POSITION_EQUITY_CONSISTENCY,
            "Cash/equity consistency forced to pass for passed-report coverage.",
        ),
    )
    monkeypatch.setattr(
        service,
        "_check_data_freshness",
        lambda *, window_start, window_end: service._passed(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Data freshness forced to pass for passed-report coverage.",
        ),
    )

    report = service.verify(
        window_start=window_start,
        window_end=window_end,
    )

    assert report.status == RuntimeSoakStatus.PASSED
    assert report.summary["failed"] == 0
    assert report.summary["warnings"] == 0


def test_runtime_job_health_fails_when_expected_jobs_are_missing(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()

    check = service._check_runtime_job_health(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.check_name == RuntimeSoakCheckName.RUNTIME_JOB_HEALTH
    assert check.metadata["missing_jobs"] == [
        "market_ingestion_cycle",
        "feature_pipeline_cycle",
        "trading_cycle",
    ]


def test_runtime_job_health_passes_when_expected_jobs_complete_successfully(
    db_session: Session,
) -> None:
    _seed_healthy_runtime_window(db_session)
    service = _build_service(db_session)
    window_start, window_end = _window()

    check = service._check_runtime_job_health(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["missing_jobs"] == []
    assert check.metadata["missing_trading_steps"] == []


def test_runtime_job_health_warns_when_failed_job_recovers_later_in_window(
    db_session: Session,
) -> None:
    _seed_healthy_runtime_window(db_session)
    window_start, window_end = _window()
    failed_started_at = window_start + timedelta(minutes=2)
    _seed_runtime_job_run(
        db_session,
        job_name="feature_pipeline_cycle",
        status="failed",
        started_at=failed_started_at,
        completed_at=failed_started_at + timedelta(minutes=1),
        duration_ms=60_000,
        error_message="transient failure",
    )

    service = _build_service(db_session)
    check = service._check_runtime_job_health(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.WARNING
    assert check.metadata["recovered_failures"] == ["feature_pipeline_cycle"]


def test_stale_running_job_fails_stale_running_state_check(
    db_session: Session,
) -> None:
    _seed_healthy_runtime_window(db_session)
    window_start, window_end = _window()
    stale_started_at = window_start
    _seed_runtime_job_run(
        db_session,
        job_name="feature_pipeline_cycle",
        status="running",
        started_at=stale_started_at,
        completed_at=None,
        duration_ms=None,
    )

    service = _build_service(db_session, stale_after=timedelta(minutes=10))
    check = service._check_stale_running_state(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.check_name == RuntimeSoakCheckName.STALE_RUNNING_STATE
    assert check.metadata["stale_runtime_job_names"] == ["feature_pipeline_cycle"]


def test_data_freshness_passes_when_all_artifacts_are_recent(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    recent_created_at = window_end - timedelta(minutes=5)
    _seed_raw_bars_dataset(db_session, created_at=recent_created_at)
    _seed_feature_dataset(db_session, created_at=recent_created_at)
    _seed_completed_trading_manifest(db_session, created_at=recent_created_at)

    check = service._check_data_freshness(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["missing_artifacts"] == []
    assert check.metadata["stale_artifacts"] == []


def test_data_freshness_fails_when_raw_bars_dataset_is_missing(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    recent_created_at = window_end - timedelta(minutes=5)
    _seed_feature_dataset(db_session, created_at=recent_created_at)
    _seed_completed_trading_manifest(db_session, created_at=recent_created_at)

    check = service._check_data_freshness(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert "raw_bars_dataset" in check.metadata["missing_artifacts"]


def test_data_freshness_fails_when_feature_dataset_is_stale(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    _seed_raw_bars_dataset(db_session, created_at=window_end - timedelta(minutes=5))
    _seed_feature_dataset(db_session, created_at=window_end - timedelta(minutes=30))
    _seed_completed_trading_manifest(db_session, created_at=window_end - timedelta(minutes=5))

    check = service._check_data_freshness(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert "feature_dataset" in check.metadata["stale_artifacts"]


def test_data_freshness_fails_when_trading_manifest_is_stale(
    db_session: Session,
    monkeypatch,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    _seed_raw_bars_dataset(db_session, created_at=window_end - timedelta(minutes=5))
    _seed_feature_dataset(db_session, created_at=window_end - timedelta(minutes=5))
    stale_manifest = _seed_completed_trading_manifest(
        db_session,
        created_at=window_end - timedelta(minutes=30),
    )
    monkeypatch.setattr(
        service._repository,
        "get_latest_trading_manifest",
        lambda: stale_manifest,
    )

    check = service._check_data_freshness(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert "trading_manifest" in check.metadata["stale_artifacts"]


def test_order_reconciliation_passes_when_no_issues_exist(db_session: Session) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    _seed_broker_order(
        db_session,
        submitted_at=window_start + timedelta(minutes=5),
        updated_at=window_start + timedelta(minutes=6),
        status=OrderStatus.FILLED,
        filled_qty=Decimal("1"),
        raw_broker_payload={"status": "filled"},
    )

    check = service._check_order_reconciliation(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["stale_unreconciled_order_count"] == 0
    assert check.metadata["status_mismatch_order_count"] == 0


def test_order_reconciliation_warns_when_broker_status_is_missing(
    db_session: Session,
    monkeypatch,
) -> None:
    service = _build_service(db_session, stale_after=timedelta(minutes=15))
    window_start, window_end = _window()
    order = _seed_broker_order(
        db_session,
        submitted_at=window_end - timedelta(minutes=5),
        updated_at=window_end - timedelta(minutes=4),
        status=OrderStatus.SUBMITTED,
        raw_broker_payload=None,
    )
    monkeypatch.setattr(
        service._repository,
        "list_orders_missing_broker_status",
        lambda *, window_start, window_end: [order],
    )

    check = service._check_order_reconciliation(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.WARNING
    assert check.metadata["orders_missing_broker_status_count"] == 1


def test_order_reconciliation_fails_when_stale_unreconciled_orders_exist(
    db_session: Session,
) -> None:
    service = _build_service(db_session, stale_after=timedelta(minutes=10))
    window_start, window_end = _window()
    order = _seed_broker_order(
        db_session,
        submitted_at=window_start + timedelta(minutes=1),
        updated_at=window_start + timedelta(minutes=2),
        status=OrderStatus.SUBMITTED,
        raw_broker_payload={"status": "submitted"},
    )

    check = service._check_order_reconciliation(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.metadata["stale_unreconciled_order_count"] == 1
    assert (
        check.metadata["stale_unreconciled_orders"][0]["broker_order_id"] == order.broker_order_id
    )


def test_duplicate_fill_protection_passes_when_no_duplicates_exist(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    _seed_fill(
        db_session,
        timestamp=window_start + timedelta(minutes=5),
        broker_order_id="order-1",
        broker_fill_id="fill-1",
        execution_id="exec-1",
        idempotency_key="idem-1",
    )

    check = service._check_duplicate_fill_protection(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["duplicate_group_counts"]["broker_fill_id"] == 0


def test_duplicate_fill_protection_warns_when_detector_metadata_is_incomplete(
    db_session: Session,
    monkeypatch,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    monkeypatch.setattr(
        service._repository,
        "list_duplicate_fills_by_broker_fill_id",
        lambda *, window_start, window_end: [
            DuplicateFillGroup(group_key="broker-fill-1", fill_ids=[], count=2)
        ],
    )

    check = service._check_duplicate_fill_protection(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.WARNING
    assert check.metadata["incomplete_detectors"] == ["broker_fill_id"]


def test_duplicate_fill_protection_fails_when_duplicates_are_detected(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()
    timestamp = window_start + timedelta(minutes=5)
    _seed_fill(
        db_session,
        timestamp=timestamp,
        broker_order_id="order-dup",
        broker_fill_id="dup-fill",
        execution_id="exec-dup-a",
        idempotency_key="idem-dup-a",
    )
    _seed_fill(
        db_session,
        timestamp=timestamp + timedelta(seconds=1),
        broker_order_id="order-dup-b",
        broker_fill_id="dup-fill",
        execution_id="exec-dup-b",
        idempotency_key="idem-dup-b",
    )

    check = service._check_duplicate_fill_protection(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.severity == RuntimeSoakSeverity.CRITICAL
    assert check.metadata["duplicate_group_counts"]["broker_fill_id"] == 1


def test_cash_position_equity_consistency_passes_when_within_tolerance(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, _window_end = _window()
    timestamp = window_start + timedelta(minutes=10)
    _seed_cash_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.LEDGER,
        cash=Decimal("1000.00"),
        equity=Decimal("1500.00"),
    )
    _seed_cash_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.BROKER_RECONCILED,
        cash=Decimal("1000.50"),
        equity=Decimal("1503.00"),
    )
    _seed_position_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.LEDGER,
        positions={"AAPL": Decimal("5")},
    )
    _seed_position_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.BROKER_RECONCILED,
        positions={"AAPL": Decimal("5.000000")},
    )

    check = service._check_cash_position_equity_consistency(
        window_start=timestamp,
        window_end=timestamp,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["cash_drift"] == "0.500000"


def test_cash_position_equity_consistency_warns_when_snapshots_are_missing(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    timestamp = _window()[0] + timedelta(minutes=10)
    _seed_cash_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.LEDGER,
        cash=Decimal("1000.00"),
        equity=Decimal("1500.00"),
    )

    check = service._check_cash_position_equity_consistency(
        window_start=timestamp,
        window_end=timestamp,
    )

    assert check.status == RuntimeSoakStatus.WARNING
    assert "latest_broker_cash_snapshot" in check.metadata["missing_artifacts"]


def test_cash_position_equity_consistency_fails_when_drift_exceeds_tolerance(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    timestamp = _window()[0] + timedelta(minutes=10)
    _seed_cash_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.LEDGER,
        cash=Decimal("1000.00"),
        equity=Decimal("1500.00"),
    )
    _seed_cash_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.BROKER_RECONCILED,
        cash=Decimal("1005.50"),
        equity=Decimal("1515.00"),
    )
    _seed_position_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.LEDGER,
        positions={"AAPL": Decimal("5")},
    )
    _seed_position_snapshot(
        db_session,
        timestamp=timestamp,
        source=OrderSource.BROKER_RECONCILED,
        positions={"AAPL": Decimal("7")},
    )

    check = service._check_cash_position_equity_consistency(
        window_start=timestamp,
        window_end=timestamp,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.metadata["exceeded_position_symbols"] == ["AAPL"]


def test_failure_controls_pass_when_no_failed_runtime_jobs_exist(
    db_session: Session,
) -> None:
    service = _build_service(db_session)
    window_start, window_end = _window()

    check = service._check_failure_controls(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["failed_runtime_job_count"] == 0


def test_failure_controls_warn_when_artifacts_exist_but_freeze_is_not_persisted(
    db_session: Session,
) -> None:
    window_start, window_end = _window()
    failed_at = window_start + timedelta(minutes=5)
    _seed_runtime_job_run(
        db_session,
        job_name="trading_cycle",
        status="failed",
        started_at=failed_at,
        completed_at=failed_at + timedelta(minutes=1),
        duration_ms=60_000,
        error_message="fatal error",
    )
    _seed_completed_trading_manifest(
        db_session,
        created_at=failed_at + timedelta(minutes=1),
        last_successful_step="order_reconciliation",
        status="failed",
    )
    _seed_failure_audit_event(db_session, event_timestamp=failed_at + timedelta(minutes=1))
    _seed_runtime_control_state(
        db_session,
        trading_enabled=False,
        trading_paused=True,
        reason="runtime failure",
    )

    service = _build_service(db_session)
    check = service._check_failure_controls(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.WARNING
    assert check.metadata["trading_freeze_persisted"] is False


def test_failure_controls_fail_when_failure_artifacts_are_missing(
    db_session: Session,
) -> None:
    window_start, window_end = _window()
    failed_at = window_start + timedelta(minutes=5)
    _seed_runtime_job_run(
        db_session,
        job_name="trading_cycle",
        status="failed",
        started_at=failed_at,
        completed_at=failed_at + timedelta(minutes=1),
        duration_ms=60_000,
        error_message="fatal error",
    )

    service = _build_service(db_session)
    check = service._check_failure_controls(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert "failed_manifest" in check.metadata["missing_failure_control_artifacts"]


def test_concurrent_running_jobs_passes_when_no_duplicates_exist(
    db_session: Session,
) -> None:
    window_start, window_end = _window()
    _seed_runtime_job_run(
        db_session,
        job_name="market_ingestion_cycle",
        status="running",
        started_at=window_start + timedelta(minutes=1),
    )

    service = _build_service(db_session)
    check = service._check_concurrent_running_jobs(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.PASSED
    assert check.metadata["concurrent_job_names"] == []


def test_concurrent_running_jobs_fails_when_same_job_has_two_running_records(
    db_session: Session,
) -> None:
    """Two RUNNING records for the same job name indicate lock protection failed."""
    from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
        RuntimeSoakSeverity,
    )

    window_start, window_end = _window()
    _seed_runtime_job_run(
        db_session,
        job_name="trading_cycle",
        status="running",
        started_at=window_start + timedelta(minutes=1),
    )
    _seed_runtime_job_run(
        db_session,
        job_name="trading_cycle",
        status="running",
        started_at=window_start + timedelta(minutes=2),
    )

    service = _build_service(db_session)
    check = service._check_concurrent_running_jobs(
        window_start=window_start,
        window_end=window_end,
    )

    assert check.status == RuntimeSoakStatus.FAILED
    assert check.severity == RuntimeSoakSeverity.CRITICAL
    assert "trading_cycle" in check.metadata["concurrent_job_names"]
