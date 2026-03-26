from uuid import UUID

from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class RunManifestRepository(BaseRepository):
    def add(self, manifest: RunManifest) -> RunManifestRow:
        row = RunManifestRow(
            run_id=manifest.run_id,
            run_type=manifest.run_type,
            created_at=manifest.created_at,
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
            bar_timestamp=manifest.bar_timestamp,
            status=manifest.status,
            current_step=manifest.current_step,
            last_successful_step=manifest.last_successful_step,
            error_message=manifest.error_message,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_run_id(self, run_id: UUID) -> RunManifestRow | None:
        result: RunManifestRow | None = (
            self.session.query(RunManifestRow).filter(RunManifestRow.run_id == run_id).one_or_none()
        )
        return result

    @staticmethod
    def to_contract(row: RunManifestRow) -> RunManifest:
        return RunManifest(
            run_id=row.run_id,
            run_type=row.run_type,
            created_at=row.created_at,
            environment=row.environment,
            broker=row.broker,
            broker_account_id=row.broker_account_id,
            strategy_id=row.strategy_id,
            strategy_version=row.strategy_version,
            strategy_config=row.strategy_config,
            capital_bucket=row.capital_bucket,
            interval=row.interval,
            start_date=row.start_date,
            end_date=row.end_date,
            dataset_version=row.dataset_version,
            universe_version=row.universe_version,
            cost_model=row.cost_model,
            fill_model=row.fill_model,
            random_seed=row.random_seed,
            git_commit=row.git_commit,
            docker_image=row.docker_image,
            python_version=row.python_version,
            dependency_lock_hash=row.dependency_lock_hash,
            notes=row.notes,
            bar_timestamp=row.bar_timestamp,
            status=row.status,
            current_step=row.current_step,
            last_successful_step=row.last_successful_step,
            error_message=row.error_message,
        )
