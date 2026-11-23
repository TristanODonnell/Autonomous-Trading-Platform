"""Read a research-window dataset's registration by its dataset version id.

A research job names its bars by version id (``research.options.dataset_version``).
Only a dataset made by ``atp datasets build-window`` is accepted: it is the one kind
whose sessions were checked when it was built, and its registration says where the
bars begin and end, which names they hold and which sessions were left out by name.

Nothing is read from the bar files here. This answers "what does this version hold",
from the row written at build time.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    RESEARCH_WINDOW_LIFECYCLE,
    ResearchWindowDataset,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)


class ResearchWindowDatasetLoadError(RuntimeError):
    """The version is not a usable research-window dataset; nothing is returned."""


def load_research_window_dataset(
    session: Session, dataset_version_id: str
) -> ResearchWindowDataset:
    """The research-window dataset registered as ``dataset_version_id``.

    Raises ResearchWindowDatasetLoadError when the version is not registered, is not a
    validated research-window dataset, or is registered without its first and last
    day or the names it holds.
    """
    row = DatasetVersionsRepository(session).get_by_dataset_version_id(dataset_version_id)
    if row is None:
        raise ResearchWindowDatasetLoadError(
            f"dataset version {dataset_version_id} is not registered in this database"
        )
    metadata: dict[str, Any] = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    if (
        row.dataset_name != RAW_BARS_DATASET.dataset_key
        or metadata.get("dataset_lifecycle") != RESEARCH_WINDOW_LIFECYCLE
    ):
        raise ResearchWindowDatasetLoadError(
            f"dataset version {dataset_version_id} is not a research-window dataset (it is "
            f"a {row.dataset_name} version from {row.source}); name one made by "
            "`atp datasets build-window`"
        )
    if row.validation_status != "validated":
        raise ResearchWindowDatasetLoadError(
            f"research-window dataset {dataset_version_id} is registered as "
            f"{row.validation_status!r}, not validated"
        )
    if row.date_coverage_start is None or row.date_coverage_end is None:
        raise ResearchWindowDatasetLoadError(
            f"research-window dataset {dataset_version_id} is registered without its first "
            "and last day"
        )
    manifest: dict[str, Any] = row.source_manifest if isinstance(row.source_manifest, dict) else {}
    names = manifest.get("symbols")
    if (
        not isinstance(names, list)
        or not names
        or not all(isinstance(name, str) and name for name in names)
    ):
        raise ResearchWindowDatasetLoadError(
            f"research-window dataset {dataset_version_id} is registered without the names it holds"
        )
    raw_excluded = metadata.get("excluded_sessions") or []
    try:
        excluded = sorted({date.fromisoformat(day) for day in raw_excluded})
    except (TypeError, ValueError) as exc:
        raise ResearchWindowDatasetLoadError(
            f"research-window dataset {dataset_version_id} is registered with a left-out "
            f"session that is not a date: {raw_excluded!r}"
        ) from exc
    return ResearchWindowDataset(
        dataset_version_id=dataset_version_id,
        first_day=row.date_coverage_start,
        last_day=row.date_coverage_end,
        symbols=tuple(sorted(set(names))),
        excluded_sessions=tuple(excluded),
    )
