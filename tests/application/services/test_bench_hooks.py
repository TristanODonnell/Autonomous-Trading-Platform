"""Bench review replay hook and fixture wiring."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.platform_replay.bench_hooks import (
    run_bench_review_at_timestamp,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.platform.replay.platform_replay_config import (
    ALLOWED_JOB_NAMES,
)
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

_NOW = datetime(2024, 3, 4, 21, 0, tzinfo=UTC)


def _context(*, dry_run: bool = False) -> PlatformReplayContext:
    return PlatformReplayContext(
        run_id=uuid.uuid4(),
        replay_id="test",
        timestamp=_NOW,
        symbols=["AAPL"],
        actor="test",
        dry_run=dry_run,
    )


def test_skipped_while_bench_management_is_off(db_session: Session) -> None:
    result = run_bench_review_at_timestamp(
        session=db_session, timestamp=_NOW, replay_context=_context()
    )

    assert result.status == "skipped"
    assert result.summary == {"reason": "bench_management_disabled"}
    assert db_session.query(BenchEvaluationRow).count() == 0


def test_dry_run_does_nothing(db_session: Session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {"bench_management_enabled": True}, updated_by="test"
    )

    result = run_bench_review_at_timestamp(
        session=db_session, timestamp=_NOW, replay_context=_context(dry_run=True)
    )

    assert result.status == "dry_run"
    assert db_session.query(BenchEvaluationRow).count() == 0


def test_skipped_without_a_validated_dataset(db_session: Session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {"bench_management_enabled": True}, updated_by="test"
    )

    result = run_bench_review_at_timestamp(
        session=db_session, timestamp=_NOW, replay_context=_context()
    )

    assert result.status == "skipped"
    assert result.summary == {"reason": "insufficient_data_for_window"}


def test_bench_is_an_allowed_fixture_job() -> None:
    assert "bench" in ALLOWED_JOB_NAMES
