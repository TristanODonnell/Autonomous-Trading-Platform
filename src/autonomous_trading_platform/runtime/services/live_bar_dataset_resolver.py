"""Which raw-bars dataset versions hold a date window in live/paper.

Live ingestion registers one validated ``raw_bars`` version per trading date
(``DailyDatasetVersionResolverService``), so a strategy's warmup window spans several
versions. This resolver lists the validated versions whose coverage overlaps the
window, oldest coverage first (latest registration last for the same date, so a
re-ingested day wins when the reader de-duplicates by timestamp).

Backtests pass one cumulative version explicitly and never use this.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)


class LiveBarDatasetResolver:
    def __init__(
        self,
        session: Session,
        *,
        dataset_name: str = RAW_BARS_DATASET.dataset_key,
        price_basis: PriceBasis = PriceBasis.RAW,
    ) -> None:
        self._repo = DatasetVersionsRepository(session)
        self._dataset_name = dataset_name
        self._price_basis = price_basis
        self._cache: dict[tuple[date, date], list[str]] = {}

    def resolve(self, start_date: date, end_date: date) -> list[str]:
        key = (start_date, end_date)
        cached = self._cache.get(key)
        if cached is not None:
            return list(cached)
        rows = self._repo.list_validated_overlapping(
            dataset_name=self._dataset_name,
            price_basis=self._price_basis,
            start_date=start_date,
            end_date=end_date,
        )
        ids = [row.dataset_version_id for row in rows]
        self._cache[key] = ids
        return list(ids)

    def invalidate(self) -> None:
        self._cache.clear()
