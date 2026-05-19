from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from autonomous_trading_platform.contracts.common.enums import (
    PriceBasis,
    UniverseSource,
    UniverseStatus,
)
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import UniverseVersion
from autonomous_trading_platform.universe.services.experiment_universe_resolver import (
    ExperimentUniverseResolver,
    UniverseResolutionError,
)
from autonomous_trading_platform.universe.types import (
    ExperimentUniverseScope,
    UniverseResolutionMode,
    UniverseTransition,
)

_NOW = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
_START = date(2025, 1, 1)
_END = date(2025, 3, 1)
_START_DT = datetime.combine(_START, time.min, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_version(
    version_id: str = "ver-001",
    status: str = UniverseStatus.ACTIVE,
    effective_from: datetime = _START_DT,
    effective_to: datetime | None = None,
    source: str = UniverseSource.REBALANCE,
) -> UniverseVersion:
    return UniverseVersion(
        universe_version_id=version_id,
        name="test",
        source=source,
        created_at=_START_DT,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        rebalance_reason=None,
        config_hash="hash",
        generation_metadata_json=None,
    )


def _plan(
    *,
    universe_version: str = "v1",
    symbols: list[str] | None = None,
    universe_resolution_mode: UniverseResolutionMode | None = None,
    start: date = _START,
    end: date = _END,
) -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id="exp-001",
        experiment_type=ExperimentType.SWEEP,
        description=None,
        strategy_set=[],
        parameter_grid=[],
        dataset_version="v1",
        universe_version=universe_version,
        price_basis=PriceBasis.ADJUSTED,
        symbols=symbols or [],
        start_date=start,
        end_date=end,
        random_seed=42,
        universe_resolution_mode=universe_resolution_mode,
    )


class _StubHistory:
    def __init__(
        self,
        active: UniverseVersion | None = None,
        transitions: list[UniverseTransition] | None = None,
    ) -> None:
        self._active = active
        self._transitions = transitions or []

    def get_active_as_of(self, as_of: datetime) -> UniverseVersion | None:
        return self._active

    def build_replay_transitions(self, start: datetime, end: datetime) -> list[UniverseTransition]:
        return self._transitions


class _StubVersionRepo:
    def __init__(
        self,
        versions: dict[str, UniverseVersion] | None = None,
        symbols_by_id: dict[str, list[str]] | None = None,
    ) -> None:
        self._versions = versions or {}
        self._symbols_by_id = symbols_by_id or {}

    def get_by_version_id(self, version_id: str) -> UniverseVersion | None:
        return self._versions.get(version_id)

    def get_included_symbols(self, version_id: str) -> list[str]:
        return self._symbols_by_id.get(version_id, [])


def _resolver(
    *,
    active: UniverseVersion | None = None,
    versions: dict[str, UniverseVersion] | None = None,
    symbols_by_id: dict[str, list[str]] | None = None,
    transitions: list[UniverseTransition] | None = None,
) -> ExperimentUniverseResolver:
    return ExperimentUniverseResolver(
        history_service=_StubHistory(active=active, transitions=transitions),
        version_repo=_StubVersionRepo(versions=versions, symbols_by_id=symbols_by_id),
    )


# ---------------------------------------------------------------------------
# Mode inference
# ---------------------------------------------------------------------------


def test_mode_inferred_as_active_as_of_start_for_default():
    r = _resolver()
    plan = _plan(universe_version="v1")
    scope = r.resolve(plan, _NOW)
    assert scope.mode == UniverseResolutionMode.ACTIVE_AS_OF_START_DATE


def test_mode_inferred_as_fixed_from_uvid_prefix():
    ver = _make_version("ver-001")
    r = _resolver(versions={"ver-001": ver}, symbols_by_id={"ver-001": ["AAPL"]})
    plan = _plan(universe_version="uvid:ver-001")
    scope = r.resolve(plan, _NOW)
    assert scope.mode == UniverseResolutionMode.FIXED_UNIVERSE_VERSION


def test_mode_inferred_as_replay_from_replay_tag():
    t = UniverseTransition(
        effective_from=_START_DT,
        effective_to=None,
        universe_version_id="v1",
        added_symbols=["AAPL"],
        removed_symbols=[],
        retained_symbols=[],
    )
    r = _resolver(transitions=[t])
    plan = _plan(universe_version="replay")
    scope = r.resolve(plan, _NOW)
    assert scope.mode == UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY


def test_mode_inferred_as_custom_from_symbols():
    r = _resolver()
    plan = _plan(symbols=["AAPL", "MSFT"])
    scope = r.resolve(plan, _NOW)
    assert scope.mode == UniverseResolutionMode.CUSTOM


def test_explicit_mode_overrides_inference():
    ver = _make_version("ver-001")
    r = _resolver(versions={"ver-001": ver}, symbols_by_id={"ver-001": ["AAPL"]})
    plan = _plan(
        universe_version="ver-001",
        universe_resolution_mode=UniverseResolutionMode.FIXED_UNIVERSE_VERSION,
    )
    scope = r.resolve(plan, _NOW)
    assert scope.mode == UniverseResolutionMode.FIXED_UNIVERSE_VERSION


# ---------------------------------------------------------------------------
# FIXED_UNIVERSE_VERSION
# ---------------------------------------------------------------------------


def test_fixed_resolves_pinned_version():
    ver = _make_version("ver-pinned")
    r = _resolver(versions={"ver-pinned": ver}, symbols_by_id={"ver-pinned": ["AAPL", "MSFT"]})
    plan = _plan(universe_version="uvid:ver-pinned")
    scope = r.resolve(plan, _NOW)
    assert scope.universe_version_id == "ver-pinned"
    assert scope.member_symbols == ["AAPL", "MSFT"]
    assert scope.member_count == 2
    assert scope.replay_rotation_enabled is False
    assert scope.effective_timestamp == ver.effective_from


def test_fixed_raises_if_version_not_found():
    r = _resolver()
    plan = _plan(universe_version="uvid:nonexistent")
    with pytest.raises(UniverseResolutionError, match="nonexistent"):
        r.resolve(plan, _NOW)


# ---------------------------------------------------------------------------
# ACTIVE_AS_OF_START_DATE
# ---------------------------------------------------------------------------


def test_active_as_of_resolves_pit_version():
    ver = _make_version("ver-001")
    r = _resolver(active=ver, symbols_by_id={"ver-001": ["AAPL", "GOOG", "MSFT"]})
    plan = _plan()
    scope = r.resolve(plan, _NOW)
    assert scope.universe_version_id == "ver-001"
    assert scope.member_count == 3
    assert scope.replay_rotation_enabled is False
    assert scope.effective_timestamp == _START_DT


def test_active_as_of_returns_empty_scope_when_no_universe():
    r = _resolver(active=None)
    plan = _plan()
    scope = r.resolve(plan, _NOW)
    assert scope.universe_version_id is None
    assert scope.member_count == 0
    assert scope.member_symbols == []
    assert scope.is_empty is True


def test_active_as_of_anchors_effective_timestamp_to_start_date():
    ver = _make_version("ver-001")
    r = _resolver(active=ver, symbols_by_id={"ver-001": []})
    plan = _plan(start=date(2025, 4, 15))
    scope = r.resolve(plan, _NOW)
    expected = datetime.combine(date(2025, 4, 15), time.min, tzinfo=UTC)
    assert scope.effective_timestamp == expected


# ---------------------------------------------------------------------------
# HISTORICAL_ROTATION_REPLAY
# ---------------------------------------------------------------------------


def test_replay_builds_scope_with_transitions():
    t1 = UniverseTransition(
        effective_from=_START_DT,
        effective_to=datetime(2025, 2, 1, tzinfo=UTC),
        universe_version_id="v1",
        added_symbols=["AAPL", "MSFT"],
        removed_symbols=[],
        retained_symbols=[],
    )
    t2 = UniverseTransition(
        effective_from=datetime(2025, 2, 1, tzinfo=UTC),
        effective_to=None,
        universe_version_id="v2",
        added_symbols=["GOOG"],
        removed_symbols=["MSFT"],
        retained_symbols=["AAPL"],
    )
    r = _resolver(transitions=[t1, t2])
    plan = _plan(universe_version="replay")
    scope = r.resolve(plan, _NOW)
    assert scope.replay_rotation_enabled is True
    assert len(scope.replay_transitions) == 2
    assert scope.universe_version_id == "v1"
    assert sorted(scope.member_symbols) == ["AAPL", "MSFT"]


def test_replay_raises_when_no_transitions_found():
    r = _resolver(transitions=[])
    plan = _plan(universe_version="replay")
    with pytest.raises(UniverseResolutionError, match="no active universe transitions"):
        r.resolve(plan, _NOW)


# ---------------------------------------------------------------------------
# CUSTOM
# ---------------------------------------------------------------------------


def test_custom_uses_explicit_symbols():
    r = _resolver()
    plan = _plan(symbols=["SPY", "QQQ", "IWM"])
    scope = r.resolve(plan, _NOW)
    assert scope.mode == UniverseResolutionMode.CUSTOM
    assert sorted(scope.member_symbols) == ["IWM", "QQQ", "SPY"]
    assert scope.member_count == 3
    assert scope.replay_rotation_enabled is False
    assert scope.universe_version_id is None


def test_custom_raises_when_symbols_empty_and_mode_explicit():
    r = _resolver()
    plan = _plan(
        symbols=[],
        universe_resolution_mode=UniverseResolutionMode.CUSTOM,
    )
    with pytest.raises(UniverseResolutionError, match="non-empty symbols"):
        r.resolve(plan, _NOW)


# ---------------------------------------------------------------------------
# ExperimentUniverseScope helpers
# ---------------------------------------------------------------------------


def test_scope_is_empty_true_when_no_members_and_no_replay():
    scope = ExperimentUniverseScope(
        mode=UniverseResolutionMode.ACTIVE_AS_OF_START_DATE,
        universe_version_id=None,
        effective_timestamp=_START_DT,
        member_symbols=[],
        replay_rotation_enabled=False,
        replay_transitions=[],
        source=None,
        member_count=0,
    )
    assert scope.is_empty is True


def test_scope_is_empty_false_when_replay_enabled():
    scope = ExperimentUniverseScope(
        mode=UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY,
        universe_version_id="v1",
        effective_timestamp=_START_DT,
        member_symbols=[],
        replay_rotation_enabled=True,
        replay_transitions=[],
        source=None,
        member_count=0,
    )
    assert scope.is_empty is False
