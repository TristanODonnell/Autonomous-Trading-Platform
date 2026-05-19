from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from autonomous_trading_platform.universe.services.survivorship_guard import (
    SurvivorshipBiasError,
    SurvivorshipGuard,
)
from autonomous_trading_platform.universe.types import (
    ExperimentUniverseScope,
    UniverseResolutionMode,
    UniverseTransition,
)

_START = date(2025, 1, 1)
_END = date(2025, 3, 1)
_START_DT = datetime.combine(_START, time.min, tzinfo=UTC)


def _scope(
    *,
    mode: UniverseResolutionMode = UniverseResolutionMode.ACTIVE_AS_OF_START_DATE,
    version_id: str | None = "ver-001",
    effective_timestamp: datetime | None = _START_DT,
    member_symbols: list[str] | None = None,
    replay_rotation_enabled: bool = False,
    replay_transitions: list[UniverseTransition] | None = None,
    member_count: int = 10,
) -> ExperimentUniverseScope:
    return ExperimentUniverseScope(
        mode=mode,
        universe_version_id=version_id,
        effective_timestamp=effective_timestamp,
        member_symbols=member_symbols or ["AAPL"] * member_count,
        replay_rotation_enabled=replay_rotation_enabled,
        replay_transitions=replay_transitions or [],
        source="test",
        member_count=member_count,
    )


def _guard() -> SurvivorshipGuard:
    return SurvivorshipGuard()


# ---------------------------------------------------------------------------
# ACTIVE_AS_OF_START_DATE — safe configuration
# ---------------------------------------------------------------------------


def test_valid_active_as_of_scope_passes():
    _guard().validate_experiment_scope(_scope(), _START, _END)


# ---------------------------------------------------------------------------
# ACTIVE_AS_OF_START_DATE — no universe found
# ---------------------------------------------------------------------------


def test_active_as_of_with_no_version_raises():
    scope = _scope(version_id=None, member_count=0, member_symbols=[])
    with pytest.raises(SurvivorshipBiasError, match="no active universe version"):
        _guard().validate_experiment_scope(scope, _START, _END)


# ---------------------------------------------------------------------------
# FIXED_UNIVERSE_VERSION — always safe after resolver validation
# ---------------------------------------------------------------------------


def test_fixed_version_scope_passes():
    scope = _scope(mode=UniverseResolutionMode.FIXED_UNIVERSE_VERSION)
    _guard().validate_experiment_scope(scope, _START, _END)


# ---------------------------------------------------------------------------
# CUSTOM — always safe
# ---------------------------------------------------------------------------


def test_custom_scope_passes():
    scope = _scope(
        mode=UniverseResolutionMode.CUSTOM,
        version_id=None,
        member_symbols=["SPY", "QQQ"],
        member_count=2,
    )
    _guard().validate_experiment_scope(scope, _START, _END)


# ---------------------------------------------------------------------------
# HISTORICAL_ROTATION_REPLAY
# ---------------------------------------------------------------------------


def test_replay_scope_with_transitions_passes():
    t = UniverseTransition(
        effective_from=_START_DT,
        effective_to=None,
        universe_version_id="v1",
        added_symbols=["AAPL"],
        removed_symbols=[],
        retained_symbols=[],
    )
    scope = _scope(
        mode=UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY,
        replay_rotation_enabled=True,
        replay_transitions=[t],
        member_count=1,
    )
    _guard().validate_experiment_scope(scope, _START, _END)


def test_replay_scope_with_no_transitions_raises():
    scope = _scope(
        mode=UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY,
        replay_rotation_enabled=True,
        replay_transitions=[],
        member_count=0,
        member_symbols=[],
    )
    with pytest.raises(SurvivorshipBiasError, match="no transitions"):
        _guard().validate_experiment_scope(scope, _START, _END)


# ---------------------------------------------------------------------------
# Missing PIT anchor (bug condition)
# ---------------------------------------------------------------------------


def test_raises_when_version_id_set_but_no_timestamp():
    scope = _scope(
        mode=UniverseResolutionMode.FIXED_UNIVERSE_VERSION,
        version_id="ver-001",
        effective_timestamp=None,
    )
    with pytest.raises(SurvivorshipBiasError, match="no effective_timestamp"):
        _guard().validate_experiment_scope(scope, _START, _END)


def test_custom_mode_no_timestamp_does_not_raise():
    scope = _scope(
        mode=UniverseResolutionMode.CUSTOM,
        version_id=None,
        effective_timestamp=None,
        member_symbols=["SPY"],
        member_count=1,
    )
    _guard().validate_experiment_scope(scope, _START, _END)


# ---------------------------------------------------------------------------
# validate_symbols_not_future_leaked
# ---------------------------------------------------------------------------


def test_leaked_symbols_raises():
    guard = _guard()
    with pytest.raises(SurvivorshipBiasError, match="NEWCO"):
        guard.validate_symbols_not_future_leaked(
            symbols=["AAPL", "MSFT", "NEWCO"],
            universe_effective_timestamp=_START_DT,
            known_future_symbols={"NEWCO"},
        )


def test_no_leaked_symbols_passes():
    _guard().validate_symbols_not_future_leaked(
        symbols=["AAPL", "MSFT"],
        universe_effective_timestamp=_START_DT,
        known_future_symbols={"NEWCO", "IPO2025"},
    )


def test_empty_symbol_list_passes():
    _guard().validate_symbols_not_future_leaked(
        symbols=[],
        universe_effective_timestamp=_START_DT,
        known_future_symbols={"NEWCO"},
    )
