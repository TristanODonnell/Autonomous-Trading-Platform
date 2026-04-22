from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from autonomous_trading_platform.contracts.common.enums import CheckpointStatus
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class DatasetVersionFinalizationService:
    def __init__(self, session, base_path: str = "data") -> None:
        self.session = session
        self.base_path = base_path

    def finalize_backfill_dataset_version(
        self,
        *,
        ingestion_run_id: str,
        dataset_version_id: str,
    ) -> str:
        with SorUnitOfWork(self.session) as uow:
            checkpoints = uow.ingestion_checkpoints.list_for_run_and_dataset(
                ingestion_run_id=ingestion_run_id,
                dataset_version=dataset_version_id,
            )

            dataset_version = uow.dataset_versions.get_by_dataset_version_id(dataset_version_id)

            if dataset_version is None:
                raise ValueError(f"Dataset version {dataset_version_id} not found.")

            all_completed = all(
                checkpoint.checkpoint_status == CheckpointStatus.COMPLETED
                for checkpoint in checkpoints
            )

            if not checkpoints or not all_completed:
                dataset_version.validation_status = "incomplete"

                failed = [
                    checkpoint
                    for checkpoint in checkpoints
                    if checkpoint.checkpoint_status != CheckpointStatus.COMPLETED
                ]

                dataset_version.metadata_json = {
                    **(dataset_version.metadata_json or {}),
                    "validation": {
                        "status": "incomplete",
                        "failed_checkpoints": [
                            {
                                "symbol": cp.symbol,
                                "date": (
                                    cp.checkpoint_date.isoformat() if cp.checkpoint_date else None
                                ),
                                "status": cp.checkpoint_status.value,
                                "error": cp.error_message,
                            }
                            for cp in failed
                        ],
                    },
                }

                uow.dataset_versions.upsert(dataset_version)
                return str(dataset_version.validation_status)

            artifact_metadata = self._read_artifact_metadata(dataset_version_id)

            dataset_version.validation_status = "validated"
            dataset_version.checksum = str(artifact_metadata["checksum"])

            dataset_version.metadata_json = {
                **(dataset_version.metadata_json or {}),
                "validation": {
                    "status": "validated",
                    "failed_checkpoints": [],
                },
                "artifacts": artifact_metadata,
            }

            uow.dataset_versions.upsert(dataset_version)

            return str(dataset_version.validation_status)

    def _read_artifact_metadata(
        self,
        dataset_version_id: str,
    ) -> dict[str, Any]:
        root = dataset_version_root(
            self.base_path,
            RAW_BARS_DATASET,
            dataset_version_id,
        )

        metadata_path = Path(root) / "_metadata.json"

        if not metadata_path.exists():
            raise FileNotFoundError(f"Missing dataset metadata file: {metadata_path}")

        raw_data = json.loads(metadata_path.read_text(encoding="utf-8"))
        return cast(dict[str, Any], raw_data)
