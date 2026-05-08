# storage/sor/repositories/strategy_configs_repository.py

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.strategy_config import StrategyConfig
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class StrategyConfigsRepository(BaseRepository):
    def get_by_strategy_id(self, strategy_id: str) -> StrategyConfigs | None:
        stmt = select(StrategyConfigs).where(StrategyConfigs.strategy_id == strategy_id)
        return cast(StrategyConfigs | None, self.session.scalars(stmt).one_or_none())

    def get_by_config_hash(self, config_hash: str) -> StrategyConfigs | None:
        stmt = select(StrategyConfigs).where(StrategyConfigs.config_hash == config_hash)
        return cast(StrategyConfigs | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: StrategyConfigs) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[StrategyConfigs]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: StrategyConfigs) -> StrategyConfigs:
        existing = self.get_by_strategy_id(row.strategy_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in StrategyConfigs.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_strategy_id(self, strategy_id: str) -> None:
        obj = self.get_by_strategy_id(strategy_id)
        if obj is not None:
            self.session.delete(obj)

    def to_row(self, contract: StrategyConfig) -> StrategyConfigs:
        return StrategyConfigs(
            strategy_id=contract.strategy_id,
            config_hash=contract.config_hash,
            config_json=contract.config_json,
            created_at=contract.created_at,
            strategy_type=contract.strategy_type,
            metadata_json=contract.metadata_json,
        )

    def to_contract(self, row: StrategyConfigs) -> StrategyConfig:
        return StrategyConfig(
            strategy_id=row.strategy_id,
            config_hash=row.config_hash,
            config_json=row.config_json,
            created_at=row.created_at,
            strategy_type=row.strategy_type,
            metadata_json=row.metadata_json,
        )
