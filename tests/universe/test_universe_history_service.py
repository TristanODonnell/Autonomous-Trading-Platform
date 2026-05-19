from __future__ import annotations

import uuid
from datetime import UTC, datetime

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.storage.sor.models.universe_rotation_records import (
    UniverseRotationRecord,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseVersion,
)
from autonomous_trading_platform.universe.services.universe_history_service import (
    UniverseHistoryService,
)

_T0 = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
_T2 = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
_T3 = datetime(2025, 4, 1, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _version(
    *,
    version_id: str | None = None,
    status: str = UniverseStatus.ACTIVE,
    effective_from: datetime = _T0,
    effective_to: datetime | None = None,
    source: str = UniverseSource.REBALANCE,
) -> UniverseVersion:
    return UniverseVersion(
        universe_version_id=version_id or str(uuid.uuid4()),
        name="test",
        source=source,
        created_at=_T0,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        rebalance_reason=None,
        config_hash="hash",
        generation_metadata_json=None,
    )


def _rotation_record(
    *,
    rotation_id: str | None = None,
    new_version_id: str = "ver-new",
    previous_version_id: str | None = None,
    created_at: datetime = _T1,
    added: list[str] | None = None,
    removed: list[str] | None = None,
) -> UniverseRotationRecord:
    return UniverseRotationRecord(
        rotation_id=rotation_id or str(uuid.uuid4()),
        rotation_type="scheduled",
        previous_universe_version_id=previous_version_id,
        candidate_universe_version_id=None,
        new_universe_version_id=new_version_id,
        rebalance_run_id=None,
        added_symbols_json=added or [],
        removed_symbols_json=removed or [],
        retained_symbols_json=[],
        churn_pct=0.0,
        force_rotation=False,
        rotation_reason=None,
        rollback_reason=None,
        approved_by=None,
        approved_at=None,
        approval_required=False,
        created_at=created_at,
        status="completed",
        rejection_reason=None,
        summary_json=None,
    )


class _StubVersionRepo:
    def __init__(
        self,
        active: UniverseVersion | None = None,
        window_versions: list[UniverseVersion] | None = None,
        symbols_by_id: dict[str, list[str]] | None = None,
    ) -> None:
        self._active = active
        self._window_versions = window_versions or []
        self._symbols_by_id: dict[str, list[str]] = symbols_by_id or {}

    def get_active_version(self, as_of: datetime) -> UniverseVersion | None:
        return self._active

    def get_active_versions_in_window(
        self, start: datetime, end: datetime
    ) -> list[UniverseVersion]:
        return self._window_versions

    def get_included_symbols(self, version_id: str) -> list[str]:
        return self._symbols_by_id.get(version_id, [])


class _StubRotationRepo:
    def __init__(self, records: list[UniverseRotationRecord] | None = None) -> None:
        self._records = records or []

    def get_in_window(self, start: datetime, end: datetime) -> list[UniverseRotationRecord]:
        return [r for r in self._records if start <= r.created_at < end]


def _make_service(
    *,
    active: UniverseVersion | None = None,
    window_versions: list[UniverseVersion] | None = None,
    symbols_by_id: dict[str, list[str]] | None = None,
    rotation_records: list[UniverseRotationRecord] | None = None,
) -> UniverseHistoryService:
    return UniverseHistoryService(
        version_repo=_StubVersionRepo(active, window_versions, symbols_by_id),
        rotation_repo=_StubRotationRepo(rotation_records),
    )


# ---------------------------------------------------------------------------
# get_active_as_of
# ---------------------------------------------------------------------------


def test_get_active_as_of_returns_version_from_repo():
    ver = _version(version_id="v1")
    svc = _make_service(active=ver)
    assert svc.get_active_as_of(_T0) is ver


def test_get_active_as_of_returns_none_when_no_active():
    svc = _make_service(active=None)
    assert svc.get_active_as_of(_T0) is None


# ---------------------------------------------------------------------------
# get_included_symbols_as_of
# ---------------------------------------------------------------------------


def test_get_included_symbols_as_of_returns_symbols():
    ver = _version(version_id="v1")
    svc = _make_service(active=ver, symbols_by_id={"v1": ["AAPL", "MSFT"]})
    assert svc.get_included_symbols_as_of(_T0) == ["AAPL", "MSFT"]


def test_get_included_symbols_as_of_returns_empty_when_no_active():
    svc = _make_service(active=None)
    assert svc.get_included_symbols_as_of(_T0) == []


# ---------------------------------------------------------------------------
# is_symbol_in_universe_as_of
# ---------------------------------------------------------------------------


def test_is_symbol_in_universe_as_of_true():
    ver = _version(version_id="v1")
    svc = _make_service(active=ver, symbols_by_id={"v1": ["AAPL", "MSFT"]})
    assert svc.is_symbol_in_universe_as_of("AAPL", _T0) is True


def test_is_symbol_in_universe_as_of_false():
    ver = _version(version_id="v1")
    svc = _make_service(active=ver, symbols_by_id={"v1": ["AAPL", "MSFT"]})
    assert svc.is_symbol_in_universe_as_of("GOOG", _T0) is False


def test_is_symbol_in_universe_as_of_false_when_no_active():
    svc = _make_service(active=None)
    assert svc.is_symbol_in_universe_as_of("AAPL", _T0) is False


# ---------------------------------------------------------------------------
# get_active_versions_in_window
# ---------------------------------------------------------------------------


def test_get_active_versions_in_window_delegates_to_repo():
    v1 = _version(version_id="v1", effective_from=_T0, effective_to=_T1)
    v2 = _version(version_id="v2", effective_from=_T1, effective_to=None)
    svc = _make_service(window_versions=[v1, v2])
    result = svc.get_active_versions_in_window(_T0, _T2)
    assert [v.universe_version_id for v in result] == ["v1", "v2"]


def test_get_active_versions_in_window_empty():
    svc = _make_service(window_versions=[])
    assert svc.get_active_versions_in_window(_T0, _T1) == []


# ---------------------------------------------------------------------------
# get_transitions_in_window
# ---------------------------------------------------------------------------


def test_get_transitions_in_window_filters_by_date():
    rec_early = _rotation_record(rotation_id="r1", created_at=_T0)
    rec_in_window = _rotation_record(rotation_id="r2", created_at=_T1)
    rec_late = _rotation_record(rotation_id="r3", created_at=_T2)
    svc = _make_service(rotation_records=[rec_early, rec_in_window, rec_late])
    result = svc.get_transitions_in_window(_T1, _T2)
    assert len(result) == 1
    assert result[0].rotation_id == "r2"


def test_get_transitions_in_window_empty():
    svc = _make_service(rotation_records=[])
    assert svc.get_transitions_in_window(_T0, _T1) == []


# ---------------------------------------------------------------------------
# reconstruct_timeline
# ---------------------------------------------------------------------------


def test_reconstruct_timeline_builds_entries_with_symbols():
    v1 = _version(version_id="v1", effective_from=_T0, effective_to=_T1)
    v2 = _version(version_id="v2", effective_from=_T1, effective_to=None)
    svc = _make_service(
        window_versions=[v1, v2],
        symbols_by_id={"v1": ["AAPL"], "v2": ["AAPL", "GOOG"]},
    )
    timeline = svc.reconstruct_timeline(_T0, _T2)
    assert len(timeline) == 2
    assert timeline[0].version.universe_version_id == "v1"
    assert timeline[0].symbols == ["AAPL"]
    assert timeline[1].symbols == ["AAPL", "GOOG"]


def test_reconstruct_timeline_empty_window():
    svc = _make_service(window_versions=[])
    assert svc.reconstruct_timeline(_T0, _T1) == []


# ---------------------------------------------------------------------------
# build_replay_transitions
# ---------------------------------------------------------------------------


def test_build_replay_transitions_first_entry_has_no_removed():
    v1 = _version(version_id="v1", effective_from=_T0, effective_to=_T1)
    svc = _make_service(
        window_versions=[v1],
        symbols_by_id={"v1": ["AAPL", "MSFT"]},
    )
    transitions = svc.build_replay_transitions(_T0, _T2)
    assert len(transitions) == 1
    t = transitions[0]
    assert t.universe_version_id == "v1"
    assert sorted(t.added_symbols) == ["AAPL", "MSFT"]
    assert t.removed_symbols == []
    assert t.retained_symbols == []


def test_build_replay_transitions_second_entry_shows_diff():
    v1 = _version(version_id="v1", effective_from=_T0, effective_to=_T1)
    v2 = _version(version_id="v2", effective_from=_T1, effective_to=None)
    svc = _make_service(
        window_versions=[v1, v2],
        symbols_by_id={"v1": ["AAPL", "MSFT"], "v2": ["AAPL", "GOOG"]},
    )
    transitions = svc.build_replay_transitions(_T0, _T2)
    assert len(transitions) == 2
    t2 = transitions[1]
    assert t2.universe_version_id == "v2"
    assert t2.added_symbols == ["GOOG"]
    assert t2.removed_symbols == ["MSFT"]
    assert t2.retained_symbols == ["AAPL"]


def test_build_replay_transitions_no_change_between_versions():
    v1 = _version(version_id="v1", effective_from=_T0, effective_to=_T1)
    v2 = _version(version_id="v2", effective_from=_T1, effective_to=None)
    same_symbols = ["AAPL", "MSFT", "GOOG"]
    svc = _make_service(
        window_versions=[v1, v2],
        symbols_by_id={"v1": same_symbols, "v2": same_symbols},
    )
    transitions = svc.build_replay_transitions(_T0, _T2)
    t2 = transitions[1]
    assert t2.added_symbols == []
    assert t2.removed_symbols == []
    assert sorted(t2.retained_symbols) == sorted(same_symbols)


def test_build_replay_transitions_returns_empty_for_empty_window():
    svc = _make_service(window_versions=[])
    assert svc.build_replay_transitions(_T0, _T1) == []
