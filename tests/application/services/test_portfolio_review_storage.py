"""Portfolio review storage and settings (portfolio rotation step 4A)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    PortfolioReviewMode,
    ReviewDecisionType,
)
from autonomous_trading_platform.storage.sor.models.portfolio_reviews import (
    PortfolioReviewDecisionRow,
    PortfolioReviewRow,
    PortfolioScorecardRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_review_repository import (
    PortfolioReviewRepository,
)

T0 = datetime(2024, 3, 4, 21, 0, tzinfo=UTC)


def _review(repo: PortfolioReviewRepository, review_id: str, at: datetime, *, swap: bool) -> None:
    repo.insert_review(
        PortfolioReviewRow(
            review_id=review_id,
            reviewed_at=at,
            mode=PortfolioReviewMode.ADVISORY.value,
            swap_eligible=swap,
            window_start=date(2024, 1, 2),
            window_end=at.date(),
        )
    )


def test_review_settings_defaults(db_session: Session) -> None:
    row = OperatorSettingsRepository(db_session).get_or_create_default()

    assert row.portfolio_review_mode == "off"
    assert float(row.review_swap_margin) == 0.10
    assert row.review_swap_consecutive == 4
    assert row.review_min_tenure_days == 60
    assert row.review_max_swaps_per_review == 1
    assert row.review_swap_interval_days == 28
    assert float(row.review_turnover_cost_bps) == 20
    assert row.review_min_shadow_days == 20
    assert row.review_min_shadow_trades == 10
    assert float(row.review_score_floor) == 1.0
    assert row.review_on_deck_min_tenure_days == 21


def test_review_rows_round_trip(db_session: Session) -> None:
    repo = PortfolioReviewRepository(db_session)
    _review(repo, "r1", T0, swap=True)
    repo.insert_scorecard(
        PortfolioScorecardRow(
            scorecard_id=uuid4(),
            review_id="r1",
            strategy_id="a",
            reviewed_at=T0,
            tier="active",
            forward_source="live",
            forward_score=1.2,
            forward_weight=0.5,
            resim_score=1.1,
            resim_weight=0.4,
            backtest_score=1.3,
            backtest_weight=0.1,
            evidence_score=1.17,
            score=1.1,
            rank=1,
        )
    )
    repo.insert_decision(
        PortfolioReviewDecisionRow(
            decision_id=uuid4(),
            review_id="r1",
            reviewed_at=T0,
            decision_type=ReviewDecisionType.CHALLENGE.value,
            strategy_id="c",
            counterpart_id="a",
            margin=0.12,
            streak=1,
            guardrails={"streak": False, "tenure": True},
            reason="streak_below_required",
        )
    )

    cards = repo.latest_scorecards()
    decisions = repo.decisions("r1")
    assert [c.strategy_id for c in cards] == ["a"]
    assert decisions[0].guardrails == {"streak": False, "tenure": True}
    assert repo.decisions_for_reviews(["r1"])[0].counterpart_id == "a"


def test_recent_and_last_swap_eligible_reviews(db_session: Session) -> None:
    repo = PortfolioReviewRepository(db_session)
    _review(repo, "r1", T0, swap=True)
    _review(repo, "r2", T0 + timedelta(days=7), swap=False)
    _review(repo, "r3", T0 + timedelta(days=14), swap=False)

    assert [r.review_id for r in repo.recent_reviews(limit=2)] == ["r3", "r2"]
    assert [r.review_id for r in repo.recent_reviews(before=T0 + timedelta(days=14))] == [
        "r2",
        "r1",
    ]
    last = repo.last_swap_eligible()
    assert last is not None and last.review_id == "r1"
    assert repo.last_swap_eligible(before=T0) is None


def test_set_status_records_review_id_on_the_transition(db_session: Session) -> None:
    transition = ActivePortfolioService(db_session).set_status(
        "s1",
        MembershipStatus.ON_DECK,
        "promote_on_deck",
        actor="portfolio_review",
        now=T0,
        review_id="r1",
    )

    stored = PortfolioMembershipRepository(db_session).get_transitions("s1")
    assert transition.review_id == "r1"
    assert [t.review_id for t in stored] == ["r1"]
