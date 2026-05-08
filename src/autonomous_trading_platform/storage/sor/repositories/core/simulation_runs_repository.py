# storage/sor/repositories/simulation_runs_repository.py

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.simulation_run import SimulationRun
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class SimulationRunsRepository(BaseRepository):
    def get_by_run_id(self, run_id: str) -> SimulationRuns | None:
        stmt = select(SimulationRuns).where(SimulationRuns.run_id == run_id)
        return cast(SimulationRuns | None, self.session.scalars(stmt).one_or_none())

    def list_by_experiment_id(self, experiment_id: str) -> list[SimulationRuns]:
        stmt = (
            select(SimulationRuns)
            .where(SimulationRuns.experiment_id == experiment_id)
            .order_by(SimulationRuns.start_time.desc())
        )
        return list(self.session.scalars(stmt).all())

    def list_by_strategy_id(self, strategy_id: str) -> list[SimulationRuns]:
        stmt = (
            select(SimulationRuns)
            .where(SimulationRuns.strategy_id == strategy_id)
            .order_by(SimulationRuns.start_time.desc())
        )
        return list(self.session.scalars(stmt).all())

    def insert(self, row: SimulationRuns) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[SimulationRuns]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: SimulationRuns) -> SimulationRuns:
        existing = self.get_by_run_id(row.run_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in SimulationRuns.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_run_id(self, run_id: str) -> None:
        obj = self.get_by_run_id(run_id)
        if obj is not None:
            self.session.delete(obj)

    def to_row(self, contract: SimulationRun) -> SimulationRuns:
        return SimulationRuns(
            run_id=contract.run_id,
            experiment_id=contract.experiment_id,
            strategy_id=contract.strategy_id,
            dataset_version=contract.dataset_version,
            universe_version=contract.universe_version,
            price_basis=contract.price_basis.value,
            symbols=contract.symbols,
            start_date=contract.start_date,
            end_date=contract.end_date,
            window_role=contract.window_role,
            start_time=contract.start_time,
            end_time=contract.end_time,
            execution_config=contract.execution_config,
            status=contract.status,
            metrics_snapshot_id=contract.metrics_snapshot_id,
        )

    def to_contract(self, row: SimulationRuns) -> SimulationRun:
        return SimulationRun(
            run_id=row.run_id,
            experiment_id=row.experiment_id,
            strategy_id=row.strategy_id,
            dataset_version=row.dataset_version,
            universe_version=row.universe_version,
            price_basis=PriceBasis(row.price_basis),
            symbols=row.symbols,
            start_date=row.start_date,
            end_date=row.end_date,
            window_role=row.window_role,
            start_time=row.start_time,
            end_time=row.end_time,
            execution_config=row.execution_config,
            status=row.status,
            metrics_snapshot_id=row.metrics_snapshot_id,
        )
