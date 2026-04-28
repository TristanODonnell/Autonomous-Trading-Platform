from __future__ import annotations

import pandas as pd

from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)


class ResultRecorderService:
    def __init__(
        self,
        *,
        parquet_simulation_repository: ParquetSimulationRepository,
    ) -> None:
        self.parquet_simulation_repository = parquet_simulation_repository

    def record_results(
        self,
        *,
        experiment_id: str,
        strategy_id: str,
        trade_logs: pd.DataFrame,
        equity_curve: pd.DataFrame,
        per_bar_metrics: pd.DataFrame,
    ) -> None:
        self.parquet_simulation_repository.write_trade_logs(
            frame=trade_logs,
            experiment_id=experiment_id,
            strategy_id=strategy_id,
        )

        self.parquet_simulation_repository.write_equity_curve(
            frame=equity_curve,
            experiment_id=experiment_id,
            strategy_id=strategy_id,
        )

        self.parquet_simulation_repository.write_per_bar_metrics(
            frame=per_bar_metrics,
            experiment_id=experiment_id,
            strategy_id=strategy_id,
        )
