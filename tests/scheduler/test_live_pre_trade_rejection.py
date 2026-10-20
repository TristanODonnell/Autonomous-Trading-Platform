"""Soak findings 9 and 11: the live submission path enforces the dollar limits, and a
broker refusal rejects that one intent instead of failing the tick."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import autonomous_trading_platform.execution.contexts.build_execution_context as execution_builder
from autonomous_trading_platform.contracts.common.enums import OrderStatus, Side
from autonomous_trading_platform.execution.errors import BrokerOrderRejectedError
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.tracked_orders import TrackedOrder
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from tests.utilities.paper_trading_cycle_fixture import FakePaperBrokerClient


def _latest_manifest(db_session) -> RunManifestRow:
    db_session.expire_all()
    manifest: RunManifestRow | None = (
        db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()
    )
    assert manifest is not None
    return manifest


def _trading_cycle_job(db_session) -> RuntimeJobRuns:
    job: RuntimeJobRuns | None = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "trading_cycle")
        .order_by(RuntimeJobRuns.started_at.desc())
        .first()
    )
    assert job is not None
    return job


def _events(db_session, run_id, event_type: str) -> list[AuditLogRow]:
    rows: list[AuditLogRow] = (
        db_session.query(AuditLogRow)
        .filter(AuditLogRow.run_id == str(run_id))
        .filter(AuditLogRow.event_type == event_type)
        .all()
    )
    return rows


def _assert_tick_completed(db_session) -> RunManifestRow:
    manifest = _latest_manifest(db_session)
    assert manifest.status == "completed"
    job = _trading_cycle_job(db_session)
    assert job.status == "completed"
    assert job.error_message is None
    return manifest


def _assert_tick_completed_with_one_rejected_intent(db_session) -> tuple[RunManifestRow, Any]:
    manifest = _latest_manifest(db_session)
    assert manifest.status == "completed"
    job = _trading_cycle_job(db_session)
    assert job.status == "completed"
    assert job.error_message is None

    intents = db_session.query(OrderIntents).filter(OrderIntents.run_id == manifest.run_id).all()
    assert len(intents) == 1
    assert db_session.query(Fill).count() == 0

    tracked = db_session.query(TrackedOrder).filter(TrackedOrder.run_id == manifest.run_id).all()
    assert [row.current_status for row in tracked] == [OrderStatus.REJECTED]
    assert tracked[0].is_open is False
    return manifest, intents[0]


def test_gross_exposure_cap_drops_the_intent_at_evaluation_and_the_tick_completes(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    # Soak day 1: the live reader answered 0 for gross exposure, so MAX_GROSS_EXPOSURE
    # never bound and the paper account bought $312k of stock. With real exposure a
    # breach at evaluation used to degrade the whole tick; now that intent is dropped
    # and audited and the tick completes.
    monkeypatch.setenv("MAX_GROSS_EXPOSURE", "1")

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    manifest = _assert_tick_completed(db_session)
    assert (
        db_session.query(OrderIntents).filter(OrderIntents.run_id == manifest.run_id).count() == 0
    )
    assert db_session.query(Fill).count() == 0
    events = _events(db_session, manifest.run_id, "ORDER_REJECTED_BY_RISK_LIMIT")
    assert len(events) == 1
    assert events[0].event_metadata["symbol"] == seeded_paper_trading_cycle_fixture.symbol
    assert events[0].event_metadata["stage"] == "evaluation"
    assert events[0].event_metadata["severity"] == "warning"
    assert "gross exposure" in events[0].event_metadata["error"]
    assert _events(db_session, manifest.run_id, "ORDER_REJECTED_BY_THROTTLE") == []
    assert _events(db_session, manifest.run_id, "RUN_FAILED") == []


def test_second_buy_in_a_bar_is_checked_against_the_first_at_submission(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    # Portfolio mode: two strategies each buy $45k of SPY in one bar. Evaluation checks
    # each against the opening state (gross 0), so both pass a $60k cap; at submission
    # the first order is counted and the second is over the cap.
    monkeypatch.setenv("MAX_GROSS_EXPOSURE", "60000")
    monkeypatch.setenv("MAX_SYMBOL_EXPOSURE", "10000000")
    OperatorSettingsRepository(db_session).update_current(
        {
            "portfolio_mode_enabled": True,
            "min_active_strategies": 1,
            "max_active_strategies": 6,
            "per_strategy_cap": 1.0,
            "max_total_strategy_allocation_pct": 0.9,
        },
        updated_by="test",
    )

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    manifest = _assert_tick_completed(db_session)
    assert manifest.strategy_id == "portfolio"
    intents = db_session.query(OrderIntents).filter(OrderIntents.run_id == manifest.run_id).all()
    assert len(intents) == 2

    tracked = db_session.query(TrackedOrder).filter(TrackedOrder.run_id == manifest.run_id).all()
    statuses = [row.current_status for row in tracked]
    assert statuses.count(OrderStatus.REJECTED) == 1
    assert db_session.query(Fill).count() == 1

    events = _events(db_session, manifest.run_id, "ORDER_REJECTED_BY_RISK_LIMIT")
    assert len(events) == 1
    assert "gross exposure" in events[0].event_metadata["error"]
    assert "stage" not in events[0].event_metadata  # rejected at submission, not evaluation


def test_daily_notional_cap_counts_fills_already_booked_today(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    # $100k already traded today (fixture cap is $100k): the next buy is over the cap.
    now_utc = seeded_paper_trading_cycle_fixture.now_utc
    db_session.add(
        Fill(
            fill_id="fill-earlier-today",
            broker_order_id="paper-000000",
            intent_id=uuid4(),
            run_id=uuid4(),
            timestamp=now_utc - timedelta(hours=2),
            symbol="AAPL",
            side=Side.BUY,
            quantity=Decimal("1000"),
            price=Decimal("100"),
        )
    )
    db_session.commit()

    run_trading_cycle(now_utc=now_utc)

    manifest = _assert_tick_completed(db_session)
    assert (
        db_session.query(OrderIntents).filter(OrderIntents.run_id == manifest.run_id).count() == 0
    )
    events = _events(db_session, manifest.run_id, "ORDER_REJECTED_BY_RISK_LIMIT")
    assert len(events) == 1
    assert "daily notional" in events[0].event_metadata["error"]
    # Only the seeded fill exists: nothing reached the broker.
    assert db_session.query(Fill).count() == 1


class _RefusingBrokerClient(FakePaperBrokerClient):
    """Alpaca answering 403 on POST /v2/orders (buying power exhausted on soak day 1)."""

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise BrokerOrderRejectedError(
            f"Broker rejected order for {payload.get('symbol')}: HTTP 403",
            status_code=403,
            body='{"code":40310000,"message":"insufficient buying power"}',
            symbol=str(payload.get("symbol")),
        )


def test_broker_refusal_rejects_the_intent_and_the_tick_completes(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    monkeypatch.setattr(execution_builder, "AlpacaBrokerClient", _RefusingBrokerClient)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    manifest, intent = _assert_tick_completed_with_one_rejected_intent(db_session)
    events = _events(db_session, manifest.run_id, "ORDER_REJECTED_BY_BROKER")
    assert len(events) == 1
    metadata = events[0].event_metadata
    assert metadata["symbol"] == intent.symbol
    assert metadata["status_code"] == 403
    assert "insufficient buying power" in metadata["broker_response"]
    assert _events(db_session, manifest.run_id, "RUN_FAILED") == []
