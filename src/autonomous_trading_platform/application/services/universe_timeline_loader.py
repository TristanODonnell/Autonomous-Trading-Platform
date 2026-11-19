"""Load an exported universe timeline by its dataset version id.

A research run names the timeline it trades on the way it names its bars: by version id.
There is no "newest timeline" here on purpose.

The registration row and the file are read together and must agree: the content hash
stored at export is computed again from the file and the row's steps and rules. A file
that was replaced, cut short or copied under another version id is refused, not trusted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.universe_timeline_export_service import (
    StoredTimeline,
    timeline_content_hash,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    TIMELINE_SUMMARY_KEY,
    UNIVERSE_TIMELINE_LIFECYCLE,
    UniverseTimeline,
    UniverseTimelineExportStep,
)
from autonomous_trading_platform.storage.parquet.datasets import UNIVERSE_TIMELINE_DATASET
from autonomous_trading_platform.storage.parquet.repositories.parquet_universe_timeline_repository import (
    ParquetUniverseTimelineRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)


class UniverseTimelineLoadError(RuntimeError):
    """The version is not a usable universe timeline; nothing is returned."""


def load_universe_timeline(
    session: Session, dataset_version_id: str, *, base_path: str | Path | None = None
) -> UniverseTimeline:
    """The timeline registered as ``dataset_version_id``, checked against its file.

    Raises UniverseTimelineLoadError when the version is not registered, is not a
    validated universe timeline, has no file under the data root, or its file does not
    match what was registered.
    """
    row = DatasetVersionsRepository(session).get_by_dataset_version_id(dataset_version_id)
    if row is None:
        raise UniverseTimelineLoadError(
            f"dataset version {dataset_version_id} is not registered in this database"
        )
    metadata: dict[str, Any] = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    if (
        row.dataset_name != UNIVERSE_TIMELINE_DATASET.dataset_key
        or metadata.get("dataset_lifecycle") != UNIVERSE_TIMELINE_LIFECYCLE
    ):
        raise UniverseTimelineLoadError(
            f"dataset version {dataset_version_id} is a {row.dataset_name} version, not a "
            "universe timeline"
        )
    if row.validation_status != "validated":
        raise UniverseTimelineLoadError(
            f"universe timeline {dataset_version_id} is registered as "
            f"{row.validation_status!r}, not validated"
        )

    stamp = metadata.get(TIMELINE_SUMMARY_KEY)
    registered_hash = metadata.get("content_hash")
    raw_steps = metadata.get("steps")
    if (
        not isinstance(stamp, dict)
        or not isinstance(registered_hash, str)
        or not isinstance(raw_steps, list)
    ):
        raise UniverseTimelineLoadError(
            f"universe timeline {dataset_version_id} is registered without its rules, "
            "steps or content hash"
        )
    try:
        steps = [UniverseTimelineExportStep.model_validate(step) for step in raw_steps]
    except ValidationError as exc:
        raise UniverseTimelineLoadError(
            f"universe timeline {dataset_version_id} is registered with a step that cannot "
            f"be read: {exc.errors()[0]['msg']}"
        ) from exc

    try:
        periods = ParquetUniverseTimelineRepository(base_path=base_path).read_periods(
            dataset_version_id
        )
    except (FileNotFoundError, ValueError) as exc:
        raise UniverseTimelineLoadError(str(exc)) from exc

    found_hash = timeline_content_hash(
        StoredTimeline(
            stamp=stamp, window_start=row.date_coverage_start, steps=steps, periods=periods
        )
    )
    if found_hash != registered_hash:
        raise UniverseTimelineLoadError(
            f"universe timeline {dataset_version_id}: the file does not match its "
            f"registration (content hash {found_hash[:12]} on disk, {registered_hash[:12]} "
            "registered)"
        )

    return UniverseTimeline(
        dataset_version_id=dataset_version_id,
        window_start=row.date_coverage_start,
        rules=stamp,
        content_hash=registered_hash,
        steps=steps,
        periods=periods,
    )
