from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import autonomous_trading_platform.execution.clients.alpaca_broker_client as alpaca_client_module
import autonomous_trading_platform.scheduler.common.trading_cycle_common as trading_common
import autonomous_trading_platform.scheduler.cycles.run_trading_cycle as cycle_module
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import OrderStatus, OrderType
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.services.broker_runtime_sync_service import (
    BrokerRuntimeSyncService,
)
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_account_snapshots import (
    BrokerAccountSnapshot,
)
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.tracked_orders import TrackedOrder
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.strategy.contexts import (
    build_strategy_runtime_context as strategy_builder,
)
from tests.conftest import auth_headers
from tests.utilities import paper_trading_cycle_fixture as paper_fixture

pytestmark = [
    pytest.mark.external,
    pytest.mark.alpaca,
    pytest.mark.paper_runtime,
]

TERMINAL_BROKER_STATUSES = {
    "canceled",
    "expired",
    "filled",
    "rejected",
}


@dataclass(frozen=True)
class ExternalPaperRuntimeFixture:
    now_utc: Any
    symbol: str
    settings: Settings


def _seed_external_paper_runtime_fixture(
    *,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> ExternalPaperRuntimeFixture:
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.setenv("NO_LIVE_TRADING", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "false")
    monkeypatch.setenv("SHADOW_MODE_ENABLED", "false")
    monkeypatch.setenv("PAPER_ALLOWED_ACCOUNT_IDS", paper_fixture.PAPER_CYCLE_ACCOUNT_ID)
    monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "true")
    monkeypatch.setenv("SKIP_EVALUATION_ON_INGESTION_FAILURE", "true")
    monkeypatch.setenv("HOLD_POSITIONS_ON_EVALUATION_FAILURE", "true")
    monkeypatch.setenv("FREEZE_TRADING_ON_RECONCILIATION_FAILURE", "false")
    monkeypatch.setenv("PAPER_RUNTIME_ORDER_SAFEGUARDS_ENABLED", "true")
    monkeypatch.setenv("PAPER_RUNTIME_MAX_ORDER_QTY", "1")
    monkeypatch.setenv("PAPER_RUNTIME_MAX_ORDER_NOTIONAL", "1.00")
    monkeypatch.setenv("PAPER_RUNTIME_MAX_LIMIT_PRICE", "1.00")
    monkeypatch.setenv("PAPER_RUNTIME_ALLOWED_SYMBOLS", paper_fixture.PAPER_CYCLE_SYMBOL)
    monkeypatch.setenv("PAPER_RUNTIME_REQUIRE_LIMIT_ORDERS", "true")

    paper_fixture._write_adjusted_spy_bars(data_root=tmp_path)
    paper_fixture._write_spy_feature_dataset(data_root=tmp_path)
    paper_fixture._seed_database(session=db_session, data_root=tmp_path)
    paper_fixture._patch_strategy_evaluation(monkeypatch)

    monkeypatch.setattr(trading_common, "get_session", lambda: db_session)
    monkeypatch.setattr(
        strategy_builder.SqlAlchemyUniverseMembershipReader,
        "get_symbols_for_timestamp",
        lambda _self, _as_of: [paper_fixture.PAPER_CYCLE_SYMBOL],
    )

    monkeypatch.setattr(
        alpaca_client_module.AlpacaBrokerClient,
        "get_latest_trades",
        lambda _self, symbols: {
            symbol: {
                "p": 100.0,
                "s": 100,
                "t": paper_fixture.PAPER_CYCLE_NOW_UTC.isoformat(),
            }
            for symbol in symbols
        },
    )

    original_generate = PortfolioConstructionService.generate_order_intents

    def generate_tiny_limit_intents(self, *args, **kwargs):
        for intent in original_generate(self, *args, **kwargs):
            metadata = dict(intent.metadata or {})
            metadata["paper_runtime_validation"] = {
                "story": "STORY-79A",
                "safeguard": "tiny_non_marketable_limit_order",
            }
            yield intent.model_copy(
                update={
                    "qty": Decimal("1"),
                    "notional": None,
                    "order_type": OrderType.LIMIT,
                    "limit_price": Decimal("0.01"),
                    "metadata": metadata,
                }
            )

    monkeypatch.setattr(
        PortfolioConstructionService,
        "generate_order_intents",
        generate_tiny_limit_intents,
    )

    def reconcile_with_real_broker_status(*, run_id: str, now_utc=None) -> None:
        run_uuid = UUID(run_id)
        sync_service = BrokerRuntimeSyncService(
            session=db_session,
            broker_client=AlpacaBrokerClient(settings),
            settings=settings,
        )
        account_sync = sync_service.sync_broker_runtime_state(
            run_id=run_uuid,
            observed_at=now_utc,
        )

        reconciliation_deps = trading_common.build_trading_cycle_dependencies()
        with SorUnitOfWork(db_session) as uow:
            for row in uow.tracked_orders.list_reconcilable_orders():
                if row.run_id == run_uuid:
                    row.account_id = account_sync.broker_account_id
                    uow.tracked_orders.upsert(row)
            reconciliation_inputs = reconciliation_deps.execution_context.order_runtime_state_service.list_reconciliation_inputs(
                uow=uow
            )

        fill_count = 0
        reconciled_order_count = 0
        for tracked_order in reconciliation_inputs:
            if tracked_order.run_id != run_uuid:
                continue
            reconciled_order_count += 1

            result = (
                reconciliation_deps.execution_context.order_reconciliation_service.reconcile_order(
                    tracked_order,
                    now=now_utc,
                )
            )

            with SorUnitOfWork(db_session) as uow:
                uow.broker_orders.upsert(
                    sync_service.broker_order_mapper.to_orm_row(result.broker_order)
                )

                if result.fill is not None:
                    uow.fills.upsert(sync_service.broker_order_mapper.to_fill_orm_row(result.fill))
                    fill_count += 1

                (
                    reconciliation_deps.execution_context.order_runtime_state_service.apply_reconciliation_result(
                        uow=uow,
                        result=result,
                        now_utc=now_utc,
                    )
                )

        AuditLoggingService(db_session).record_event(
            run_id=run_id,
            event_type="RECONCILIATION_COMPLETED",
            component="external.paper_runtime_cycle",
            message="External paper runtime reconciliation completed",
            metadata={
                "broker_order_count": reconciled_order_count,
                "fill_count": fill_count,
                "severity": "info",
            },
        )

    monkeypatch.setattr(
        cycle_module,
        "run_order_reconciliation_job",
        reconcile_with_real_broker_status,
    )

    return ExternalPaperRuntimeFixture(
        now_utc=paper_fixture.PAPER_CYCLE_NOW_UTC,
        symbol=paper_fixture.PAPER_CYCLE_SYMBOL,
        settings=settings,
    )


def _latest_completed_manifest(db_session: Session) -> RunManifestRow:
    manifest = (
        db_session.query(RunManifestRow)
        .filter(RunManifestRow.run_type == "paper")
        .filter(RunManifestRow.status == "completed")
        .order_by(RunManifestRow.created_at.desc())
        .first()
    )
    assert manifest is not None
    return cast(RunManifestRow, manifest)


def _cancel_runtime_orders(
    *,
    db_session: Session,
    client: AlpacaBrokerClient,
) -> None:
    broker_orders = db_session.query(BrokerOrder).all()
    for broker_order in broker_orders:
        if broker_order.status == OrderStatus.FILLED:
            continue
        with suppress(Exception):
            fetched = client.get_order_by_id(broker_order.broker_order_id)
            if fetched.get("status") not in TERMINAL_BROKER_STATUSES:
                client.cancel_order(broker_order.broker_order_id)


def test_tiny_one_pass_paper_runtime_cycle_persists_and_surfaces_state(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alpaca_paper_settings: Settings,
    alpaca_paper_client: AlpacaBrokerClient,
) -> None:
    fixture = _seed_external_paper_runtime_fixture(
        db_session=db_session,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        settings=alpaca_paper_settings,
    )

    try:
        cycle_module.run_trading_cycle(now_utc=fixture.now_utc)

        manifest = _latest_completed_manifest(db_session)

        runtime_job = (
            db_session.query(RuntimeJobRuns)
            .filter(RuntimeJobRuns.job_name == "trading_cycle")
            .filter(RuntimeJobRuns.correlation_id == str(manifest.run_id))
            .one()
        )
        assert runtime_job.status == "completed"
        assert runtime_job.started_at is not None
        assert runtime_job.completed_at is not None
        assert runtime_job.duration_ms is not None
        assert runtime_job.input_summary_json["trading_environment"] == "paper"
        assert runtime_job.output_summary_json["last_successful_step"] == "risk_snapshot"

        account_snapshot = (
            db_session.query(BrokerAccountSnapshot)
            .filter(BrokerAccountSnapshot.trading_environment == "paper")
            .order_by(BrokerAccountSnapshot.observed_at.desc())
            .first()
        )
        assert account_snapshot is not None
        assert account_snapshot.broker == "alpaca"
        assert account_snapshot.broker_account_id

        cash_snapshot = (
            db_session.query(CashSnapshot)
            .filter(CashSnapshot.run_id == manifest.run_id)
            .order_by(CashSnapshot.timestamp.desc())
            .first()
        )
        assert cash_snapshot is not None
        assert cash_snapshot.equity == account_snapshot.equity

        order_intents = (
            db_session.query(OrderIntents).filter(OrderIntents.run_id == manifest.run_id).all()
        )
        assert len(order_intents) == 1
        assert order_intents[0].strategy_id == "baseline_strategy"
        assert order_intents[0].symbol == fixture.symbol
        assert order_intents[0].qty == Decimal("1")
        assert order_intents[0].order_type == OrderType.LIMIT
        assert order_intents[0].limit_price == Decimal("0.01")

        broker_orders = (
            db_session.query(BrokerOrder).filter(BrokerOrder.run_id == manifest.run_id).all()
        )
        assert len(broker_orders) == 1
        assert broker_orders[0].symbol == fixture.symbol
        assert broker_orders[0].qty == Decimal("1")
        assert broker_orders[0].limit_price == Decimal("0.01")
        assert broker_orders[0].broker_order_id
        assert broker_orders[0].status in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
        }

        tracked_order = (
            db_session.query(TrackedOrder).filter(TrackedOrder.run_id == manifest.run_id).one()
        )
        assert tracked_order.broker_order_id == broker_orders[0].broker_order_id
        assert tracked_order.current_status == broker_orders[0].status
        assert tracked_order.previous_filled_qty == broker_orders[0].filled_qty
        assert tracked_order.account_id == account_snapshot.broker_account_id

        fills = db_session.query(Fill).filter(Fill.run_id == manifest.run_id).all()
        if broker_orders[0].filled_qty == Decimal("0"):
            assert fills == []
        else:
            assert fills != []

        audit_events = (
            db_session.query(AuditLogRow).filter(AuditLogRow.run_id == str(manifest.run_id)).all()
        )
        event_types = {event.event_type for event in audit_events}
        assert {
            "RUN_STARTED",
            "BROKER_SYNC_COMPLETED",
            "ORDER_GENERATED",
            "order_transition",
            "RECONCILIATION_COMPLETED",
            "RUN_COMPLETED",
        }.issubset(event_types)

        headers = auth_headers()
        runtime_response = client.get("/api/v1/operations/runtime-state", headers=headers)
        activity_response = client.get("/api/v1/activity/recent?limit=20", headers=headers)
        summary_response = client.get("/api/v1/portfolio/summary", headers=headers)
        strategies_response = client.get("/api/v1/strategies/active", headers=headers)

        assert runtime_response.status_code == 200
        assert activity_response.status_code == 200
        assert summary_response.status_code == 200
        assert strategies_response.status_code == 200

        activity_items = activity_response.json()["data"]["items"]
        assert any(item["event_type"] == "RECONCILIATION_COMPLETED" for item in activity_items)
        assert any(item["event_type"] == "order_update" for item in activity_items)
    finally:
        _cancel_runtime_orders(db_session=db_session, client=alpaca_paper_client)
