"""The replay features hook (plan 5d-F): a day with no bars (market holiday) is a
skip, not an error; any other failure still fails."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from autonomous_trading_platform.application.services.platform_replay.features_hooks import (
    run_features_at_timestamp,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext

_CYCLE = (
    "autonomous_trading_platform.application.services.platform_replay.features_hooks."
    "run_feature_pipeline_cycle"
)


def _context() -> PlatformReplayContext:
    return PlatformReplayContext(
        run_id=uuid4(),
        replay_id="replay-test",
        timestamp=datetime(2024, 5, 27, 20, tzinfo=UTC),
        symbols=["NVDA"],
        actor="test",
        dry_run=False,
    )


def test_a_day_without_bars_is_skipped_with_a_warning() -> None:
    with patch(_CYCLE, side_effect=ValueError("No bar data found for dataset_version_id=raw_v.")):
        result = run_features_at_timestamp(
            session=MagicMock(),
            timestamp=datetime(2024, 5, 27, 20, tzinfo=UTC),  # Memorial Day
            dataset_version_id="raw_v",
            symbols=["NVDA"],
            replay_context=_context(),
        )
    assert result.status == "skipped"
    assert result.errors == []
    assert result.warnings == ["No bars for 2024-05-27 — feature pipeline skipped"]
    assert result.summary == {"dataset_version_id": "raw_v", "date": "2024-05-27"}


def test_other_failures_still_fail() -> None:
    with patch(_CYCLE, side_effect=ValueError("schema mismatch")):
        result = run_features_at_timestamp(
            session=MagicMock(),
            timestamp=datetime(2024, 5, 28, 20, tzinfo=UTC),
            dataset_version_id="raw_v",
            symbols=["NVDA"],
            replay_context=_context(),
        )
    assert result.status == "failed"
    assert result.errors == ["schema mismatch"]
