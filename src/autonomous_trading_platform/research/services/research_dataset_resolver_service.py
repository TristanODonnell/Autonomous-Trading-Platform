from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
    RAW_BARS_DATASET,
    ParquetDataset,
)
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root


@dataclass(slots=True)
class ResolvedResearchDataset:
    dataset_version: str
    dataset: ParquetDataset
    price_basis: PriceBasis
    root_path: Path
    schema_version: str
    metadata: dict[str, Any]


class ResearchDatasetResolver:
    def __init__(self, *, base_path: str = "data") -> None:
        self.base_path = Path(base_path)

    def resolve_bars_dataset(
        self,
        *,
        dataset_version: str,
        price_basis: PriceBasis,
    ) -> ResolvedResearchDataset:
        if not dataset_version.strip():
            raise ValueError("dataset_version must not be empty")

        dataset = self._bars_dataset_for_price_basis(price_basis)
        root_path = dataset_version_root(self.base_path, dataset, dataset_version)

        if not root_path.exists():
            raise FileNotFoundError(
                f"Dataset root does not exist for dataset_version={dataset_version}: {root_path}"
            )

        metadata: dict[str, Any] = {
            "dataset_name": dataset.dataset_key,
            "dataset_version": dataset_version,
            "price_basis": price_basis.value,
            "schema_version": dataset.schema_version,
            "root_path": str(root_path),
        }

        return ResolvedResearchDataset(
            dataset_version=dataset_version,
            dataset=dataset,
            price_basis=price_basis,
            root_path=root_path,
            schema_version=dataset.schema_version,
            metadata=metadata,
        )

    @staticmethod
    def _bars_dataset_for_price_basis(price_basis: PriceBasis) -> ParquetDataset:
        if price_basis == PriceBasis.RAW:
            return RAW_BARS_DATASET
        if price_basis == PriceBasis.ADJUSTED:
            return ADJUSTED_BARS_DATASET

        raise ValueError(f"Unsupported price basis for research bars dataset: {price_basis}")
