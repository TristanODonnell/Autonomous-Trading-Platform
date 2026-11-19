"""Read one exported universe timeline version back from Parquet.

The bar reader walks ``symbol=/year=/month=`` folders. A timeline has none: a version is
one small file read whole, so it has its own read here.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyarrow.dataset as ds

from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
from autonomous_trading_platform.storage.parquet.datasets import UNIVERSE_TIMELINE_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root, get_data_root

_COLUMNS = [field.name for field in UNIVERSE_TIMELINE_DATASET.schema]


class ParquetUniverseTimelineRepository:
    def __init__(self, *, base_path: str | Path | None = None) -> None:
        self._base_path = Path(base_path) if base_path is not None else get_data_root()

    def version_root(self, dataset_version: str) -> Path:
        return dataset_version_root(self._base_path, UNIVERSE_TIMELINE_DATASET, dataset_version)

    def read_periods(self, dataset_version: str) -> list[UniverseTimelinePeriod]:
        """The version's lists, oldest first, each with its members best rank first.

        Raises FileNotFoundError when the version has no file under the data root, and
        ValueError when the file is not shaped as a timeline (other columns, or one list
        with two different spans).
        """
        root = self.version_root(dataset_version)
        if not root.is_dir() or not any(root.rglob("*.parquet")):
            raise FileNotFoundError(
                f"universe timeline version {dataset_version} has no file under {root}"
            )
        table = ds.dataset(str(root), format="parquet").to_table()
        missing = [name for name in _COLUMNS if name not in table.column_names]
        if missing:
            raise ValueError(
                f"universe timeline version {dataset_version} lacks column(s) {missing}"
            )

        spans: dict[str, tuple[datetime, datetime | None]] = {}
        members: dict[str, list[UniverseTimelineMember]] = {}
        for row in table.select(_COLUMNS).to_pylist():
            version_id = row["universe_version_id"]
            span = (row["effective_from"], row["effective_to"])
            if spans.setdefault(version_id, span) != span:
                raise ValueError(
                    f"universe timeline version {dataset_version}: list {version_id} is "
                    f"stored with two spans, {spans[version_id]} and {span}"
                )
            members.setdefault(version_id, []).append(
                UniverseTimelineMember(symbol=row["symbol"], rank=row["rank"])
            )

        return [
            UniverseTimelinePeriod(
                universe_version_id=version_id,
                effective_from=effective_from,
                effective_to=effective_to,
                members=sorted(
                    members[version_id], key=lambda m: (m.rank is None, m.rank or 0, m.symbol)
                ),
            )
            for version_id, (effective_from, effective_to) in sorted(
                spans.items(), key=lambda item: item[1][0]
            )
        ]
