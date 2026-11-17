"""A failed universe step in a backtest is reported on its tick.

Every other step's errors were already copied onto the tick. The universe step kept
only its summary, so a run whose monthly rotation failed carried on with the universe
it had and finished ``ok`` with nothing to show for it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.platform_backtest_service import (
    PlatformBacktestInputs,
    PlatformBacktestRunner,
)
from autonomous_trading_platform.application.services.platform_replay import universe_hooks
from autonomous_trading_platform.contracts.runtime.platform_replay import UniverseReplayResult

_DAY = date(2024, 7, 10)
_OTHER_JOBS = (
    "ingestion",
    "corporate_actions",
    "features",
    "trading_cycle",
    "risk",
    "governance",
    "portfolio_snapshot",
    "research",
    "bench",
    "operations_health",
)


def _run_one_tick(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, *, status: str, errors: list[str]
) -> Any:
    # The runner builds Settings(), which wants broker keys in paper mode. No broker is
    # called; without these the tests would need a developer's local .env.
    monkeypatch.setenv("PAPER_BROKER_API_KEY", "test-key")
    monkeypatch.setenv("PAPER_BROKER_API_SECRET", "test-secret")

    def fake_universe_step(**kwargs: Any) -> UniverseReplayResult:
        return UniverseReplayResult(
            domain="universe",
            timestamp=kwargs["timestamp"],
            run_id=str(kwargs["replay_context"].run_id),
            status=status,
            errors=errors,
        )

    monkeypatch.setattr(universe_hooks, "run_universe_at_timestamp", fake_universe_step)
    inputs = PlatformBacktestInputs(
        symbols=["AAPL"],
        start_date=_DAY,
        end_date=_DAY,
        dry_run=True,
        scheduled_jobs_config={
            **{job: {"enabled": False} for job in _OTHER_JOBS},
            "universe": {"enabled": True, "cadence": "monthly"},
        },
    )
    return PlatformBacktestRunner(session_factory=lambda: db_session).run(inputs)


def test_a_failed_universe_step_fails_its_tick(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = "screener_refresh_failed: membership unavailable"

    artifact = _run_one_tick(db_session, monkeypatch, status="failed", errors=[error])

    assert error in artifact.errors
    assert artifact.runtime.ticks_attempted == 1
    assert artifact.runtime.ticks_failed == 1
    assert artifact.runtime.ticks_ok == 0


def test_a_clean_universe_step_leaves_the_tick_ok(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _run_one_tick(db_session, monkeypatch, status="ok", errors=[])

    assert artifact.errors == []
    assert artifact.runtime.ticks_failed == 0
    assert artifact.runtime.ticks_ok == 1
