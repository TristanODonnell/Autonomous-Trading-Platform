"""A throttle rejection skips that intent; it must not fail the tick (soak Finding 8)."""

from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import OrderStatus
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


def _latest_manifest(db_session) -> RunManifestRow:
    db_session.expire_all()
    manifest: RunManifestRow | None = (
        db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()
    )
    assert manifest is not None
    return manifest


def _latest_trading_cycle_job(db_session) -> RuntimeJobRuns:
    job: RuntimeJobRuns | None = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "trading_cycle")
        .order_by(RuntimeJobRuns.started_at.desc())
        .first()
    )
    assert job is not None
    return job


def _throttle_events(db_session, run_id) -> list[AuditLogRow]:
    rows: list[AuditLogRow] = (
        db_session.query(AuditLogRow)
        .filter(AuditLogRow.run_id == str(run_id))
        .filter(AuditLogRow.event_type == "ORDER_REJECTED_BY_THROTTLE")
        .all()
    )
    return rows


def test_throttle_rejection_skips_the_intent_and_the_tick_completes(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    # Zero orders per bar: the one intent is rejected before it reaches the broker.
    monkeypatch.setenv("MAX_ORDERS_PER_BAR", "0")

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    manifest = _latest_manifest(db_session)
    assert manifest.status == "completed"
    job = _latest_trading_cycle_job(db_session)
    assert job.status == "completed"
    assert job.error_message is None

    intents = db_session.query(OrderIntents).filter(OrderIntents.run_id == manifest.run_id).all()
    assert len(intents) == 1
    assert db_session.query(Fill).count() == 0

    tracked = db_session.query(TrackedOrder).filter(TrackedOrder.run_id == manifest.run_id).all()
    assert [row.current_status for row in tracked] == [OrderStatus.REJECTED]
    assert tracked[0].is_open is False

    events = _throttle_events(db_session, manifest.run_id)
    assert len(events) == 1
    assert events[0].event_metadata["severity"] == "warning"
    assert events[0].event_metadata["symbol"] == intents[0].symbol
    assert "reached limit 0" in events[0].event_metadata["error"]


def test_other_intents_in_the_bar_still_submit_after_a_throttle_rejection(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    # Portfolio mode: two strategies each buy the same symbol in one bar. With one order
    # per bar the second intent is throttled; the first must still fill.
    monkeypatch.setenv("MAX_ORDERS_PER_BAR", "1")
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

    manifest = _latest_manifest(db_session)
    assert manifest.strategy_id == "portfolio"
    assert manifest.status == "completed"
    assert _latest_trading_cycle_job(db_session).status == "completed"

    intents = db_session.query(OrderIntents).filter(OrderIntents.run_id == manifest.run_id).all()
    assert len(intents) == 2

    tracked = db_session.query(TrackedOrder).filter(TrackedOrder.run_id == manifest.run_id).all()
    statuses = [row.current_status for row in tracked]
    assert len(statuses) == 2
    assert statuses.count(OrderStatus.REJECTED) == 1

    assert db_session.query(Fill).count() == 1
    assert len(_throttle_events(db_session, manifest.run_id)) == 1
