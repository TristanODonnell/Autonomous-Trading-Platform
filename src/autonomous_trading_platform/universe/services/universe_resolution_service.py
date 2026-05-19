from __future__ import annotations

import time as time_module
from datetime import UTC, date, datetime, time
from typing import Protocol

from autonomous_trading_platform.contracts.common.enums import UniverseStatus
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    record_universe_active_size,
    record_universe_resolution_latency,
)
from autonomous_trading_platform.observability.runtime_context import get_runtime_context
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)


class NoActiveUniverseError(RuntimeError):
    """Raised when no ACTIVE universe version exists for the requested timestamp."""


logger = get_logger(__name__)


class UniverseVersionReader(Protocol):
    def get_active_version(self, as_of: datetime) -> UniverseVersion | None: ...

    def get_included_symbols(self, version_id: str) -> list[str]: ...

    def get_included_members(self, version_id: str) -> list[UniverseMember]: ...


class UniverseResolutionService:
    """
    Central service for deterministic universe resolution across all pipelines.

    All execution paths — trading, ingestion, features, simulations, replay —
    resolve symbol sets through this service to guarantee consistent, point-in-time
    universe state.
    """

    def __init__(self, repository: UniverseVersionReader) -> None:
        self._repo = repository

    def resolve_active(self, as_of: datetime) -> UniverseVersion:
        """Return the ACTIVE universe version for ``as_of``, or raise."""
        started = time_module.perf_counter()
        version = self._repo.get_active_version(as_of)
        record_universe_resolution_latency(time_module.perf_counter() - started)
        if version is None:
            self._log_resolution(as_of=as_of, version=None, status="missing")
            raise NoActiveUniverseError(
                f"No active universe version found for {as_of.isoformat()}. "
                "Run 'universe select-now' or 'universe seed' to create one."
            )
        self._log_resolution(as_of=as_of, version=version, status="resolved")
        return version

    def resolve_active_or_none(self, as_of: datetime) -> UniverseVersion | None:
        """Return the ACTIVE universe version for ``as_of``, or None."""
        return self._repo.get_active_version(as_of)

    def resolve_active_version_id(self, as_of: datetime) -> str | None:
        """Return the universe_version_id string for the active version, or None."""
        version = self._repo.get_active_version(as_of)
        return version.universe_version_id if version is not None else None

    def resolve_active_members(self, as_of: datetime) -> list[str]:
        """Return included symbol strings for the active universe at ``as_of``."""
        started = time_module.perf_counter()
        version = self._repo.get_active_version(as_of)
        if version is None:
            record_universe_resolution_latency(time_module.perf_counter() - started)
            self._log_resolution(as_of=as_of, version=None, status="missing")
            return []
        symbols = self._repo.get_included_symbols(version.universe_version_id)
        record_universe_active_size(len(symbols))
        record_universe_resolution_latency(time_module.perf_counter() - started)
        self._log_resolution(
            as_of=as_of,
            version=version,
            status="resolved",
            member_count=len(symbols),
        )
        return symbols

    def resolve_for_date(self, as_of: date) -> UniverseVersion:
        """Return the ACTIVE universe version for a calendar date (start of day UTC)."""
        as_of_dt = datetime.combine(as_of, time.min, tzinfo=UTC)
        return self.resolve_active(as_of_dt)

    def resolve_for_simulation_window(self, start: date, end: date) -> UniverseVersion:
        """
        Return the universe that was active at the start of a simulation window.

        Simulations must use a fixed universe version so results are reproducible.
        Using the start-of-window version is the canonical choice — it reflects
        what was known before the window began.
        """
        as_of_dt = datetime.combine(start, time.min, tzinfo=UTC)
        return self.resolve_active(as_of_dt)

    def resolve_member_count(self, as_of: datetime) -> int:
        """Return the count of included members in the active universe at ``as_of``."""
        version = self._repo.get_active_version(as_of)
        if version is None:
            return 0
        return len(self._repo.get_included_members(version.universe_version_id))

    def assert_active_universe_exists(self, as_of: datetime) -> None:
        """
        Raise ``NoActiveUniverseError`` if no active universe exists for ``as_of``.

        Call this at the start of any cycle that requires a valid active universe
        before execution can proceed (trading, ingestion, feature pipeline).
        """
        version = self._repo.get_active_version(as_of)
        if version is None:
            raise NoActiveUniverseError(
                f"No active universe version found for {as_of.isoformat()}. "
                "Universe must be initialised before execution cycles can run."
            )
        if version.status != UniverseStatus.ACTIVE:
            raise NoActiveUniverseError(
                f"Universe version '{version.universe_version_id}' has status "
                f"'{version.status}', expected 'active'."
            )

    def _log_resolution(
        self,
        *,
        as_of: datetime,
        version: UniverseVersion | None,
        status: str,
        member_count: int | None = None,
    ) -> None:
        ctx = get_runtime_context()
        logger.info(
            "active universe resolution",
            extra={
                **LogContext(
                    component="universe_resolution",
                    step="active_universe_resolution",
                    universe_version_id=version.universe_version_id if version else None,
                    active_universe_version_id=version.universe_version_id if version else None,
                    correlation_id=ctx.correlation_id if ctx else None,
                    job_run_id=ctx.job_run_id if ctx else None,
                ).to_extra(),
                "as_of": as_of.isoformat(),
                "status": status,
                "member_count": member_count,
            },
        )
