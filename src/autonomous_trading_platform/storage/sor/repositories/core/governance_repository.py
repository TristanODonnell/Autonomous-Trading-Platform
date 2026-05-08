from __future__ import annotations

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.governance.strategy_governance import (
    GovernanceState,
    StrategyGovernanceRecord,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class GovernanceRepository(BaseRepository):
    def get_by_strategy(self, strategy_id: str, config_hash: str) -> StrategyGovernance | None:
        stmt = select(StrategyGovernance).where(
            StrategyGovernance.strategy_id == strategy_id,
            StrategyGovernance.config_hash == config_hash,
        )
        return cast(StrategyGovernance | None, self.session.scalars(stmt).one_or_none())

    def list_by_state(self, state: GovernanceState) -> list[StrategyGovernance]:
        stmt = select(StrategyGovernance).where(StrategyGovernance.current_state == state.value)
        return list(self.session.scalars(stmt).all())

    def list_by_experiment(self, experiment_id: str) -> list[StrategyGovernance]:
        stmt = select(StrategyGovernance).where(StrategyGovernance.experiment_id == experiment_id)
        return list(self.session.scalars(stmt).all())

    def upsert(self, row: StrategyGovernance) -> StrategyGovernance:
        existing = self.get_by_strategy(row.strategy_id, row.config_hash)
        if existing is None:
            self.session.add(row)
            return row
        for column in StrategyGovernance.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))
        return existing

    def to_row(self, contract: StrategyGovernanceRecord) -> StrategyGovernance:
        return StrategyGovernance(
            strategy_id=contract.strategy_id,
            config_hash=contract.config_hash,
            current_state=contract.current_state.value,
            experiment_id=contract.experiment_id,
            source_run_id=contract.source_run_id,
            submitted_at=contract.submitted_at,
            updated_at=contract.updated_at,
            submitted_by=contract.submitted_by,
        )

    def to_contract(self, row: StrategyGovernance) -> StrategyGovernanceRecord:
        return StrategyGovernanceRecord(
            strategy_id=row.strategy_id,
            config_hash=row.config_hash,
            current_state=GovernanceState(row.current_state),
            experiment_id=row.experiment_id,
            source_run_id=row.source_run_id,
            submitted_at=row.submitted_at,
            updated_at=row.updated_at,
            submitted_by=row.submitted_by,
        )
