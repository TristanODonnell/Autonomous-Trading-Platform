"""Export a universe timeline as one versioned dataset.

``atp universe build-timeline`` leaves a timeline in the database: one rotation record
per step and one dated universe version per list. That is run state: a worker reset
deletes it, and a research run cannot name which timeline it was tested on. The export
writes the same lists to an immutable, registered Parquet version the research engine
can read by id.

The timeline is read from two places that must agree:

- the rotation records say what each step did (a new list, no change, or a refusal);
- the universe versions say what was in force from when to when.

A record that names a missing list, a list that starts at another time than its step,
a gap or an overlap between two lists, or steps stamped with different rules: any of
these refuses the export and nothing is written. The engine trusts this file for which
names may be traded on each day, and a hole in it would read as a universe of no names.

Idempotent per timeline: when the newest exported version holds the same rules, steps
and lists, nothing is written. A timeline extended by one more month is a new version.

The ``dataset_versions`` table was shaped for bars and requires a price basis and a bar
interval. A timeline has neither, so the row carries the same stand-ins the corporate
actions dataset uses (raw, one day) and names them in ``metadata_json``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pyarrow as pa
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
    UniverseStatus,
)
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    TIMELINE_RECORD_STATUS,
    TIMELINE_ROTATION_REASON,
    TIMELINE_SUMMARY_KEY,
    UNIVERSE_TIMELINE_LIFECYCLE,
    UNIVERSE_TIMELINE_SOURCE,
    UniverseTimelineExportResult,
    UniverseTimelineExportStep,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.storage.parquet.compute_checksum import compute_file_checksum
from autonomous_trading_platform.storage.parquet.datasets import UNIVERSE_TIMELINE_DATASET
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root, get_data_root
from autonomous_trading_platform.storage.parquet.schemas import UNIVERSE_TIMELINE_SCHEMA
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
    UniverseRotationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

# Columns of ``dataset_versions`` that mean nothing for a timeline (see module docstring).
REGISTRY_STAND_INS = ["price_basis", "interval"]


class UniverseTimelineExportError(RuntimeError):
    """The stored timeline does not hold together; nothing is exported."""


@dataclass(frozen=True)
class StoredTimeline:
    """A timeline as read from the database and checked."""

    stamp: dict[str, Any]
    window_start: date
    steps: list[UniverseTimelineExportStep]
    periods: list[UniverseTimelinePeriod]

    @property
    def symbols_union(self) -> list[str]:
        return sorted({m.symbol for period in self.periods for m in period.members})

    @property
    def rows(self) -> int:
        return sum(len(period.members) for period in self.periods)


class UniverseTimelineExportService:
    def __init__(self, session: Session, *, base_path: str | Path | None = None) -> None:
        self._session = session
        self._base_path = Path(base_path) if base_path is not None else get_data_root()

    # ------------------------------------------------------------------ public

    def export(self, *, now_utc: datetime, plan_only: bool = False) -> UniverseTimelineExportResult:
        try:
            timeline = self.read_timeline()
        except UniverseTimelineExportError as exc:
            return UniverseTimelineExportResult(status="refused", errors=[str(exc)])

        content_hash = timeline_content_hash(timeline)
        found: dict[str, Any] = {
            "window_start": timeline.window_start,
            "rules": timeline.stamp,
            "first_step_at": timeline.steps[0].timestamp,
            "last_step_at": timeline.steps[-1].timestamp,
            "steps": timeline.steps,
            "periods": timeline.periods,
            "symbols_union": timeline.symbols_union,
            "rows": timeline.rows,
            "content_hash": content_hash,
        }

        existing = self.latest_export()
        if existing is not None and existing[1] == content_hash:
            return UniverseTimelineExportResult(
                status="up_to_date", dataset_version_id=existing[0], **found
            )
        if plan_only:
            return UniverseTimelineExportResult(status="planned", **found)

        version_id = self._write_and_register(timeline, content_hash=content_hash, now_utc=now_utc)
        logger.info(
            "universe_timeline_export.created",
            extra={
                "dataset_version_id": version_id,
                "steps": len(timeline.steps),
                "periods": len(timeline.periods),
                "rows": timeline.rows,
                "symbols": len(timeline.symbols_union),
                "content_hash": content_hash,
            },
        )
        return UniverseTimelineExportResult(
            status="created", dataset_version_id=version_id, **found
        )

    def read_timeline(self) -> StoredTimeline:
        """The timeline this database holds, checked step by step.

        Raises UniverseTimelineExportError when there is none or it does not hold together.
        """
        rotation_repo = UniverseRotationRepository(self._session)
        version_repo = UniverseVersionRepository(self._session)

        records = rotation_repo.get_for_reason(TIMELINE_ROTATION_REASON)
        if not records:
            raise UniverseTimelineExportError(
                "this database holds no universe timeline (no rotation record with reason "
                f"{TIMELINE_ROTATION_REASON!r}); build one with `atp universe build-timeline`"
            )

        stamp = _stamp_of(records[0])
        for record in records[1:]:
            other = _stamp_of(record)
            if other != stamp:
                raise UniverseTimelineExportError(
                    f"the step at {_iso(record.created_at)} was built under {other}, the "
                    f"first step under {stamp}; one timeline has one first day and one "
                    "set of rules"
                )
        window_start = _window_start(stamp)
        first_at = datetime.combine(window_start, time.min, tzinfo=UTC)
        if records[0].created_at != first_at:
            raise UniverseTimelineExportError(
                f"the first step is at {_iso(records[0].created_at)}, the timeline's first "
                f"day is {window_start.isoformat()}: the steps before it are missing"
            )

        steps: list[UniverseTimelineExportStep] = []
        periods: list[UniverseTimelinePeriod] = []
        stored_ends: list[datetime | None] = []
        seen_times: set[datetime] = set()
        seen_versions: set[str] = set()
        for record in records:
            when = record.created_at
            if when in seen_times:
                raise UniverseTimelineExportError(
                    f"two timeline records for {_iso(when)}; expected one"
                )
            seen_times.add(when)
            outcome = TIMELINE_RECORD_STATUS.get(record.status)
            if outcome is None:
                raise UniverseTimelineExportError(
                    f"the step at {_iso(when)} has status {record.status!r}"
                )
            version_id = record.new_universe_version_id

            if outcome == "rotated":
                if version_id in seen_versions:
                    raise UniverseTimelineExportError(
                        f"the step at {_iso(when)} names list {version_id} as new, and so "
                        "does an earlier step"
                    )
                version = version_repo.get_by_version_id(version_id)
                if version is None:
                    raise UniverseTimelineExportError(
                        f"the step at {_iso(when)} names list {version_id}, which is not "
                        "in this database"
                    )
                if version.status not in (UniverseStatus.ACTIVE, UniverseStatus.RETIRED):
                    raise UniverseTimelineExportError(
                        f"list {version_id} (step at {_iso(when)}) has status "
                        f"{version.status!r}; a list that was in force is active or retired"
                    )
                if version.effective_from != when:
                    raise UniverseTimelineExportError(
                        f"list {version_id} is in force from {_iso(version.effective_from)}, "
                        f"its step ran at {_iso(when)}"
                    )
                members = [
                    UniverseTimelineMember(symbol=m.symbol, rank=m.rank)
                    for m in version_repo.get_included_members(version_id)
                ]
                if not members:
                    raise UniverseTimelineExportError(
                        f"list {version_id} (step at {_iso(when)}) has no members"
                    )
                seen_versions.add(version_id)
                stored_ends.append(version.effective_to)
                periods.append(
                    UniverseTimelinePeriod(
                        universe_version_id=version_id,
                        effective_from=when,
                        effective_to=version.effective_to,
                        members=sorted(members, key=_member_order),
                    )
                )
            else:
                if not periods:
                    raise UniverseTimelineExportError(
                        f"the first step, at {_iso(when)}, picked no list (status "
                        f"{record.status!r})"
                    )
                in_force = periods[-1].universe_version_id
                if version_id != in_force:
                    raise UniverseTimelineExportError(
                        f"the step at {_iso(when)} changed nothing but names list "
                        f"{version_id}; the list in force then was {in_force}"
                    )
            steps.append(
                UniverseTimelineExportStep(
                    timestamp=when, outcome=outcome, universe_version_id=version_id
                )
            )

        # Each list must end exactly where the next begins, and the last must be open.
        for index, period in enumerate(periods):
            expected = periods[index + 1].effective_from if index + 1 < len(periods) else None
            stored = stored_ends[index]
            if stored == expected:
                continue
            if expected is None:
                raise UniverseTimelineExportError(
                    f"the last list, {period.universe_version_id}, ends at {_iso(stored)} "
                    "but no later step picked a list: something outside the timeline "
                    "retired it"
                )
            if stored is None:
                raise UniverseTimelineExportError(
                    f"list {period.universe_version_id} has no end and the next begins "
                    f"at {_iso(expected)}: an overlap in the timeline"
                )
            kind = "a gap" if stored < expected else "an overlap"
            raise UniverseTimelineExportError(
                f"list {period.universe_version_id} ends at {_iso(stored)} and the next "
                f"begins at {_iso(expected)}: {kind} in the timeline"
            )

        return StoredTimeline(stamp=stamp, window_start=window_start, steps=steps, periods=periods)

    def latest_export(self) -> tuple[str, str | None] | None:
        """The newest exported version's id and content hash, if any."""
        row = DatasetVersionsRepository(self._session).get_latest_validated(
            dataset_name=UNIVERSE_TIMELINE_DATASET.dataset_key, price_basis=PriceBasis.RAW
        )
        if row is None:
            return None
        found = (row.metadata_json or {}).get("content_hash")
        return row.dataset_version_id, str(found) if found is not None else None

    # ----------------------------------------------------------------- private

    def _write_and_register(
        self, timeline: StoredTimeline, *, content_hash: str, now_utc: datetime
    ) -> str:
        version_id = generate_dataset_version(UNIVERSE_TIMELINE_DATASET.dataset_key)
        root = dataset_version_root(self._base_path, UNIVERSE_TIMELINE_DATASET, version_id)
        write_table(
            timeline_table(timeline), UNIVERSE_TIMELINE_DATASET, self._base_path, version_id
        )
        try:
            files = [
                (path, compute_file_checksum(path))
                for path in sorted(p for p in root.rglob("*.parquet") if p.is_file())
            ]
            symbols = timeline.symbols_union
            last_step_at = timeline.steps[-1].timestamp
            # ``register`` checks the row against the dataset rules and commits it. The
            # version row must exist before any checksum row (``checksums`` references
            # ``dataset_versions``; Postgres enforces it).
            DatasetRegistrationService(session=self._session).register(
                DatasetVersion(
                    dataset_version_id=version_id,
                    dataset_name=UNIVERSE_TIMELINE_DATASET.dataset_key,
                    created_at=now_utc,
                    source=UNIVERSE_TIMELINE_SOURCE,
                    price_basis=PriceBasis.RAW,
                    interval=BarInterval.ONE_DAY,
                    schema_version=UNIVERSE_TIMELINE_DATASET.schema_version,
                    symbol_coverage=len(symbols),
                    date_coverage_start=timeline.window_start,
                    # The day of the last step. Its list stays in force after it.
                    date_coverage_end=last_step_at.astimezone(UTC).date(),
                    validation_status="validated",
                    checksum=_aggregate_checksum(files),
                    source_manifest={
                        "pipeline": UNIVERSE_TIMELINE_SOURCE,
                        "symbols": symbols,
                        TIMELINE_SUMMARY_KEY: timeline.stamp,
                    },
                    metadata_json={
                        "dataset_lifecycle": UNIVERSE_TIMELINE_LIFECYCLE,
                        TIMELINE_SUMMARY_KEY: timeline.stamp,
                        "content_hash": content_hash,
                        "first_step_at": _iso(timeline.steps[0].timestamp),
                        "last_step_at": _iso(last_step_at),
                        "periods": len(timeline.periods),
                        "steps": [
                            {
                                "timestamp": _iso(step.timestamp),
                                "outcome": step.outcome,
                                "universe_version_id": step.universe_version_id,
                            }
                            for step in timeline.steps
                        ],
                        "registry_stand_ins": REGISTRY_STAND_INS,
                    },
                )
            )
            with SorUnitOfWork(self._session) as uow:
                for path, value in files:
                    uow.checksums.upsert(
                        uow.checksums.build_row(
                            dataset_version=version_id,
                            object_type="parquet_file",
                            object_path=str(path),
                            checksum_algorithm="sha256",
                            checksum_value=value,
                        )
                    )
            self._session.flush()
        except Exception:
            # Never leave an unregistered version on disk: a publish step ships every
            # version directory it finds, registered or not.
            shutil.rmtree(root, ignore_errors=True)
            raise
        return version_id


def timeline_table(timeline: StoredTimeline) -> pa.Table:
    """One row per name per list, oldest list first, best rank first."""
    version_ids: list[str] = []
    starts: list[datetime] = []
    ends: list[datetime | None] = []
    symbols: list[str] = []
    ranks: list[int | None] = []
    for period in timeline.periods:
        for member in period.members:
            version_ids.append(period.universe_version_id)
            starts.append(period.effective_from)
            ends.append(period.effective_to)
            symbols.append(member.symbol)
            ranks.append(member.rank)
    return pa.table(
        {
            "universe_version_id": version_ids,
            "effective_from": starts,
            "effective_to": ends,
            "symbol": symbols,
            "rank": ranks,
        },
        schema=UNIVERSE_TIMELINE_SCHEMA,
    )


def timeline_content_hash(timeline: StoredTimeline) -> str:
    """sha256 over the rules, each step's time and outcome, and each list's dates and
    members. Universe version ids are left out: they are random per database, and two
    databases that hold the same timeline should give the same value."""
    payload = {
        "timeline": timeline.stamp,
        "steps": [[_iso(step.timestamp), step.outcome] for step in timeline.steps],
        "periods": [
            [
                _iso(period.effective_from),
                _iso(period.effective_to),
                [
                    [member.symbol, member.rank]
                    for member in sorted(period.members, key=lambda m: m.symbol)
                ],
            ]
            for period in timeline.periods
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stamp_of(record: Any) -> dict[str, Any]:
    found = (record.summary_json or {}).get(TIMELINE_SUMMARY_KEY)
    if not isinstance(found, dict) or not found:
        raise UniverseTimelineExportError(
            f"the step at {_iso(record.created_at)} carries no timeline rules; it was not "
            "written by a timeline build"
        )
    return dict(found)


def _window_start(stamp: dict[str, Any]) -> date:
    raw = stamp.get("window_start")
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise UniverseTimelineExportError(
            f"the timeline's first day is recorded as {raw!r}, not a date"
        ) from None


def _member_order(member: UniverseTimelineMember) -> tuple[bool, int, str]:
    return (member.rank is None, member.rank or 0, member.symbol)


def _aggregate_checksum(files: list[tuple[Path, str]]) -> str:
    aggregate = hashlib.sha256()
    for path, value in files:
        aggregate.update(f"{path.name}|{value}\n".encode())
    return aggregate.hexdigest()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    # A time read back without a zone is UTC; ``astimezone`` would take it for local time.
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()
