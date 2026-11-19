"""UniverseTimelineExportService: a stored timeline written as one checked dataset version.

Timelines are seeded here the way a build leaves them (dated versions, members, one
stamped rotation record per step) so each fault can be planted exactly. A timeline built
by the real rotation is exported in ``test_universe_timeline_service.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services import (
    universe_timeline_export_service as export_mod,
)
from autonomous_trading_platform.application.services.universe_timeline_export_service import (
    REGISTRY_STAND_INS,
    StoredTimeline,
    UniverseTimelineExportService,
    timeline_content_hash,
)
from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
    UniverseStatus,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    TIMELINE_ROTATION_REASON,
    TIMELINE_SUMMARY_KEY,
    UNIVERSE_TIMELINE_LIFECYCLE,
    UNIVERSE_TIMELINE_SOURCE,
    UniverseTimelineExportResult,
    UniverseTimelineExportStep,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    RAW_BARS_DATASET,
    UNIVERSE_TIMELINE_DATASET,
)
from autonomous_trading_platform.storage.sor.models.checksums import Checksums
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.universe_rotation_records import (
    UniverseRotationRecord,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)

_NOW = datetime(2026, 11, 19, 20, tzinfo=UTC)
_T_START = datetime(2024, 11, 18, 0, 0, tzinfo=UTC)
_T_NOV = datetime(2024, 11, 29, 23, 0, tzinfo=UTC)
_T_DEC = datetime(2024, 12, 31, 23, 0, tzinfo=UTC)
_T_JAN = datetime(2025, 1, 31, 23, 0, tzinfo=UTC)
_STAMP: dict[str, Any] = {
    "window_start": "2024-11-18",
    "screener_source": "sp500_point_in_time",
    "candidate_market_data": "alpaca_daily",
    "min_intraday_bar_coverage": 0.7,
}
_FIRST = ["AAA", "BBB", "CCC"]
_SECOND = ["AAA", "DDD", "CCC"]


def _record(
    session: Session,
    when: datetime,
    status: str,
    *,
    new: str,
    previous: str | None = None,
    stamp: dict[str, Any] | None = _STAMP,
) -> UniverseRotationRecord:
    record = UniverseRotationRecord(
        rotation_id=str(uuid.uuid4()),
        rotation_type="scheduled",
        previous_universe_version_id=previous,
        new_universe_version_id=new,
        force_rotation=False,
        rotation_reason=TIMELINE_ROTATION_REASON,
        approval_required=False,
        created_at=when,
        status=status,
        summary_json={"churn_pct": 0.0, **({TIMELINE_SUMMARY_KEY: stamp} if stamp else {})},
    )
    session.add(record)
    session.flush()
    return record


def _rotated(
    session: Session,
    when: datetime,
    symbols: list[str],
    *,
    removed: tuple[str, ...] = (),
    stamp: dict[str, Any] | None = _STAMP,
) -> str:
    """A step that put a new list in force, as the rotation does: the list before it is
    retired at the step's time and the new one starts there."""
    repo = UniverseVersionRepository(session)
    previous = repo.retire_active_version(when)
    session.flush()
    version_id = f"list-{when:%Y%m%d}"
    repo.insert_version(
        UniverseVersion(
            universe_version_id=version_id,
            name=version_id,
            source="rebalance",
            created_at=when,
            effective_from=when,
            effective_to=None,
            status=UniverseStatus.CANDIDATE,
            config_hash=version_id,
        )
    )
    session.flush()
    members = [
        # Ranks come from the candidate pool, so they need not run from 1 without gaps.
        UniverseMember(
            universe_version_id=version_id, symbol=symbol, rank=rank * 2, included_reason="test"
        )
        for rank, symbol in enumerate(symbols, start=1)
    ] + [
        UniverseMember(
            universe_version_id=version_id, symbol=symbol, excluded_reason="removed_by_rebalance"
        )
        for symbol in removed
    ]
    repo.insert_members(members)
    session.flush()
    repo.activate_version(version_id)
    session.flush()
    _record(
        session,
        when,
        "completed",
        new=version_id,
        previous=previous.universe_version_id if previous is not None else None,
        stamp=stamp,
    )
    return version_id


def _unchanged(session: Session, when: datetime, in_force: str, **kwargs: Any) -> None:
    _record(session, when, "skipped", new=in_force, previous=in_force, **kwargs)


def _seed_timeline(session: Session) -> tuple[str, str]:
    """Four steps: a first list, a month with no change, a new list, no change again."""
    first = _rotated(session, _T_START, _FIRST)
    _unchanged(session, _T_NOV, first)
    second = _rotated(session, _T_DEC, _SECOND, removed=("BBB",))
    _unchanged(session, _T_JAN, second)
    return first, second


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _export(
    session: Session, base_path: Path, *, plan_only: bool = False
) -> UniverseTimelineExportResult:
    return UniverseTimelineExportService(session, base_path=base_path).export(
        now_utc=_NOW, plan_only=plan_only
    )


def _version_dirs(base_path: Path) -> list[Path]:
    return sorted((base_path / "universe_timeline").glob("dataset_version=*"))


def _registry_rows(session: Session) -> list[DatasetVersions]:
    return list(session.execute(select(DatasetVersions)).scalars().all())


def _read(base_path: Path, version_id: str) -> pa.Table:
    root = base_path / "universe_timeline" / f"dataset_version={version_id}"
    return ds.dataset(str(root), format="parquet").to_table()


# ---------------------------------------------------------------------------
# What is written
# ---------------------------------------------------------------------------


def test_export_writes_one_row_per_name_per_list_in_one_file(
    db_session: Session, base_path: Path
) -> None:
    first, second = _seed_timeline(db_session)

    result = _export(db_session, base_path)

    assert result.status == "created"
    assert result.errors == []
    assert result.rows == 6
    assert result.symbols_union == ["AAA", "BBB", "CCC", "DDD"]
    assert [(p.universe_version_id, p.effective_from, p.effective_to) for p in result.periods] == [
        (first, _T_START, _T_DEC),
        (second, _T_DEC, None),
    ]

    (root,) = _version_dirs(base_path)
    assert root.name == f"dataset_version={result.dataset_version_id}"
    assert len(list(root.rglob("*.parquet"))) == 1

    assert result.dataset_version_id is not None
    rows = _read(base_path, result.dataset_version_id).to_pylist()
    assert rows == [
        {
            "universe_version_id": first,
            "effective_from": _T_START,
            "effective_to": _T_DEC,
            "symbol": symbol,
            "rank": rank,
        }
        for symbol, rank in (("AAA", 2), ("BBB", 4), ("CCC", 6))
    ] + [
        {
            "universe_version_id": second,
            "effective_from": _T_DEC,
            "effective_to": None,
            "symbol": symbol,
            "rank": rank,
        }
        for symbol, rank in (("AAA", 2), ("DDD", 4), ("CCC", 6))
    ]


def test_a_name_removed_from_a_list_is_not_exported(db_session: Session, base_path: Path) -> None:
    # The second list still holds a row for BBB, marked as removed by the rotation.
    _seed_timeline(db_session)

    result = _export(db_session, base_path)

    assert result.periods[1].symbols == ["AAA", "CCC", "DDD"]


def test_a_month_with_no_change_is_a_step_but_adds_no_rows(
    db_session: Session, base_path: Path
) -> None:
    first, second = _seed_timeline(db_session)

    result = _export(db_session, base_path)

    assert [(s.timestamp, s.outcome, s.universe_version_id) for s in result.steps] == [
        (_T_START, "rotated", first),
        (_T_NOV, "unchanged", first),
        (_T_DEC, "rotated", second),
        (_T_JAN, "unchanged", second),
    ]
    assert (result.first_step_at, result.last_step_at) == (_T_START, _T_JAN)
    assert len(result.periods) == 2


def test_a_refused_month_is_a_step_and_keeps_the_list_in_force(
    db_session: Session, base_path: Path
) -> None:
    first = _rotated(db_session, _T_START, _FIRST)
    _record(db_session, _T_NOV, "rejected", new=first, previous=first)

    result = _export(db_session, base_path)

    assert result.status == "created"
    assert [s.outcome for s in result.steps] == ["rotated", "refused"]
    assert len(result.periods) == 1


def test_the_version_is_registered_as_a_timeline_with_its_rules(
    db_session: Session, base_path: Path
) -> None:
    _seed_timeline(db_session)

    result = _export(db_session, base_path)

    (row,) = _registry_rows(db_session)
    assert row.dataset_version_id == result.dataset_version_id
    assert row.dataset_name == "universe_timeline"
    assert row.source == UNIVERSE_TIMELINE_SOURCE
    assert row.validation_status == "validated"
    assert row.schema_version == UNIVERSE_TIMELINE_DATASET.schema_version
    assert row.symbol_coverage == 4
    assert (row.date_coverage_start, row.date_coverage_end) == (
        date(2024, 11, 18),
        date(2025, 1, 31),
    )
    assert row.checksum
    assert row.source_manifest == {
        "pipeline": UNIVERSE_TIMELINE_SOURCE,
        "symbols": ["AAA", "BBB", "CCC", "DDD"],
        TIMELINE_SUMMARY_KEY: _STAMP,
    }
    meta = row.metadata_json or {}
    assert meta["dataset_lifecycle"] == UNIVERSE_TIMELINE_LIFECYCLE
    assert meta[TIMELINE_SUMMARY_KEY] == _STAMP
    assert meta["content_hash"] == result.content_hash
    assert (meta["first_step_at"], meta["last_step_at"]) == (
        "2024-11-18T00:00:00+00:00",
        "2025-01-31T23:00:00+00:00",
    )
    assert meta["periods"] == 2
    assert [step["outcome"] for step in meta["steps"]] == [
        "rotated",
        "unchanged",
        "rotated",
        "unchanged",
    ]
    # The registry requires a price basis and an interval; a timeline has neither.
    assert (row.price_basis, row.interval) == (PriceBasis.RAW, BarInterval.ONE_DAY)
    assert meta["registry_stand_ins"] == REGISTRY_STAND_INS == ["price_basis", "interval"]

    checksums = list(db_session.execute(select(Checksums)).scalars().all())
    assert [c.dataset_version for c in checksums] == [result.dataset_version_id]


def test_an_exported_timeline_is_never_the_newest_raw_bars_version(
    db_session: Session, base_path: Path
) -> None:
    # Readers that are not handed a bars version fall back to the newest validated one,
    # looked up by dataset name. A timeline must not be an answer to that question.
    _seed_timeline(db_session)
    _export(db_session, base_path)

    repo = DatasetVersionsRepository(db_session)
    for basis in PriceBasis:
        assert (
            repo.get_latest_validated(dataset_name=RAW_BARS_DATASET.dataset_key, price_basis=basis)
            is None
        )


# ---------------------------------------------------------------------------
# Written once per timeline
# ---------------------------------------------------------------------------


def test_a_second_export_of_the_same_timeline_writes_nothing(
    db_session: Session, base_path: Path
) -> None:
    _seed_timeline(db_session)
    first = _export(db_session, base_path)

    second = _export(db_session, base_path)

    assert second.status == "up_to_date"
    assert second.dataset_version_id == first.dataset_version_id
    assert second.content_hash == first.content_hash
    assert len(_version_dirs(base_path)) == 1
    assert len(_registry_rows(db_session)) == 1


def test_a_timeline_extended_by_a_month_is_a_new_version(
    db_session: Session, base_path: Path
) -> None:
    _, second = _seed_timeline(db_session)
    before = _export(db_session, base_path)

    # One more month end, no change: the same lists, valid for a month longer.
    _unchanged(db_session, datetime(2025, 2, 28, 23, 0, tzinfo=UTC), second)
    after = _export(db_session, base_path)

    assert after.status == "created"
    assert after.dataset_version_id != before.dataset_version_id
    assert after.content_hash != before.content_hash
    assert [p.symbols for p in after.periods] == [p.symbols for p in before.periods]
    assert len(_version_dirs(base_path)) == 2
    # The older version is left as it was: versions are immutable.
    assert {row.dataset_version_id for row in _registry_rows(db_session)} == {
        before.dataset_version_id,
        after.dataset_version_id,
    }


def test_plan_reports_the_timeline_and_writes_nothing(db_session: Session, base_path: Path) -> None:
    _seed_timeline(db_session)

    result = _export(db_session, base_path, plan_only=True)

    assert result.status == "planned"
    assert result.dataset_version_id is None
    assert result.rows == 6
    assert result.content_hash
    assert _version_dirs(base_path) == []
    assert _registry_rows(db_session) == []


def test_a_failed_registration_leaves_no_version_on_disk(
    db_session: Session, base_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_timeline(db_session)

    def fail(self: Any, dataset_version: Any) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(export_mod.DatasetRegistrationService, "register", fail)

    with pytest.raises(RuntimeError, match="database unavailable"):
        _export(db_session, base_path)

    assert _version_dirs(base_path) == []


# ---------------------------------------------------------------------------
# The content hash
# ---------------------------------------------------------------------------


def _stored(
    version_ids: tuple[str, str], *, second_symbols: tuple[str, ...] = ("AAA", "DDD")
) -> StoredTimeline:
    first, second = version_ids
    return StoredTimeline(
        stamp=dict(_STAMP),
        window_start=date(2024, 11, 18),
        steps=[
            UniverseTimelineExportStep(
                timestamp=_T_START, outcome="rotated", universe_version_id=first
            ),
            UniverseTimelineExportStep(
                timestamp=_T_DEC, outcome="rotated", universe_version_id=second
            ),
        ],
        periods=[
            UniverseTimelinePeriod(
                universe_version_id=first,
                effective_from=_T_START,
                effective_to=_T_DEC,
                members=[UniverseTimelineMember(symbol="AAA", rank=1)],
            ),
            UniverseTimelinePeriod(
                universe_version_id=second,
                effective_from=_T_DEC,
                members=[
                    UniverseTimelineMember(symbol=symbol, rank=rank)
                    for rank, symbol in enumerate(second_symbols, start=1)
                ],
            ),
        ],
    )


def test_the_content_hash_is_the_same_for_the_same_timeline_in_two_databases() -> None:
    # Universe version ids are random per database; the lists and dates are not.
    assert timeline_content_hash(_stored(("a1", "a2"))) == timeline_content_hash(
        _stored(("b1", "b2"))
    )


def test_the_content_hash_changes_with_a_member_a_date_or_a_rule() -> None:
    base = _stored(("a1", "a2"))
    reference = timeline_content_hash(base)

    other_member = _stored(("a1", "a2"), second_symbols=("AAA", "EEE"))
    assert timeline_content_hash(other_member) != reference

    later = _stored(("a1", "a2"))
    later.periods[1] = later.periods[1].model_copy(
        update={"effective_from": _T_DEC + timedelta(days=1)}
    )
    assert timeline_content_hash(later) != reference

    other_rule = _stored(("a1", "a2"))
    other_rule.stamp["min_intraday_bar_coverage"] = 0.6
    assert timeline_content_hash(other_rule) != reference


# ---------------------------------------------------------------------------
# Refused: the stored timeline does not hold together
# ---------------------------------------------------------------------------


def _version(session: Session, version_id: str) -> UniverseVersion:
    version = UniverseVersionRepository(session).get_by_version_id(version_id)
    assert version is not None
    return version


def _no_timeline(session: Session) -> None:
    return None


def _a_step_under_other_rules(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    _unchanged(session, _T_NOV, first, stamp={**_STAMP, "min_intraday_bar_coverage": 0.6})


def _a_step_with_no_rules(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    _unchanged(session, _T_NOV, first, stamp=None)


def _first_step_after_the_first_day(session: Session) -> None:
    # The stamp says the timeline starts on 2024-11-18; the first stored step is later.
    _rotated(session, _T_NOV, _FIRST)


def _first_step_picked_nothing(session: Session) -> None:
    _record(session, _T_START, "skipped", new="list-missing")


def _a_list_that_is_not_in_the_database(session: Session) -> None:
    _rotated(session, _T_START, _FIRST)
    _record(session, _T_DEC, "completed", new="list-missing")


def _a_list_starting_after_its_step(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    _version(session, first).effective_from = _T_START + timedelta(hours=1)
    session.flush()


def _a_gap_between_two_lists(session: Session) -> None:
    first, _ = _seed_timeline(session)
    _version(session, first).effective_to = _T_DEC - timedelta(days=1)
    session.flush()


def _an_overlap_between_two_lists(session: Session) -> None:
    first, _ = _seed_timeline(session)
    _version(session, first).effective_to = _T_DEC + timedelta(days=1)
    session.flush()


def _an_earlier_list_left_open(session: Session) -> None:
    first, _ = _seed_timeline(session)
    _version(session, first).effective_to = None
    session.flush()


def _the_last_list_retired_by_something_else(session: Session) -> None:
    _, second = _seed_timeline(session)
    _version(session, second).effective_to = _T_JAN + timedelta(days=10)
    session.flush()


def _a_no_change_step_naming_another_list(session: Session) -> None:
    first, _ = _seed_timeline(session)
    _unchanged(session, datetime(2025, 2, 28, 23, 0, tzinfo=UTC), first)


def _a_list_with_no_members(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    for member in UniverseVersionRepository(session).get_members(first):
        member.excluded_reason = "removed_by_rebalance"
    session.flush()


def _a_list_that_was_never_in_force(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    _version(session, first).status = UniverseStatus.CANDIDATE
    session.flush()


def _an_unknown_step_status(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    _record(session, _T_NOV, "rolled_back", new=first, previous=first)


def _two_steps_at_one_time(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    _unchanged(session, _T_NOV, first)
    _unchanged(session, _T_NOV, first)


def _one_list_named_new_by_two_steps(session: Session) -> None:
    first = _rotated(session, _T_START, _FIRST)
    _record(session, _T_NOV, "completed", new=first, previous=first)


@pytest.mark.parametrize(
    ("plant", "says"),
    [
        (_no_timeline, "holds no universe timeline"),
        (_a_step_under_other_rules, "one timeline has one first day and one set of rules"),
        (_a_step_with_no_rules, "carries no timeline rules"),
        (_first_step_after_the_first_day, "the steps before it are missing"),
        (_first_step_picked_nothing, "picked no list"),
        (_a_list_that_is_not_in_the_database, "is not in this database"),
        (_a_list_starting_after_its_step, "its step ran at"),
        (_a_gap_between_two_lists, "a gap in the timeline"),
        (_an_overlap_between_two_lists, "an overlap in the timeline"),
        (_an_earlier_list_left_open, "an overlap in the timeline"),
        (_the_last_list_retired_by_something_else, "something outside the timeline retired it"),
        (_a_no_change_step_naming_another_list, "the list in force then was"),
        (_a_list_with_no_members, "has no members"),
        (_a_list_that_was_never_in_force, "is active or retired"),
        (_an_unknown_step_status, "has status 'rolled_back'"),
        (_two_steps_at_one_time, "two timeline records for"),
        (_one_list_named_new_by_two_steps, "and so does an earlier step"),
    ],
)
def test_a_timeline_that_does_not_hold_together_is_refused_and_nothing_is_written(
    db_session: Session,
    base_path: Path,
    plant: Callable[[Session], None],
    says: str,
) -> None:
    plant(db_session)

    result = _export(db_session, base_path)

    assert result.status == "refused"
    assert len(result.errors) == 1
    assert says in " ".join(result.errors[0].split())
    assert result.dataset_version_id is None
    assert _version_dirs(base_path) == []
    assert _registry_rows(db_session) == []
