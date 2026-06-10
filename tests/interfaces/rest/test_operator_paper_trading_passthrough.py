from __future__ import annotations

from decimal import Decimal

import autonomous_trading_platform.scheduler.cycles.run_trading_cycle as cycle_module
from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
    PaperTradingGoldenPathOrchestrator,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from tests.conftest import auth_headers


def _run_golden_path(db_session, fixture, monkeypatch):
    recorded_events = []

    class FakeAuditLogger:
        def record_run_started(self, **kwargs):
            recorded_events.append(("started", kwargs))

        def record_run_completed(self, **kwargs):
            recorded_events.append(("completed", kwargs))

        def record_run_failed(self, **kwargs):
            recorded_events.append(("failed", kwargs))

    from autonomous_trading_platform.scheduler.common import trading_cycle_common

    original_builder = trading_cycle_common.build_trading_cycle_dependencies

    def patched_builder(**kwargs):
        deps = original_builder(**kwargs)
        deps.audit_logger = FakeAuditLogger()
        return deps

    monkeypatch.setattr(
        cycle_module,
        "build_trading_cycle_dependencies",
        patched_builder,
    )

    orchestrator = PaperTradingGoldenPathOrchestrator(db_session)
    return orchestrator.run(now_utc=fixture.now_utc)


def _run_trading_cycle_only(db_session, fixture, monkeypatch):
    recorded_events = []

    class FakeAuditLogger:
        def record_run_started(self, **kwargs):
            recorded_events.append(("started", kwargs))

        def record_run_completed(self, **kwargs):
            recorded_events.append(("completed", kwargs))

        def record_run_failed(self, **kwargs):
            recorded_events.append(("failed", kwargs))

    from autonomous_trading_platform.scheduler.common import trading_cycle_common

    original_builder = trading_cycle_common.build_trading_cycle_dependencies

    def patched_builder(**kwargs):
        deps = original_builder(**kwargs)
        deps.audit_logger = FakeAuditLogger()
        return deps

    monkeypatch.setattr(
        cycle_module,
        "build_trading_cycle_dependencies",
        patched_builder,
    )

    before_job_ids = {
        row.job_run_id
        for row in db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "trading_cycle")
        .all()
    }

    cycle_module.run_trading_cycle(now_utc=fixture.now_utc)

    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == "trading_cycle")
        .filter(RuntimeJobRuns.job_run_id.notin_(before_job_ids))
        .order_by(RuntimeJobRuns.started_at.desc())
        .first()
    )


def _latest_completed_paper_manifest(db_session):
    return (
        db_session.query(RunManifestRow)
        .filter(RunManifestRow.run_type == "paper")
        .filter(RunManifestRow.status == "completed")
        .order_by(RunManifestRow.created_at.desc())
        .first()
    )


def _latest_cash_snapshot_for_run(db_session, run_id):
    return (
        db_session.query(CashSnapshot)
        .filter(CashSnapshot.run_id == run_id)
        .order_by(CashSnapshot.timestamp.desc())
        .first()
    )


def _broker_order_count(db_session) -> int:
    return int(db_session.query(BrokerOrder).count())


def test_operator_paper_trading_passthrough(
    client,
    db_session,
    seeded_paper_trading_golden_path_fixture,
    monkeypatch,
):
    fixture = seeded_paper_trading_golden_path_fixture
    operator_headers = auth_headers(role="operator")

    mode_response = client.put(
        "/api/v1/system/trading-mode",
        json={"mode": "paper", "rationale": "operator passthrough test"},
        headers=operator_headers,
    )
    assert mode_response.status_code in {200, 409}

    first_result = _run_golden_path(
        db_session=db_session,
        fixture=fixture,
        monkeypatch=monkeypatch,
    )

    first_manifest = _latest_completed_paper_manifest(db_session)
    assert first_manifest is not None
    target_strategy_id = first_manifest.strategy_id

    latest_cash = _latest_cash_snapshot_for_run(db_session, first_manifest.run_id)
    assert latest_cash is not None

    summary_response = client.get("/api/v1/portfolio/summary", headers=operator_headers)
    equity_response = client.get(
        "/api/v1/portfolio/equity-curve?period=1w",
        headers=operator_headers,
    )
    strategies_response = client.get("/api/v1/strategies/active", headers=operator_headers)
    activity_response = client.get(
        "/api/v1/activity/recent?limit=20",
        headers=operator_headers,
    )
    operations_response = client.get(
        "/api/v1/operations/jobs/trading_cycle/runs?limit=5",
        headers=operator_headers,
    )

    assert summary_response.status_code == 200
    assert equity_response.status_code == 200
    assert strategies_response.status_code == 200
    assert activity_response.status_code == 200
    assert operations_response.status_code == 200

    summary_data = summary_response.json()["data"]
    equity_data = equity_response.json()["data"]
    strategies_data = strategies_response.json()["data"]
    activity_data = activity_response.json()["data"]
    operations_data = operations_response.json()["data"]

    assert Decimal(str(summary_data["cash_balance"])) == latest_cash.cash
    assert Decimal(str(summary_data["current_portfolio_value"])) == latest_cash.equity
    assert equity_data["points"] != []
    assert Decimal(str(equity_data["points"][-1]["value"])) == latest_cash.equity
    assert strategies_data["strategies"] != []
    assert activity_data["items"] != []
    assert operations_data["runs"] != []

    before_disabled_order_count = _broker_order_count(db_session)

    disable_response = client.put(
        f"/api/v1/strategies/{target_strategy_id}/enabled",
        json={"enabled": False, "reason": "operator passthrough disable"},
        headers=operator_headers,
    )

    assert disable_response.status_code == 200
    disable_data = disable_response.json()["data"]
    assert disable_data["strategy_id"] == target_strategy_id
    assert disable_data["enabled"] is False

    control_state = db_session.get(StrategyControlState, target_strategy_id)
    assert control_state is not None
    assert control_state.enabled is False

    disabled_strategies_response = client.get(
        "/api/v1/strategies/active",
        headers=operator_headers,
    )
    assert disabled_strategies_response.status_code == 200
    disabled_strategies = {
        strategy["strategy_id"]: strategy
        for strategy in disabled_strategies_response.json()["data"]["strategies"]
    }
    assert disabled_strategies[target_strategy_id]["enabled"] is False

    second_job_run = _run_trading_cycle_only(
        db_session=db_session,
        fixture=fixture,
        monkeypatch=monkeypatch,
    )
    assert second_job_run is not None
    assert second_job_run.status == "completed"
    assert second_job_run.output_summary_json is not None
    assert second_job_run.output_summary_json["status"] == "skipped"
    assert second_job_run.output_summary_json["skip_reason"] == "strategy_disabled"

    after_disabled_order_count = _broker_order_count(db_session)
    assert after_disabled_order_count == before_disabled_order_count

    audit_response = client.get(
        "/api/v1/audit-log",
        params={"strategy_id": target_strategy_id},
        headers=operator_headers,
    )
    assert audit_response.status_code == 200
    audit_events = audit_response.json()["data"]["events"]
    disable_events = [
        event for event in audit_events if event["action_type"] == "STRATEGY_DISABLED"
    ]
    assert disable_events != []
    assert disable_events[0]["user"] == "test-user"
    assert disable_events[0]["rationale"] == "operator passthrough disable"

    operations_response = client.get(
        "/api/v1/operations/jobs/trading_cycle/runs?limit=10",
        headers=operator_headers,
    )
    assert operations_response.status_code == 200
    operations_runs = operations_response.json()["data"]["runs"]
    assert operations_runs != []
    matching_second_runs = [
        run for run in operations_runs if run["correlation_id"] == second_job_run.correlation_id
    ]
    assert matching_second_runs != []
    assert matching_second_runs[0]["status"] == "completed"
    assert matching_second_runs[0]["output_summary"]["status"] == "skipped"
    assert matching_second_runs[0]["output_summary"]["skip_reason"] == "strategy_disabled"

    runtime_job_runs = (
        db_session.query(RuntimeJobRuns)
        .filter(
            RuntimeJobRuns.correlation_id.in_(
                [first_result.correlation_id, second_job_run.correlation_id]
            )
        )
        .all()
    )
    assert runtime_job_runs != []

    audit_logs = (
        db_session.query(AuditLogRow).filter(AuditLogRow.event_type == "STRATEGY_DISABLED").all()
    )
    assert audit_logs != []
    assert audit_logs[0].event_metadata is not None
    assert audit_logs[0].event_metadata["actor"] == "test-user"
    assert audit_logs[0].event_metadata["reason"] == "operator passthrough disable"
