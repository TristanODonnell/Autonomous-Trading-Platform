from decimal import Decimal

import autonomous_trading_platform.scheduler.cycles.run_trading_cycle as cycle_module
from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
    PaperTradingGoldenPathOrchestrator,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from tests.conftest import auth_headers

FORBIDDEN_PUBLIC_FIELDS = {
    "dataset_version",
    "dataset_version_id",
    "run_id",
    "experiment_id",
    "snapshot_id",
    "price_basis",
    "source_dataset_version",
    "source_manifest",
    "correlation_id",
    "broker_account_id",
}


def _assert_no_internal_fields(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            assert key not in FORBIDDEN_PUBLIC_FIELDS
            _assert_no_internal_fields(nested)

    elif isinstance(value, list):
        for item in value:
            _assert_no_internal_fields(item)


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


def _latest_position_items_for_run(db_session, run_id):
    return (
        db_session.query(PositionSnapshotItem)
        .join(PositionSnapshot)
        .filter(PositionSnapshot.run_id == run_id)
        .all()
    )


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

    def patched_builder():
        deps = original_builder()
        deps.audit_logger = FakeAuditLogger()
        return deps

    monkeypatch.setattr(
        cycle_module,
        "build_trading_cycle_dependencies",
        patched_builder,
    )

    orchestrator = PaperTradingGoldenPathOrchestrator(db_session)
    return orchestrator.run(now_utc=fixture.now_utc)


def test_dashboard_api_matches_paper_trading_runtime_state(
    client,
    db_session,
    seeded_paper_trading_golden_path_fixture,
    monkeypatch,
):
    fixture = seeded_paper_trading_golden_path_fixture

    result = _run_golden_path(
        db_session=db_session,
        fixture=fixture,
        monkeypatch=monkeypatch,
    )

    manifest = _latest_completed_paper_manifest(db_session)
    assert manifest is not None

    latest_cash = _latest_cash_snapshot_for_run(db_session, manifest.run_id)
    assert latest_cash is not None

    latest_positions = _latest_position_items_for_run(db_session, manifest.run_id)
    assert latest_positions != []

    fills = db_session.query(Fill).filter(Fill.run_id == manifest.run_id).all()
    assert fills != []

    runtime_jobs = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.correlation_id == result.correlation_id)
        .all()
    )
    assert runtime_jobs != []

    headers = auth_headers()

    summary_response = client.get("/api/v1/portfolio/summary", headers=headers)
    equity_response = client.get("/api/v1/portfolio/equity-curve?period=1w", headers=headers)
    strategies_response = client.get("/api/v1/strategies/active", headers=headers)
    activity_response = client.get("/api/v1/activity/recent?limit=20", headers=headers)
    health_response = client.get("/api/v1/system/health", headers=headers)

    assert summary_response.status_code == 200
    assert equity_response.status_code == 200
    assert strategies_response.status_code == 200
    assert activity_response.status_code == 200
    assert health_response.status_code == 200

    summary_data = summary_response.json()["data"]
    equity_data = equity_response.json()["data"]
    strategies_data = strategies_response.json()["data"]
    activity_data = activity_response.json()["data"]
    health_data = health_response.json()["data"]

    assert Decimal(str(summary_data["cash_balance"])) == latest_cash.cash
    assert Decimal(str(summary_data["current_portfolio_value"])) == latest_cash.equity

    assert equity_data["period"] == "1w"
    assert equity_data["points"] != []
    assert Decimal(str(equity_data["points"][-1]["value"])) == latest_cash.equity

    assert "strategies" in strategies_data
    assert isinstance(strategies_data["strategies"], list)

    assert "items" in activity_data
    assert isinstance(activity_data["items"], list)

    assert health_data is not None

    for body in [
        summary_response.json(),
        equity_response.json(),
        strategies_response.json(),
        activity_response.json(),
        health_response.json(),
    ]:
        _assert_no_internal_fields(body)
