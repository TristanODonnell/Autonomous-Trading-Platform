from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Protocol

from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseVersion,
)
from autonomous_trading_platform.universe.types import (
    ExperimentUniverseScope,
    UniverseResolutionMode,
    UniverseTransition,
)


class UniverseResolutionError(RuntimeError):
    """Raised when universe resolution fails for an experiment."""


class ExperimentUniverseHistory(Protocol):
    def get_active_as_of(self, as_of: datetime) -> UniverseVersion | None: ...

    def build_replay_transitions(
        self, start: datetime, end: datetime
    ) -> list[UniverseTransition]: ...


class ExperimentUniverseVersionReader(Protocol):
    def get_by_version_id(self, version_id: str) -> UniverseVersion | None: ...

    def get_included_symbols(self, version_id: str) -> list[str]: ...


class ExperimentUniverseResolver:
    """
    Resolves universe scope for experiments based on resolution mode.

    Supported modes (in priority order):
    - ``universe_resolution_mode`` field on ExperimentDefinition (explicit)
    - ``universe_version`` string tags for backward-compatible inference:
        - ``"uvid:<id>"``  → FIXED_UNIVERSE_VERSION
        - ``"replay"``     → HISTORICAL_ROTATION_REPLAY
    - Non-empty ``symbols`` list → CUSTOM
    - Default: ACTIVE_AS_OF_START_DATE

    All modes produce a PIT-anchored ExperimentUniverseScope — no mode can
    resolve to the current live universe without an explicit timestamp anchor.
    """

    def __init__(
        self,
        history_service: ExperimentUniverseHistory,
        version_repo: ExperimentUniverseVersionReader,
    ) -> None:
        self._history = history_service
        self._versions = version_repo

    def resolve(
        self,
        experiment_plan: ExperimentDefinition,
        now_utc: datetime,
    ) -> ExperimentUniverseScope:
        mode = self._determine_mode(experiment_plan)

        if mode == UniverseResolutionMode.FIXED_UNIVERSE_VERSION:
            return self._resolve_fixed(experiment_plan, mode)
        if mode == UniverseResolutionMode.ACTIVE_AS_OF_START_DATE:
            return self._resolve_active_as_of_start(experiment_plan, mode)
        if mode == UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY:
            return self._resolve_replay(experiment_plan, mode)
        if mode == UniverseResolutionMode.CUSTOM:
            return self._resolve_custom(experiment_plan, mode)
        raise UniverseResolutionError(f"Unsupported universe resolution mode: {mode}")

    def _determine_mode(self, plan: ExperimentDefinition) -> UniverseResolutionMode:
        if plan.universe_resolution_mode is not None:
            return plan.universe_resolution_mode
        if plan.universe_version.startswith("uvid:"):
            return UniverseResolutionMode.FIXED_UNIVERSE_VERSION
        if plan.universe_version == "replay":
            return UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY
        if plan.symbols:
            return UniverseResolutionMode.CUSTOM
        return UniverseResolutionMode.ACTIVE_AS_OF_START_DATE

    def _resolve_fixed(
        self, plan: ExperimentDefinition, mode: UniverseResolutionMode
    ) -> ExperimentUniverseScope:
        version_id = (
            plan.universe_version.removeprefix("uvid:")
            if plan.universe_version.startswith("uvid:")
            else plan.universe_version
        )
        version = self._versions.get_by_version_id(version_id)
        if version is None:
            raise UniverseResolutionError(
                f"Fixed universe version '{version_id}' not found. "
                "Use 'universe history' to inspect available versions."
            )
        symbols = self._versions.get_included_symbols(version.universe_version_id)
        return ExperimentUniverseScope(
            mode=mode,
            universe_version_id=version.universe_version_id,
            effective_timestamp=version.effective_from,
            member_symbols=symbols,
            replay_rotation_enabled=False,
            replay_transitions=[],
            source=version.source,
            member_count=len(symbols),
        )

    def _resolve_active_as_of_start(
        self, plan: ExperimentDefinition, mode: UniverseResolutionMode
    ) -> ExperimentUniverseScope:
        as_of = datetime.combine(plan.start_date, time.min, tzinfo=UTC)
        version = self._history.get_active_as_of(as_of)
        if version is None:
            return ExperimentUniverseScope(
                mode=mode,
                universe_version_id=None,
                effective_timestamp=as_of,
                member_symbols=[],
                replay_rotation_enabled=False,
                replay_transitions=[],
                source=None,
                member_count=0,
            )
        symbols = self._versions.get_included_symbols(version.universe_version_id)
        return ExperimentUniverseScope(
            mode=mode,
            universe_version_id=version.universe_version_id,
            effective_timestamp=as_of,
            member_symbols=symbols,
            replay_rotation_enabled=False,
            replay_transitions=[],
            source=version.source,
            member_count=len(symbols),
        )

    def _resolve_replay(
        self, plan: ExperimentDefinition, mode: UniverseResolutionMode
    ) -> ExperimentUniverseScope:
        start_dt = datetime.combine(plan.start_date, time.min, tzinfo=UTC)
        end_dt = datetime.combine(plan.end_date, time.min, tzinfo=UTC)
        transitions = self._history.build_replay_transitions(start_dt, end_dt)
        if not transitions:
            raise UniverseResolutionError(
                f"HISTORICAL_ROTATION_REPLAY requested for "
                f"[{plan.start_date}, {plan.end_date}] but no active universe "
                "transitions were found in this window. "
                "Use 'universe replay-timeline' to inspect available history."
            )
        first = transitions[0]
        initial_symbols = sorted(set(first.retained_symbols) | set(first.added_symbols))
        return ExperimentUniverseScope(
            mode=mode,
            universe_version_id=first.universe_version_id,
            effective_timestamp=start_dt,
            member_symbols=initial_symbols,
            replay_rotation_enabled=True,
            replay_transitions=transitions,
            source="historical_rotation_replay",
            member_count=len(initial_symbols),
        )

    def _resolve_custom(
        self, plan: ExperimentDefinition, mode: UniverseResolutionMode
    ) -> ExperimentUniverseScope:
        if not plan.symbols:
            raise UniverseResolutionError(
                "CUSTOM universe resolution mode requires a non-empty symbols list "
                "on the ExperimentDefinition."
            )
        as_of = datetime.combine(plan.start_date, time.min, tzinfo=UTC)
        return ExperimentUniverseScope(
            mode=mode,
            universe_version_id=None,
            effective_timestamp=as_of,
            member_symbols=list(plan.symbols),
            replay_rotation_enabled=False,
            replay_transitions=[],
            source="custom",
            member_count=len(plan.symbols),
        )
