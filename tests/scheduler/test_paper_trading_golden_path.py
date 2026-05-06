from decimal import Decimal

import autonomous_trading_platform.scheduler.cycles.run_trading_cycle as cycle_module
from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
    PaperTradingGoldenPathOrchestrator,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


def _latest_trading_manifest(db_session, fixture):
    return (
        db_session.query(RunManifestRow)
        .filter(RunManifestRow.run_type == "paper")
        .filter(RunManifestRow.dataset_version == fixture.dataset_version)
        .filter(RunManifestRow.broker_account_id == fixture.account_id)
        .filter(RunManifestRow.status.isnot(None))
        .order_by(RunManifestRow.created_at.desc())
        .first()
    )


def _order_intents_for_run(db_session, run_id):
    return db_session.query(OrderIntents).filter(OrderIntents.run_id == run_id).all()


def _fills_for_run(db_session, run_id):
    return db_session.query(Fill).filter(Fill.run_id == run_id).all()


def _cash_snapshots_for_run(db_session, run_id):
    return db_session.query(CashSnapshot).filter(CashSnapshot.run_id == run_id).all()


def _position_items_for_run(db_session, run_id):
    return (
        db_session.query(PositionSnapshotItem)
        .join(PositionSnapshot)
        .filter(PositionSnapshot.run_id == run_id)
        .all()
    )


def _runtime_job_runs_for_run(db_session, correlation_id):
    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.correlation_id == correlation_id)
        .all()
    )


def _latest_ingestion_run(db_session):
    return db_session.query(IngestionRuns).order_by(IngestionRuns.created_at.desc()).first()


def _latest_dataset_version(db_session):
    return (
        db_session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == "raw_bars")
        .order_by(DatasetVersions.created_at.desc())
        .first()
    )


def _latest_corporate_action_dataset_version(db_session):
    return (
        db_session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == "corporate_actions")
        .order_by(DatasetVersions.created_at.desc())
        .first()
    )


def _runtime_job_runs_by_name(db_session, job_name):
    return (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.job_name == job_name)
        .order_by(RuntimeJobRuns.started_at.desc())
        .all()
    )


def test_paper_trading_golden_path_runs_trading_runtime_chain(
    seeded_paper_trading_golden_path_fixture,
    db_session,
    monkeypatch,
):
    fixture = seeded_paper_trading_golden_path_fixture

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

    result = orchestrator.run(
        now_utc=fixture.now_utc,
    )

    # -------------------------------------------------
    # 1. INGESTION COMPLETED
    # -------------------------------------------------
    ingestion_run = _latest_ingestion_run(db_session)
    dataset_version = _latest_dataset_version(db_session)

    assert ingestion_run is not None
    assert ingestion_run.status == "completed"
    assert ingestion_run.error_message is None

    assert dataset_version is not None
    assert dataset_version.dataset_name == "raw_bars"
    assert dataset_version.validation_status == "validated"

    # -------------------------------------------------
    # 2. TRADING MANIFEST COMPLETED
    # -------------------------------------------------
    manifests = (
        db_session.query(RunManifestRow)
        .filter(RunManifestRow.run_type == "paper")
        .order_by(RunManifestRow.created_at.desc())
        .all()
    )

    for row in manifests:
        print(
            "MANIFEST:",
            row.run_id,
            row.status,
            row.dataset_version,
            row.broker_account_id,
            row.current_step,
            row.last_successful_step,
            row.error_message,
        )

    manifest = next(
        (row for row in manifests if row.status == "completed"),
        None,
    )

    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert manifest.current_step is None
    assert manifest.last_successful_step == "risk_snapshot"
    assert manifest.dataset_version == dataset_version.dataset_version_id
    assert manifest.broker_account_id == fixture.account_id

    # -------------------------------------------------
    # 3. RUNTIME JOB CHAIN EXISTS
    # -------------------------------------------------
    ingestion_jobs = _runtime_job_runs_by_name(db_session, "market_ingestion_cycle")
    feature_jobs = _runtime_job_runs_by_name(db_session, "feature_pipeline_cycle")
    trading_jobs = _runtime_job_runs_by_name(db_session, "trading_cycle")
    corporate_action_jobs = _runtime_job_runs_by_name(
        db_session,
        "corporate_action_ingestion_cycle",
    )
    golden_path_jobs = _runtime_job_runs_by_name(db_session, "paper_trading_golden_path")

    assert ingestion_jobs != []
    assert feature_jobs != []
    assert trading_jobs != []
    assert corporate_action_jobs != []
    assert golden_path_jobs != []

    assert ingestion_jobs[0].status == "completed"
    assert feature_jobs[0].status == "completed"
    assert trading_jobs[0].status == "completed"
    assert corporate_action_jobs[0].status == "completed"
    assert corporate_action_jobs[0].output_summary_json is not None
    assert (
        corporate_action_jobs[0].output_summary_json["source_raw_bars_dataset_version_id"]
        == dataset_version.dataset_version_id
    )
    assert golden_path_jobs[0].status == "completed"

    runtime_chain_job_names = {
        golden_path_jobs[0].job_name,
        ingestion_jobs[0].job_name,
        feature_jobs[0].job_name,
        trading_jobs[0].job_name,
        corporate_action_jobs[0].job_name,
    }

    assert runtime_chain_job_names == {
        "paper_trading_golden_path",
        "market_ingestion_cycle",
        "feature_pipeline_cycle",
        "trading_cycle",
        "corporate_action_ingestion_cycle",
    }

    corporate_action_dataset = _latest_corporate_action_dataset_version(db_session)

    assert corporate_action_dataset is not None
    assert corporate_action_dataset.dataset_name == "corporate_actions"
    assert corporate_action_dataset.validation_status == "validated"
    assert corporate_action_dataset.source_manifest is not None
    assert corporate_action_dataset.source_manifest["pipeline"] == "corporate_actions_ingestion"

    # -------------------------------------------------
    # 4. DETAILED TRADING OUTPUTS
    # -------------------------------------------------
    order_intents = _order_intents_for_run(db_session, manifest.run_id)
    fills = _fills_for_run(db_session, manifest.run_id)
    cash_snapshots = _cash_snapshots_for_run(db_session, manifest.run_id)
    position_items = _position_items_for_run(db_session, manifest.run_id)

    assert order_intents != []
    assert fills != []
    assert cash_snapshots != []
    assert position_items != []

    for intent in order_intents:
        assert intent.run_id == manifest.run_id
        assert intent.symbol == fixture.symbol
        assert intent.qty > Decimal("0")

    for fill in fills:
        assert fill.run_id == manifest.run_id
        assert fill.symbol == fixture.symbol
        assert fill.quantity > Decimal("0")
        assert fill.price > Decimal("0")

    latest_cash = max(cash_snapshots, key=lambda snapshot: snapshot.timestamp)
    symbol_positions = [item for item in position_items if item.symbol == fixture.symbol]

    assert symbol_positions != []

    latest_position = symbol_positions[-1]

    assert latest_cash.run_id == manifest.run_id
    assert latest_cash.cash < fixture.starting_cash
    assert latest_cash.equity > Decimal("0")
    assert latest_cash.buying_power <= fixture.starting_cash

    assert latest_position.quantity > fixture.starting_position
    assert latest_position.market_price > Decimal("0")
    assert latest_position.market_value > Decimal("0")

    # -------------------------------------------------
    # 5. AUDIT EVENTS
    # -------------------------------------------------
    assert recorded_events != []
    assert any(event[0] == "started" for event in recorded_events)
    assert any(event[0] == "completed" for event in recorded_events)
    assert not any(event[0] == "failed" for event in recorded_events)

    completed_event = next(event for event in recorded_events if event[0] == "completed")
    completed_metadata = completed_event[1]["metadata"]

    assert completed_metadata is not None
    assert isinstance(completed_metadata, dict)
    assert len(completed_metadata) > 0

    # -------------------------------------------------
    # 6. ORCHESTRATOR JOB ASSERTIONS
    # -------------------------------------------------
    orchestrator_job_runs = _runtime_job_runs_for_run(
        db_session,
        correlation_id=result.correlation_id,
    )

    assert orchestrator_job_runs != []
    assert len(orchestrator_job_runs) == 1

    orchestrator_job = orchestrator_job_runs[0]

    assert orchestrator_job.job_name == "paper_trading_golden_path"
    assert orchestrator_job.status == "completed"

    assert orchestrator_job.input_summary_json["mode"] == "full_pipeline"
    assert orchestrator_job.input_summary_json["steps"] == [
        "market_ingestion_cycle",
        "feature_pipeline_cycle",
        "trading_cycle",
        "corporate_action_ingestion_cycle",
    ]
