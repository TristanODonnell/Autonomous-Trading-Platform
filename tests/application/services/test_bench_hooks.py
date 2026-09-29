"""Bench review replay hook and fixture wiring."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.bench_resimulation_service import (
    BenchWindow,
    ResimOutcome,
)
from autonomous_trading_platform.application.services.platform_replay.bench_hooks import (
    _run_portfolio_review,
    current_regime_label,
    run_bench_review_at_timestamp,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.platform.replay.platform_replay_config import (
    ALLOWED_JOB_NAMES,
)
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_review_repository import (
    PortfolioReviewRepository,
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


# ---------------------------------------------------------------------------
# Portfolio review after the bench review (portfolio rotation step 4E)
# ---------------------------------------------------------------------------


def _window() -> BenchWindow:
    return BenchWindow(
        dataset_version="raw_bars_test",
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=date(2024, 1, 2),
        end_date=_NOW.date(),
    )


def test_portfolio_review_is_skipped_while_its_mode_is_off(db_session: Session) -> None:
    summary = _run_portfolio_review(
        session=db_session,
        timestamp=_NOW,
        window=_window(),
        bench_review_id="bench_1",
        outcomes={},
        simulation_runner=object(),
    )

    assert summary is None
    assert PortfolioReviewRepository(db_session).recent_reviews() == []


def test_portfolio_review_runs_on_the_bench_resims(db_session: Session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {"portfolio_review_mode": "advisory"}, updated_by="test"
    )
    ActivePortfolioService(db_session).set_status(
        "b1", MembershipStatus.BENCH, "seed", actor="test", now=_NOW
    )
    outcome = ResimOutcome("b1", "momentum", score=Decimal("1.2"))

    summary = _run_portfolio_review(
        session=db_session,
        timestamp=_NOW,
        window=_window(),
        bench_review_id="bench_1",
        outcomes={"b1": outcome},
        simulation_runner=object(),  # no bar reader: no regime label
    )

    assert summary is not None
    assert summary["mode"] == "advisory"
    assert summary["regime"] is None
    assert summary["ranking"] == [{"strategy_id": "b1", "tier": "bench", "score": 1.2}]
    (review,) = PortfolioReviewRepository(db_session).recent_reviews()
    assert review.bench_review_id == "bench_1"
    assert review.window_start == date(2024, 1, 2)


def test_regime_label_is_none_without_a_bar_reader() -> None:
    assert current_regime_label(simulation_runner=object(), window=_window()) is None
