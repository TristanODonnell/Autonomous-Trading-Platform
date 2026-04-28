from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from autonomous_trading_platform.storage.parquet.datasets import (
    SIMULATION_EQUITY_CURVE_DATASET,
    SIMULATION_PER_BAR_METRICS_DATASET,
    SIMULATION_TRADE_LOGS_DATASET,
    ParquetDataset,
)

SIMULATION_DATASETS_BY_NAME = {
    "trade_logs": SIMULATION_TRADE_LOGS_DATASET,
    "equity_curve": SIMULATION_EQUITY_CURVE_DATASET,
    "per_bar_metrics": SIMULATION_PER_BAR_METRICS_DATASET,
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
        experiment_id: str,
        strategy_id: str,
    ) -> pd.DataFrame:
        prepared = frame.copy()

        if "timestamp" not in prepared.columns:
            raise ValueError("Simulation frame is missing required column: timestamp")

        timestamp = pd.to_datetime(prepared["timestamp"], utc=True)

        if "date" not in prepared.columns:
            prepared["date"] = timestamp.dt.date

        if "experiment_id" not in prepared.columns:
            prepared["experiment_id"] = experiment_id

        if "strategy_id" not in prepared.columns:
            prepared["strategy_id"] = strategy_id

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
        experiment_id: str,
        strategy_id: str,
    ) -> None:
        try:
            dataset = SIMULATION_DATASETS_BY_NAME[output_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported simulation output_name={output_name}") from exc

        prepared_frame = self._prepare_simulation_frame_for_write(
            frame=frame,
            dataset=dataset,
            experiment_id=experiment_id,
            strategy_id=strategy_id,
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

        ds.write_dataset(
            data=table,
            base_dir=str(dataset_root),
            format="parquet",
            partitioning=list(dataset.partition_cols),
            existing_data_behavior="overwrite_or_ignore",
        )

    def write_trade_logs(
        self,
        *,
        frame: pd.DataFrame,
        experiment_id: str,
        strategy_id: str,
    ) -> None:
        self.write_simulation_frame(
            frame=frame,
            output_name="trade_logs",
            experiment_id=experiment_id,
            strategy_id=strategy_id,
        )

    def write_equity_curve(
        self,
        *,
        frame: pd.DataFrame,
        experiment_id: str,
        strategy_id: str,
    ) -> None:
        self.write_simulation_frame(
            frame=frame,
            output_name="equity_curve",
            experiment_id=experiment_id,
            strategy_id=strategy_id,
        )

    def write_per_bar_metrics(
        self,
        *,
        frame: pd.DataFrame,
        experiment_id: str,
        strategy_id: str,
    ) -> None:
        self.write_simulation_frame(
            frame=frame,
            output_name="per_bar_metrics",
            experiment_id=experiment_id,
            strategy_id=strategy_id,
        )
