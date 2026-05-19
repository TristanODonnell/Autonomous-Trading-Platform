from __future__ import annotations

from datetime import datetime
from typing import NamedTuple, Protocol

from autonomous_trading_platform.storage.sor.models.universe_rotation_records import (
    UniverseRotationRecord,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import UniverseVersion
from autonomous_trading_platform.universe.types import UniverseTransition


class UniverseTimelineEntry(NamedTuple):
    effective_from: datetime
    effective_to: datetime | None
    version: UniverseVersion
    symbols: list[str]


class UniverseHistoryVersionReader(Protocol):
    def get_active_version(self, as_of: datetime) -> UniverseVersion | None: ...

    def get_included_symbols(self, version_id: str) -> list[str]: ...

    def get_active_versions_in_window(
        self, start: datetime, end: datetime
    ) -> list[UniverseVersion]: ...


class UniverseHistoryRotationReader(Protocol):
    def get_in_window(self, start: datetime, end: datetime) -> list[UniverseRotationRecord]: ...


class UniverseHistoryService:
    """
    Historical point-in-time universe lookup and timeline reconstruction.

    All queries are timestamp-anchored. No method touches the current live
    state without an explicit timestamp, preventing implicit survivorship bias.
    """

    def __init__(
        self,
        version_repo: UniverseHistoryVersionReader,
        rotation_repo: UniverseHistoryRotationReader,
    ) -> None:
        self._versions = version_repo
        self._rotations = rotation_repo

    def get_active_as_of(self, as_of: datetime) -> UniverseVersion | None:
        """Return the universe version that was active at ``as_of``."""
        return self._versions.get_active_version(as_of)

    def get_included_symbols_as_of(self, as_of: datetime) -> list[str]:
        """Return included symbol strings for the universe active at ``as_of``."""
        version = self._versions.get_active_version(as_of)
        if version is None:
            return []
        return self._versions.get_included_symbols(version.universe_version_id)

    def is_symbol_in_universe_as_of(self, symbol: str, as_of: datetime) -> bool:
        """Return True if ``symbol`` was an included member of the active universe at ``as_of``."""
        return symbol in self.get_included_symbols_as_of(as_of)

    def get_active_versions_in_window(
        self, start: datetime, end: datetime
    ) -> list[UniverseVersion]:
        """
        Return all versions (ACTIVE or RETIRED) whose effective range overlapped [start, end).

        Ordered chronologically by effective_from.
        """
        return self._versions.get_active_versions_in_window(start, end)

    def get_transitions_in_window(
        self, start: datetime, end: datetime
    ) -> list[UniverseRotationRecord]:
        """
        Return rotation records that occurred during [start, end), ordered chronologically.

        Useful for inspecting when universe changes happened within a simulation window.
        """
        return self._rotations.get_in_window(start, end)

    def reconstruct_timeline(self, start: datetime, end: datetime) -> list[UniverseTimelineEntry]:
        """
        Reconstruct the ordered sequence of active universe periods overlapping [start, end).

        Each entry captures the version and its resolved included symbols.
        Multiple entries indicate universe rotations during the window.
        """
        versions = self._versions.get_active_versions_in_window(start, end)
        entries: list[UniverseTimelineEntry] = []
        for version in versions:
            symbols = self._versions.get_included_symbols(version.universe_version_id)
            entries.append(
                UniverseTimelineEntry(
                    effective_from=version.effective_from,
                    effective_to=version.effective_to,
                    version=version,
                    symbols=symbols,
                )
            )
        return entries

    def build_replay_transitions(self, start: datetime, end: datetime) -> list[UniverseTransition]:
        """
        Build ordered UniverseTransition list for historical rotation replay mode.

        Each transition describes a universe rotation: what was added, removed, and retained
        relative to the previous active universe. The first entry has no previous universe,
        so added_symbols contains all initial members and removed/retained are empty.
        """
        timeline = self.reconstruct_timeline(start, end)
        transitions: list[UniverseTransition] = []

        for i, entry in enumerate(timeline):
            prev_symbols = set(timeline[i - 1].symbols) if i > 0 else set()
            curr_symbols = set(entry.symbols)

            transitions.append(
                UniverseTransition(
                    effective_from=entry.effective_from,
                    effective_to=entry.effective_to,
                    universe_version_id=entry.version.universe_version_id,
                    added_symbols=sorted(curr_symbols - prev_symbols),
                    removed_symbols=sorted(prev_symbols - curr_symbols),
                    retained_symbols=sorted(curr_symbols & prev_symbols),
                )
            )

        return transitions
