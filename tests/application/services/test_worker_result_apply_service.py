from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.bench_resimulation_service import (
    ResimOutcome,
)
from autonomous_trading_platform.application.services.worker_result_apply_service import (
    WorkerResultApplyService,
    outcomes_to_payload,
    payload_to_outcomes,
)
from autonomous_trading_platform.application.services.worker_result_gate_service import (
    WorkerResultGateService,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    PortfolioReviewMode,
)
from autonomous_trading_platform.contracts.governance.worker_results import (
    BenchResimsPayload,
    WorkerResultSubmission,
    WorkerResultType,
)
from autonomous_trading_platform.storage.sor.models.portfolio_reviews import PortfolioReviewRow
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.worker_result_submissions_repository import (
    WorkerResultSubmissionsRepository,
)
from tests.application.services import test_portfolio_review_service as review_tests

NOW = datetime(2026, 10, 19, 22, 30, tzinfo=UTC)  # Monday night: the week's first session
WINDOW = (date(2026, 7, 20), date(2026, 10, 16))


def _outcomes() -> dict[str, ResimOutcome]:
    idx = pd.to_datetime(["2026-10-14", "2026-10-15", "2026-10-16"])
    return {
        "a1": ResimOutcome(
            "a1",
            "momentum",
            run_id=uuid4(),
            trade_count=12,
            total_return=0.04,
            sharpe_ratio=1.3,
            max_drawdown=0.05,
            win_rate=0.55,
            score=Decimal("1.25"),
            daily_turnover=0.2,
            daily_returns=pd.Series([0.01, -0.002, 0.004], index=idx),
        ),
        "c": ResimOutcome("c", "mean_reversion", error="missing_strategy_config"),
    }


def _accept(
    session: Session, *, run_id: str = "w1", as_of: date = date(2026, 10, 16), max_age: int = 3
) -> None:
    OperatorSettingsRepository(session).update_current(
        {
            "worker_results_enabled": True,
            "worker_result_require_matching_git_sha": False,
            "worker_result_max_age_days": max_age,
        },
        updated_by="test",
    )
    payload = outcomes_to_payload(
        _outcomes(),
        window_start=WINDOW[0],
        window_end=WINDOW[1],
        dataset_version="raw_bars_20261016",
        market_returns=pd.Series(
            [0.002, 0.001], index=pd.to_datetime(["2026-10-15", "2026-10-16"])
        ),
    )
    decision = WorkerResultGateService(session, local_git_sha=None).evaluate(
        WorkerResultSubmission(
            run_id=run_id,
            result_type=WorkerResultType.BENCH_RESIMS,
            produced_at=NOW,
            git_sha="abc1234",
            dataset_as_of=as_of,
            payload=payload.model_dump(mode="json"),
        ),
        now=NOW,
    )
    assert decision.status.value == "accepted", decision


def test_payload_round_trip_keeps_what_the_review_uses() -> None:
    payload = outcomes_to_payload(_outcomes(), window_start=WINDOW[0], window_end=WINDOW[1])

    wire = BenchResimsPayload.model_validate(payload.model_dump(mode="json"))
    back = payload_to_outcomes(wire)

    assert back["a1"].score == Decimal("1.25")
    assert back["a1"].ok and back["a1"].daily_turnover == 0.2
    assert list(back["a1"].daily_returns.round(4)) == [0.01, -0.002, 0.004]
    assert back["a1"].daily_returns.index[0] == pd.Timestamp("2026-10-14")
    assert back["a1"].run_id == _outcomes()["a1"].run_id or back["a1"].run_id is not None
    assert back["c"].ok is False and back["c"].error == "missing_strategy_config"
    assert len(back["c"].daily_returns) == 0


def test_latest_bench_resims_respects_freshness_and_applied_state(db_session: Session) -> None:
    service = WorkerResultApplyService(db_session)
    assert service.latest_bench_resims(now=NOW) is None

    # accepted when it was fresh (wide limit), too old by the time the review runs
    _accept(db_session, run_id="old", as_of=date(2026, 10, 9), max_age=30)
    OperatorSettingsRepository(db_session).update_current(
        {"worker_result_max_age_days": 3}, updated_by="test"
    )
    assert service.latest_bench_resims(now=NOW) is None  # 10 days old > 3

    _accept(db_session, run_id="w1")
    found = service.latest_bench_resims(now=NOW)
    assert found is not None
    assert found.run_id == "w1"
    assert found.window_start == WINDOW[0] and found.window_end == WINDOW[1]
    assert set(found.outcomes) == {"a1", "c"}
    assert found.market_returns is not None and len(found.market_returns) == 2

    service.mark_applied("w1", review_id="review_x", now=NOW)
    assert service.latest_bench_resims(now=NOW) is None  # applied results are not reused
    row = WorkerResultSubmissionsRepository(db_session).get("w1")
    assert row is not None and row.status == "applied" and row.review_id == "review_x"


def test_weekly_review_step_consumes_and_applies_the_worker_result(db_session: Session) -> None:
    from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import ChainContext
    from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
        PaperTradingGoldenPathOrchestrator,
    )

    review_tests._seed_swap(db_session)
    review_tests._mode(db_session, PortfolioReviewMode.ADVISORY)
    _accept(db_session, run_id="w1")
    orchestrator = PaperTradingGoldenPathOrchestrator(db_session)
    ctx = ChainContext(
        chain_name="eod",
        trading_date=NOW.date(),
        now_utc=NOW,
        session=db_session,
        parent_job_run_id="p",
    )

    out = orchestrator._step_weekly_portfolio_review(ctx)

    assert out["mode"] == "advisory"
    assert out["worker_run_id"] == "w1"
    assert out["review_id"]
    review = db_session.get(PortfolioReviewRow, out["review_id"])
    assert review is not None
    assert review.worker_run_id == "w1"
    assert review.window_start == WINDOW[0] and review.window_end == WINDOW[1]
    row = WorkerResultSubmissionsRepository(db_session).get("w1")
    assert row is not None and row.status == "applied" and row.review_id == out["review_id"]

    # Next week's review has nothing new to consume.
    later = NOW.replace(day=26)
    ctx_next = ChainContext(
        chain_name="eod",
        trading_date=later.date(),
        now_utc=later,
        session=db_session,
        parent_job_run_id="p2",
    )
    again = orchestrator._step_weekly_portfolio_review(ctx_next)
    assert again["worker_run_id"] is None


def test_weekly_review_step_runs_without_a_worker_result(db_session: Session) -> None:
    from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import ChainContext
    from autonomous_trading_platform.scheduler.orchestration.paper_trading_golden_path_orchestrator import (
        PaperTradingGoldenPathOrchestrator,
    )

    review_tests._seed_swap(db_session)
    review_tests._mode(db_session, PortfolioReviewMode.ADVISORY)
    ctx = ChainContext(
        chain_name="eod",
        trading_date=NOW.date(),
        now_utc=NOW,
        session=db_session,
        parent_job_run_id="p",
    )

    out = PaperTradingGoldenPathOrchestrator(db_session)._step_weekly_portfolio_review(ctx)

    assert out["worker_run_id"] is None
    assert out["review_id"]
    assert db_session.get(PortfolioReviewRow, out["review_id"]).worker_run_id is None
