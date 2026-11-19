"""load_universe_timeline: an exported timeline read back by id and asked by date."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from autonomous_trading_platform.application.services.universe_timeline_loader import (
    UniverseTimelineLoadError,
    load_universe_timeline,
)
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.universe_timeline import UniverseTimeline
from autonomous_trading_platform.storage.parquet.repositories.parquet_universe_timeline_repository import (
    ParquetUniverseTimelineRepository,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from tests.application.services.test_universe_timeline_export_service import (
    _STAMP,
    _T_DEC,
    _T_JAN,
    _T_NOV,
    _T_START,
    _export,
    _seed_timeline,
    _stored,
)

_FIRST = frozenset({"AAA", "BBB", "CCC"})
_SECOND = frozenset({"AAA", "CCC", "DDD"})


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def exported(db_session: Session, base_path: Path) -> str:
    """A four-step timeline (two lists), exported; the version id."""
    _seed_timeline(db_session)
    version_id = _export(db_session, base_path).dataset_version_id
    assert version_id is not None
    return version_id


def _row(session: Session, version_id: str) -> DatasetVersions:
    row = DatasetVersionsRepository(session).get_by_dataset_version_id(version_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Read back
# ---------------------------------------------------------------------------


def test_an_exported_timeline_loads_as_what_was_exported(
    db_session: Session, base_path: Path
) -> None:
    _seed_timeline(db_session)
    result = _export(db_session, base_path)
    assert result.dataset_version_id is not None

    timeline = load_universe_timeline(db_session, result.dataset_version_id, base_path=base_path)

    assert timeline.dataset_version_id == result.dataset_version_id
    assert timeline.window_start == date(2024, 11, 18)
    assert timeline.rules == _STAMP
    assert timeline.content_hash == result.content_hash
    assert timeline.steps == result.steps
    assert timeline.periods == result.periods
    assert timeline.symbols_union == result.symbols_union == ["AAA", "BBB", "CCC", "DDD"]


@pytest.mark.parametrize(
    ("moment", "members"),
    [
        # Before the first step no list is in force.
        (_T_START - timedelta(seconds=1), frozenset()),
        (_T_START, _FIRST),
        # A month end with no change keeps the first list.
        (_T_NOV, _FIRST),
        (_T_DEC - timedelta(seconds=1), _FIRST),
        # The moment of a rotation belongs to the new list.
        (_T_DEC, _SECOND),
        (_T_JAN, _SECOND),
        # The last list is open: in force for as long as nothing replaces it.
        (datetime(2026, 11, 19, 14, 30, tzinfo=UTC), _SECOND),
    ],
)
def test_the_loaded_timeline_answers_which_names_were_members_at_a_moment(
    db_session: Session, base_path: Path, exported: str, moment: datetime, members: frozenset[str]
) -> None:
    timeline = load_universe_timeline(db_session, exported, base_path=base_path)

    assert timeline.members_at(moment) == members


def test_a_moment_is_asked_in_any_time_zone_but_not_without_one(
    db_session: Session, base_path: Path, exported: str
) -> None:
    timeline = load_universe_timeline(db_session, exported, base_path=base_path)

    # 2024-12-31 23:00 UTC, the rotation, written as 18:00 at UTC-5.
    eastern = datetime.fromisoformat("2024-12-31T18:00:00-05:00")
    assert timeline.members_at(eastern) == _SECOND
    assert timeline.members_at(eastern - timedelta(minutes=1)) == _FIRST

    with pytest.raises(ValueError, match="timezone-aware"):
        timeline.members_at(datetime(2024, 12, 31, 23, 0))


def test_a_timeline_with_a_gap_has_no_list_in_the_gap() -> None:
    # The export refuses a timeline with a gap. Should one be built by hand, the answer
    # inside the gap is "no list", never the list before it.
    stored = _stored(("v1", "v2"))
    gap_start = _T_DEC - timedelta(days=2)
    timeline = UniverseTimeline(
        dataset_version_id="tl-1",
        window_start=date(2024, 11, 18),
        content_hash="x",
        periods=[
            stored.periods[0].model_copy(update={"effective_to": gap_start}),
            stored.periods[1],
        ],
    )

    assert timeline.period_at(gap_start) is None
    assert timeline.members_at(gap_start + timedelta(days=1)) == frozenset()
    assert timeline.members_at(gap_start - timedelta(seconds=1)) == frozenset({"AAA"})


# ---------------------------------------------------------------------------
# Refused
# ---------------------------------------------------------------------------


def test_an_unknown_version_is_refused(db_session: Session, base_path: Path) -> None:
    with pytest.raises(UniverseTimelineLoadError, match="tl-missing is not registered"):
        load_universe_timeline(db_session, "tl-missing", base_path=base_path)


def test_a_bars_version_is_not_a_timeline(db_session: Session, base_path: Path) -> None:
    db_session.add(
        DatasetVersions(
            dataset_version_id="raw_bars_v1",
            dataset_name="raw_bars",
            created_at=datetime(2026, 11, 19, tzinfo=UTC),
            source="alpaca",
            price_basis=PriceBasis.RAW,
            interval=BarInterval.FIVE_MIN,
            schema_version="1.0.0",
            date_coverage_start=date(2024, 11, 18),
            date_coverage_end=date(2026, 10, 30),
            validation_status="validated",
            metadata_json={"dataset_lifecycle": "research_window"},
        )
    )
    db_session.flush()

    with pytest.raises(UniverseTimelineLoadError, match="is a raw_bars version, not a universe"):
        load_universe_timeline(db_session, "raw_bars_v1", base_path=base_path)


def _not_validated(session: Session, base_path: Path, version_id: str) -> None:
    _row(session, version_id).validation_status = "pending"
    session.flush()


def _no_content_hash(session: Session, base_path: Path, version_id: str) -> None:
    row = _row(session, version_id)
    metadata = dict(row.metadata_json or {})
    del metadata["content_hash"]
    row.metadata_json = metadata
    flag_modified(row, "metadata_json")
    session.flush()


def _an_unreadable_step(session: Session, base_path: Path, version_id: str) -> None:
    row = _row(session, version_id)
    metadata = dict(row.metadata_json or {})
    metadata["steps"] = [{"timestamp": "not a time", "outcome": "rotated"}]
    row.metadata_json = metadata
    flag_modified(row, "metadata_json")
    session.flush()


def _a_step_dropped_from_the_registration(
    session: Session, base_path: Path, version_id: str
) -> None:
    row = _row(session, version_id)
    metadata = dict(row.metadata_json or {})
    metadata["steps"] = list(metadata["steps"])[:-1]
    row.metadata_json = metadata
    flag_modified(row, "metadata_json")
    session.flush()


def _the_file_is_gone(session: Session, base_path: Path, version_id: str) -> None:
    shutil.rmtree(ParquetUniverseTimelineRepository(base_path=base_path).version_root(version_id))


def _the_file_holds_another_member(session: Session, base_path: Path, version_id: str) -> None:
    # The same lists and dates with one name swapped: a file from some other timeline.
    root = ParquetUniverseTimelineRepository(base_path=base_path).version_root(version_id)
    (path,) = list(root.rglob("*.parquet"))
    table = pq.read_table(path)
    symbols = ["ZZZ" if s == "DDD" else s for s in table.column("symbol").to_pylist()]
    index = table.schema.get_field_index("symbol")
    pq.write_table(table.set_column(index, table.schema.field("symbol"), [symbols]), path)


def _the_file_is_cut_short(session: Session, base_path: Path, version_id: str) -> None:
    root = ParquetUniverseTimelineRepository(base_path=base_path).version_root(version_id)
    (path,) = list(root.rglob("*.parquet"))
    pq.write_table(pq.read_table(path).slice(0, 4), path)


@pytest.mark.parametrize(
    ("damage", "says"),
    [
        (_not_validated, "is registered as 'pending', not validated"),
        (_no_content_hash, "without its rules, steps or content hash"),
        (_an_unreadable_step, "a step that cannot be read"),
        (_a_step_dropped_from_the_registration, "the file does not match its registration"),
        (_the_file_is_gone, "has no file under"),
        (_the_file_holds_another_member, "the file does not match its registration"),
        (_the_file_is_cut_short, "the file does not match its registration"),
    ],
)
def test_a_version_whose_row_and_file_do_not_agree_is_refused(
    db_session: Session,
    base_path: Path,
    exported: str,
    damage: Callable[[Session, Path, str], None],
    says: str,
) -> None:
    damage(db_session, base_path, exported)

    with pytest.raises(UniverseTimelineLoadError, match=says):
        load_universe_timeline(db_session, exported, base_path=base_path)
