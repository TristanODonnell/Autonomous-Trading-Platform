from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow


def test_run_trading_cycle_directly_without_scheduler(
    seeded_paper_trading_cycle_fixture,
):
    fixture = seeded_paper_trading_cycle_fixture

    run_trading_cycle(now_utc=fixture.now_utc)

    assert True


def test_trading_evaluation_runs(
    seeded_paper_trading_cycle_fixture,
    db_session,
):
    fixture = seeded_paper_trading_cycle_fixture

    run_trading_cycle(now_utc=fixture.now_utc)

    manifest = db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()

    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert manifest.last_successful_step == "risk_snapshot"
