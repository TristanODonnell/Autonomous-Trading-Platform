from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    SIMULATION_EQUITY_CURVE_DATASET,
    SIMULATION_PER_BAR_METRICS_DATASET,
    SIMULATION_POSITIONS_DATASET,
    SIMULATION_SIGNAL_LOG_DATASET,
    SIMULATION_TRADE_LOGS_DATASET,
    ParquetDataset,
)

SIMULATION_DATASETS_BY_NAME = {
    "trade_logs": SIMULATION_TRADE_LOGS_DATASET,
    "equity_curve": SIMULATION_EQUITY_CURVE_DATASET,
    "per_bar_metrics": SIMULATION_PER_BAR_METRICS_DATASET,
    "positions": SIMULATION_POSITIONS_DATASET,
    "signal_log": SIMULATION_SIGNAL_LOG_DATASET,
}


class ParquetSimulationRepository:
    def __init__(self, *, base_path: str = "data") -> None:
        self.base_path = Path(base_path)

    def _dataset_root(self, dataset: ParquetDataset) -> Path:
        root = self.base_path
        for part in dataset.root_parts:
            root = root / part
        return root

    def _validate_table(self, table: pa.Table, *, dataset: ParquetDataset) -> None:
        expected_schema = dataset.schema

        if table.schema.names != expected_schema.names:
            raise ValueError(
                f"Simulation table columns do not match expected schema. "
                f"Expected={expected_schema.names}, actual={table.schema.names}"
            )

        for expected_field, actual_field in zip(
            expected_schema,
            table.schema,
            strict=True,
        ):
            if expected_field.type != actual_field.type:
                raise ValueError(
                    f"Simulation table field type mismatch for "
                    f"'{expected_field.name}': "
                    f"expected={expected_field.type}, actual={actual_field.type}"
                )

    def _prepare_simulation_frame_for_write(
        self,
        *,
        frame: pd.DataFrame,
        dataset: ParquetDataset,
        identity: SimulationArtifactIdentity,
    ) -> pd.DataFrame:
        prepared = frame.copy()

        if "timestamp" not in prepared.columns:
            raise ValueError("Simulation frame is missing required column: timestamp")

        timestamp = pd.to_datetime(prepared["timestamp"], utc=True)

        if "date" not in prepared.columns:
            prepared["date"] = timestamp.dt.date

        if "experiment_id" not in prepared.columns:
            prepared["experiment_id"] = identity.experiment_id

        if "strategy_id" not in prepared.columns:
            prepared["strategy_id"] = identity.strategy_id

        if "run_id" not in prepared.columns:
            prepared["run_id"] = identity.run_id

        # Always overwrite — these are identity-level columns that must match
        # the artifact identity regardless of what the execution engine emits.
        prepared["stage_name"] = identity.stage_name
        prepared["window_role"] = identity.window_role
        prepared["dataset_version"] = identity.dataset_version

        missing_columns = [
            column for column in dataset.schema.names if column not in prepared.columns
        ]

        if missing_columns:
            raise ValueError(
                f"Simulation frame is missing required schema columns: "
                f"{missing_columns}. "
                f"actual columns={list(prepared.columns)}, "
                f"expected columns={dataset.schema.names}"
            )

        return prepared[dataset.schema.names]

    def write_simulation_frame(
        self,
        *,
        frame: pd.DataFrame,
        output_name: str,
        identity: SimulationArtifactIdentity,
    ) -> None:
        try:
            dataset = SIMULATION_DATASETS_BY_NAME[output_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported simulation output_name={output_name}") from exc

        prepared_frame = self._prepare_simulation_frame_for_write(
            frame=frame,
            dataset=dataset,
            identity=identity,
        )

        table = pa.Table.from_pandas(prepared_frame, preserve_index=False)

        self.write_simulation_dataset(
            dataset=dataset,
            table=table,
        )

    def write_simulation_dataset(
        self,
        *,
        dataset: ParquetDataset,
        table: pa.Table,
    ) -> None:
        table = table.cast(dataset.schema)
        self._validate_table(table, dataset=dataset)

        dataset_root = self._dataset_root(dataset)
        dataset_root.mkdir(parents=True, exist_ok=True)

        # Use hive-style partitioning so directories follow key=value naming,
        # making partition paths self-documenting and readable by standard tools.
        partition_schema = pa.schema([(col, pa.string()) for col in dataset.partition_cols])
        partitioning = ds.partitioning(partition_schema, flavor="hive")

        ds.write_dataset(
            data=table,
            base_dir=str(dataset_root),
            format="parquet",
            partitioning=partitioning,
            existing_data_behavior="overwrite_or_ignore",
        )

    def write_trade_logs(
        self,
        *,
        frame: pd.DataFrame,
        identity: SimulationArtifactIdentity,
    ) -> None:
        self.write_simulation_frame(frame=frame, output_name="trade_logs", identity=identity)

    def write_equity_curve(
        self,
        *,
        frame: pd.DataFrame,
        identity: SimulationArtifactIdentity,
    ) -> None:
        self.write_simulation_frame(frame=frame, output_name="equity_curve", identity=identity)

    def read_equity_curve(
        self,
        *,
        experiment_id: str,
        strategy_id: str,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        dataset_root = self._dataset_root(SIMULATION_EQUITY_CURVE_DATASET)
        if not dataset_root.exists():
            return []

        dataset = ds.dataset(str(dataset_root), format="parquet", partitioning="hive")
        if not {"experiment_id", "strategy_id"}.issubset(set(dataset.schema.names)):
            return []
        filter_expr = (ds.field("experiment_id") == experiment_id) & (
            ds.field("strategy_id") == strategy_id
        )
        if run_id is not None:
            filter_expr = filter_expr & (ds.field("run_id") == run_id)

        table = dataset.to_table(filter=filter_expr)
        if table.num_rows == 0:
            return []

        frame = table.to_pandas().sort_values("timestamp")
        return [
            {
                "timestamp": row["timestamp"],
                "value": float(row["equity"]),
                "drawdown": float(row["drawdown"] or 0.0),
            }
            for _, row in frame.iterrows()
        ]

    def write_per_bar_metrics(
        self,
        *,
        frame: pd.DataFrame,
        identity: SimulationArtifactIdentity,
    ) -> None:
        self.write_simulation_frame(frame=frame, output_name="per_bar_metrics", identity=identity)

    def write_positions(
        self,
        *,
        frame: pd.DataFrame,
        identity: SimulationArtifactIdentity,
    ) -> None:
        self.write_simulation_frame(frame=frame, output_name="positions", identity=identity)

    def write_signal_log(
        self,
        *,
        frame: pd.DataFrame,
        identity: SimulationArtifactIdentity,
    ) -> None:
        self.write_simulation_frame(frame=frame, output_name="signal_log", identity=identity)
