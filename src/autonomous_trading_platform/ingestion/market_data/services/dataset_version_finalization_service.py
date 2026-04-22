from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from autonomous_trading_platform.contracts.common.enums import CheckpointStatus
from autonomous_trading_platform.storage.parquet.compute_checksum import compute_file_checksum
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

            files = self._list_dataset_files(dataset_version_id)

            self._persist_file_checksums(
                uow,
                dataset_version_id=dataset_version_id,
                files=files,
            )

            aggregate_checksum = self._compute_aggregate_checksum(files)

            dataset_version.validation_status = "validated"
            dataset_version.checksum = aggregate_checksum

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

    def _list_dataset_files(
        self,
        dataset_version_id: str,
    ) -> list[Path]:
        root = dataset_version_root(
            self.base_path,
            RAW_BARS_DATASET,
            dataset_version_id,
        )

        if not root.exists():
            raise FileNotFoundError(f"Dataset root not found: {root}")

        files = sorted(path for path in root.rglob("*.parquet") if path.is_file())

        if not files:
            raise FileNotFoundError(f"No parquet files found under dataset root: {root}")

        return files

    def _compute_aggregate_checksum(
        self,
        files: list[Path],
    ) -> str:
        import hashlib

        hasher = hashlib.sha256()

        for path in files:
            file_checksum = compute_file_checksum(path)
            hasher.update(str(path).encode("utf-8"))
            hasher.update(b"|")
            hasher.update(file_checksum.encode("utf-8"))
            hasher.update(b"\n")

        return hasher.hexdigest()

    def _persist_file_checksums(
        self,
        uow,
        *,
        dataset_version_id: str,
        files: list[Path],
    ) -> None:
        for path in files:
            checksum_value = compute_file_checksum(path)

            checksum_row = uow.checksums.build_row(
                dataset_version=dataset_version_id,
                object_type="parquet_file",
                object_path=str(path),
                checksum_algorithm="sha256",
                checksum_value=checksum_value,
            )
            uow.checksums.upsert(checksum_row)
