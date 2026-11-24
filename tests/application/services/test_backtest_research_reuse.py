"""A resumed job tells the research step to read back what it already simulated.

``worker run --resume`` keeps the database, so the simulations the first attempt
completed are still there (research plan R3). The word has to travel from the worker's
flag to the research step: worker → backtest command → backtest inputs → research hook.
Each hop is held here; a run that is not a resume must never reuse.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services import platform_backtest_service
from autonomous_trading_platform.application.services.platform_backtest_service import (
    PlatformBacktestInputs,
    PlatformBacktestRunner,
)
from autonomous_trading_platform.application.services.platform_replay import research_hooks
from autonomous_trading_platform.cli.commands import platform as platform_cli
from autonomous_trading_platform.contracts.runtime.platform_replay import ResearchReplayResult

_DAY = date(2024, 7, 10)
_NEXT_DAY = date(2024, 7, 11)
_OTHER_JOBS = (
    "ingestion",
    "corporate_actions",
    "features",
    "trading_cycle",
    "risk",
    "governance",
    "portfolio_snapshot",
    "universe",
    "bench",
    "operations_health",
)


def _research_step_kwargs(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, **inputs: Any
) -> dict[str, Any]:
    """Run one tick with research due and return what the research step was called with."""
    # The runner builds Settings(), which wants broker keys in paper mode. No broker is
    # called; without these the tests would need a developer's local .env.
    monkeypatch.setenv("PAPER_BROKER_API_KEY", "test-key")
    monkeypatch.setenv("PAPER_BROKER_API_SECRET", "test-secret")
    calls: list[dict[str, Any]] = []

    def fake_research_step(**kwargs: Any) -> ResearchReplayResult:
        calls.append(kwargs)
        return ResearchReplayResult(
            domain="research",
            timestamp=kwargs["timestamp"],
            run_id=str(kwargs["replay_context"].run_id),
            status="ok",
        )

    monkeypatch.setattr(research_hooks, "run_scheduled_research_at_timestamp", fake_research_step)
    PlatformBacktestRunner(session_factory=lambda: db_session).run(
        PlatformBacktestInputs(
            symbols=["AAPL"],
            start_date=_DAY,
            end_date=_DAY,
            dry_run=True,
            scheduled_jobs_config={
                **{job: {"enabled": False} for job in _OTHER_JOBS},
                "research": {"enabled": True, "cadence": "monthly", "options": {"profile": "x"}},
            },
            **inputs,
        )
    )
    (call,) = calls
    return call


def test_a_backtest_does_not_reuse_research_runs_unless_told_to(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _research_step_kwargs(db_session, monkeypatch)

    assert call["reuse_completed_runs"] is False
    assert call["research_options"] == {"profile": "x"}


def test_a_backtest_told_to_reuse_tells_the_research_step(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _research_step_kwargs(db_session, monkeypatch, reuse_completed_research_runs=True)

    assert call["reuse_completed_runs"] is True


def _backtest_command_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **flags: Any
) -> PlatformBacktestInputs:
    """Run the backtest command with the runner stubbed out; return the inputs it built."""
    seen: list[PlatformBacktestInputs] = []

    class _Stopped(Exception):
        pass

    def run(self: Any, inputs: PlatformBacktestInputs) -> Any:
        seen.append(inputs)
        raise _Stopped("stopped by the test once the inputs were built")

    monkeypatch.setattr(platform_backtest_service.PlatformBacktestRunner, "run", run)
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        fixture=None,
        symbols="AAPL",
        start=_DAY.isoformat(),
        end=_NEXT_DAY.isoformat(),
        starting_cash=None,
        random_seed=None,
        cadence_minutes=None,
        actor="test",
        dry_run=False,
        inject_failures=False,
        output=tmp_path / "bundle.json",
        no_progress=True,
        **flags,
    )
    # The command reports the stubbed runner's exception as a failed run.
    assert platform_cli.handle_backtest_run(args) == 1
    (inputs,) = seen
    return inputs


def test_the_backtest_command_passes_the_reuse_flag_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert (
        _backtest_command_inputs(
            monkeypatch, tmp_path, reuse_research_runs=True
        ).reuse_completed_research_runs
        is True
    )
    assert (
        _backtest_command_inputs(
            monkeypatch, tmp_path, reuse_research_runs=False
        ).reuse_completed_research_runs
        is False
    )
    # A caller that builds its own arguments and has never heard of the flag.
    assert _backtest_command_inputs(monkeypatch, tmp_path).reuse_completed_research_runs is False


def test_the_backtest_command_has_the_flag_and_it_is_off_by_default() -> None:
    parser = argparse.ArgumentParser()
    platform_cli.register(parser.add_subparsers(dest="command"))

    base = ["platform", "backtest", "run", "--symbols", "AAPL"]
    assert parser.parse_args(base).reuse_research_runs is False
    assert parser.parse_args([*base, "--reuse-research-runs"]).reuse_research_runs is True
