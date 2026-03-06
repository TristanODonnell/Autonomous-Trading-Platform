from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow


def insert_run_manifest(session: Session, manifest: RunManifest) -> None:
    """Insert a run manifest into the database."""

    row = RunManifestRow(
        run_id=manifest.run_id,
        run_type=manifest.run_type,
        environment=manifest.environment,
        broker=manifest.broker,
        broker_account_id=manifest.broker_account_id,
        strategy_id=manifest.strategy_id,
        strategy_version=manifest.strategy_version,
        strategy_config=manifest.strategy_config,
        capital_bucket=manifest.capital_bucket,
        interval=manifest.interval,
        start_date=manifest.start_date,
        end_date=manifest.end_date,
        dataset_version=manifest.dataset_version,
        universe_version=manifest.universe_version,
        cost_model=manifest.cost_model,
        fill_model=manifest.fill_model,
        random_seed=manifest.random_seed,
        git_commit=manifest.git_commit,
        docker_image=manifest.docker_image,
        python_version=manifest.python_version,
        dependency_lock_hash=manifest.dependency_lock_hash,
        notes=manifest.notes,
    )

    session.add(row)
    session.commit()
