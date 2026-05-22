"""
Replay Runtime Service

CLASSIFICATION: integration_replay
    Thin wrapper over RuntimeReplayDebugRunner that records replay evidence
    with non-debug job naming ("runtime_replay" vs "runtime_replay_debug").

    Same execution engine and synthetic-price / synthetic-signal limitations as
    RuntimeReplayDebugRunner. Use for formally recorded replay verification runs
    (e.g. CI or scheduled verification pipelines) where you need job-tracking
    metadata without the debug marker.

    DO NOT use this path for strategy research or production decisions.
    See docs/architecture/research_execution_paths.md for the full classification.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.runtime.clock import MarketCalendar
from autonomous_trading_platform.runtime.replay_debug import (
    CycleHandler,
    RuntimeReplayDebugRunner,
    RuntimeReplayInputs,
    RuntimeReplaySettings,
)


class ReplayRuntimeService:
    """Replay runtime service that records non-debug runtime replay evidence."""

    def __init__(
        self,
        *,
        settings: RuntimeReplaySettings | None = None,
        session_factory: Callable[[], Session] = get_session,
        close_session: bool = True,
        calendar: MarketCalendar | None = None,
        cycle_handlers: dict[str, CycleHandler] | None = None,
    ) -> None:
        self._settings = settings or Settings()
        self._session_factory = session_factory
        self._close_session = close_session
        self._calendar = calendar
        self._cycle_handlers = cycle_handlers

    def run(self, inputs: RuntimeReplayInputs) -> dict[str, Any]:
        return RuntimeReplayDebugRunner(
            inputs=inputs,
            settings=self._settings,
            session_factory=self._session_factory,
            close_session=self._close_session,
            calendar=self._calendar,
            cycle_handlers=self._cycle_handlers,
            job_name_prefix="runtime_replay",
        ).run()
