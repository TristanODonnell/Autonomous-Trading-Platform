from __future__ import annotations

import pandas as pd

from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
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
        identity: SimulationArtifactIdentity,
        trade_logs: pd.DataFrame,
        equity_curve: pd.DataFrame,
        per_bar_metrics: pd.DataFrame,
        positions: pd.DataFrame,
        signal_log: pd.DataFrame,
    ) -> None:
        self.parquet_simulation_repository.write_trade_logs(
            frame=trade_logs,
            identity=identity,
        )
        self.parquet_simulation_repository.write_equity_curve(
            frame=equity_curve,
            identity=identity,
        )
        self.parquet_simulation_repository.write_per_bar_metrics(
            frame=per_bar_metrics,
            identity=identity,
        )
        self.parquet_simulation_repository.write_positions(
            frame=positions,
            identity=identity,
        )
        self.parquet_simulation_repository.write_signal_log(
            frame=signal_log,
            identity=identity,
        )
