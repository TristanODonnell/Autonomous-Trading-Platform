# storage/sor/repositories/experiments_repository.py

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.experiment import Experiment
from autonomous_trading_platform.storage.sor.models.experiments import Experiments
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class ExperimentsRepository(BaseRepository):
    def get_by_experiment_id(self, experiment_id: str) -> Experiments | None:
        stmt = select(Experiments).where(Experiments.experiment_id == experiment_id)
        return cast(Experiments | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: Experiments) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[Experiments]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: Experiments) -> Experiments:
        existing = self.get_by_experiment_id(row.experiment_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in Experiments.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_experiment_id(self, experiment_id: str) -> None:
        obj = self.get_by_experiment_id(experiment_id)
        if obj is not None:
            self.session.delete(obj)

    def to_row(self, contract: Experiment) -> Experiments:
        return Experiments(
            experiment_id=contract.experiment_id,
            experiment_name=contract.experiment_name,
            created_at=contract.created_at,
            description=contract.description,
            status=contract.status,
            strategy_set_json=contract.strategy_set_json,
            parameter_grid_json=contract.parameter_grid_json,
            dataset_version=contract.dataset_version,
            universe_version=contract.universe_version,
            start_time=contract.start_time,
            end_time=contract.end_time,
            metadata_json=contract.metadata_json,
        )

    def to_contract(self, row: Experiments) -> Experiment:
        return Experiment(
            experiment_id=row.experiment_id,
            experiment_name=row.experiment_name,
            created_at=row.created_at,
            description=row.description,
            status=row.status,
            strategy_set_json=row.strategy_set_json,
            parameter_grid_json=row.parameter_grid_json,
            dataset_version=row.dataset_version,
            universe_version=row.universe_version,
            start_time=row.start_time,
            end_time=row.end_time,
            metadata_json=row.metadata_json,
        )
