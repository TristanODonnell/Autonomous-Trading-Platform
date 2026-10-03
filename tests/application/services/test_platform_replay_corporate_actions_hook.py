"""The backtest hook must run corporate-action ingestion for the replayed date and
the replay symbols, not for wall-clock "now" (plan 5d, finding F3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from autonomous_trading_platform.application.services.platform_replay import ingestion_hooks
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext

_CYCLE_TARGET = (
    "autonomous_trading_platform.scheduler.cycles.run_corporate_action_ingestion_cycle."
    "run_corporate_action_ingestion_cycle"
)


def _context(*, dry_run: bool = False) -> PlatformReplayContext:
    return PlatformReplayContext.create(
        symbols=["NVDA", "AAPL"],
        timestamp=datetime(2024, 6, 10, 21, 0, tzinfo=UTC),
        actor="platform-backtest",
        dry_run=dry_run,
    )


def test_hook_passes_replayed_date_and_symbols_to_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_cycle(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "dataset_version_id": "corporate_actions_test",
            "corporate_actions_dataset_version_id": "corporate_actions_test",
            "counts": {"fetched": 2, "created": 1, "manual_review": 0},
            "fetch_start": "2024-06-03",
            "fetch_end": "2024-07-10",
        }

    monkeypatch.setattr(_CYCLE_TARGET, fake_cycle)

    result = ingestion_hooks.run_corporate_actions_at_timestamp(
        session=cast(Any, object()),
        timestamp=datetime(2024, 6, 10, 21, 0, tzinfo=UTC),
        replay_context=_context(),
        source_dataset_version_id="raw_bars_test",
    )

    assert captured["as_of"] == datetime(2024, 6, 10).date()
    assert captured["fetch_symbols"] == ["NVDA", "AAPL"]
    assert captured["source_raw_bars_dataset_version_id"] == "raw_bars_test"
    assert captured["trigger_type"] == "platform_replay"
    assert result.status == "ok"
    assert result.dataset_version_id == "corporate_actions_test"
    assert result.summary["actions_created"] == 1
    assert result.summary["counts"]["fetched"] == 2
    assert "adjusted_bars_dataset_version_id" not in result.summary


def test_hook_reports_cycle_failure_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_cycle(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("alpaca down")

    monkeypatch.setattr(_CYCLE_TARGET, failing_cycle)

    result = ingestion_hooks.run_corporate_actions_at_timestamp(
        session=cast(Any, object()),
        timestamp=datetime(2024, 6, 10, 21, 0, tzinfo=UTC),
        replay_context=_context(),
        source_dataset_version_id="raw_bars_test",
    )

    assert result.status == "failed"
    assert result.errors == ["alpaca down"]


def test_hook_skips_the_cycle_on_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_cycle(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("cycle must not run on dry run")

    monkeypatch.setattr(_CYCLE_TARGET, unexpected_cycle)

    result = ingestion_hooks.run_corporate_actions_at_timestamp(
        session=cast(Any, object()),
        timestamp=datetime(2024, 6, 10, 21, 0, tzinfo=UTC),
        replay_context=_context(dry_run=True),
    )

    assert result.status == "dry_run"
