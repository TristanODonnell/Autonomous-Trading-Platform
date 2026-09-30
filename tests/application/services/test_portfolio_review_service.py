"""Portfolio review service and review-driven refresh (portfolio rotation step 4D)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.portfolio_review_service import (
    REVIEW_ROLE,
    PortfolioReviewService,
)
from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
    ScorecardSet,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    PortfolioReviewMode,
    ReviewDecisionType,
    Scorecard,
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
from tests.application.services.test_active_portfolio_service import (
    _eligible,
    _hold,
    _set_state,
)

T0 = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
JOINED = T0 - timedelta(days=90)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeScorecards:
    def __init__(self, scores: dict[str, tuple[str, str]]) -> None:
        # strategy_id -> (tier, score)
        self.scores = scores

    def build(self, *, review_id: str, now: datetime, resim_outcomes: Any = None) -> ScorecardSet:
        cards = {
            sid: Scorecard(
                review_id=review_id,
                strategy_id=sid,
                reviewed_at=now,
                tier=tier,
                evidence_score=Decimal(score),
                score=Decimal(score),
                forward_days=60,
                forward_trades=40,
            )
            for sid, (tier, score) in self.scores.items()
        }
        ordered = sorted(cards.values(), key=lambda c: (-(c.score or 0), c.strategy_id))
        for index, card in enumerate(ordered, start=1):
            card.rank = index
        return ScorecardSet(
            cards=cards,
            returns={},
            active_ids=sorted(s for s, (t, _) in self.scores.items() if t == "active"),
        )


@dataclass
class _FakeGovernance:
    session: Session
    fail: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, **kw: Any) -> None:
        self.calls.append(kw)
        if self.fail:
            raise ValueError("Strategy does not meet promotion criteria")
        _set_state(self.session, kw["strategy_id"], kw["to_state"])


@dataclass
class _Rebalanced:
    skipped_reason: str | None = None
    changed: bool = True
    allocation_changes_count: int = 2
    after_allocation: dict[str, Decimal] = field(default_factory=dict)


class _FakeReallocation:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def rebalance(self, **kw: Any) -> _Rebalanced:
        self.calls.append(kw)
        return _Rebalanced(after_allocation={"a2": Decimal("0.5"), "c": Decimal("0.5")})


def _mode(session: Session, mode: PortfolioReviewMode, **extra: object) -> None:
    OperatorSettingsRepository(session).update_current(
        {
            "portfolio_review_mode": mode.value,
            "min_active_strategies": 1,
            "max_active_strategies": 2,
            **extra,
        },
        updated_by="test",
    )


def _member(session: Session, sid: str, status: MembershipStatus, at: datetime = JOINED) -> None:
    ActivePortfolioService(session).set_status(sid, status, "seed", actor="test", now=at)


def _statuses(session: Session) -> dict[str, str]:
    return {r.strategy_id: r.status for r in PortfolioMembershipRepository(session).get_all()}


# a1 weak active, a2 strong active, c strong on-deck challenger.
SCORES = {"a1": ("active", "1.00"), "a2": ("active", "1.40"), "c": ("on_deck", "1.30")}


def _seed_swap(session: Session, *, challenger_state: str = "approved_for_paper_trading") -> None:
    for sid in ("a1", "a2"):
        _eligible(session, sid)
        _member(session, sid, MembershipStatus.ACTIVE)
    _eligible(session, "c", state=challenger_state)
    _member(session, "c", MembershipStatus.ON_DECK)


def _service(
    session: Session,
    *,
    governance: _FakeGovernance | None = None,
    reallocation: _FakeReallocation | None = None,
    scores: dict[str, tuple[str, str]] | None = None,
) -> PortfolioReviewService:
    return PortfolioReviewService(
        session,
        scorecards=_FakeScorecards(scores or SCORES),  # type: ignore[arg-type]
        governance=governance or _FakeGovernance(session),  # type: ignore[arg-type]
        reallocation=reallocation or _FakeReallocation(),  # type: ignore[arg-type]
    )


def _review_week(session: Session, service: PortfolioReviewService, weeks_before: int) -> None:
    service.run(now=T0 - timedelta(weeks=weeks_before))


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def test_off_mode_does_nothing(db_session: Session) -> None:
    _seed_swap(db_session)

    assert _service(db_session).run(now=T0) is None
    assert PortfolioReviewRepository(db_session).recent_reviews() == []


def test_advisory_records_everything_and_changes_nothing(db_session: Session) -> None:
    _seed_swap(db_session)
    _mode(db_session, PortfolioReviewMode.ADVISORY, review_swap_interval_days=7)
    reallocation = _FakeReallocation()
    service = _service(db_session, reallocation=reallocation)
    for weeks in (2, 1):
        _review_week(db_session, service, weeks)
    before = _statuses(db_session)

    result = service.run(now=T0)

    assert result is not None
    (swap,) = [d for d in result.decisions if d.decision_type == ReviewDecisionType.SWAP]
    assert (swap.strategy_id, swap.counterpart_id, swap.applied) == ("c", "a1", False)
    assert _statuses(db_session) == before
    assert reallocation.calls == []
    repo = PortfolioReviewRepository(db_session)
    assert len(repo.scorecards(result.review_id)) == 3
    assert {d.decision_type for d in repo.decisions(result.review_id)} == {"swap"}


def test_streak_comes_from_stored_reviews(db_session: Session) -> None:
    _seed_swap(db_session)
    _mode(db_session, PortfolioReviewMode.ADVISORY, review_swap_interval_days=7)
    service = _service(db_session)

    first = service.run(now=T0 - timedelta(weeks=2))
    second = service.run(now=T0 - timedelta(weeks=1))
    third = service.run(now=T0)

    assert first is not None and second is not None and third is not None
    assert [d.streak for d in first.decisions] == [1]
    assert [(d.decision_type.value, d.streak) for d in second.decisions] == [("challenge", 2)]
    assert [(d.decision_type.value, d.streak) for d in third.decisions] == [("swap", 3)]


def test_an_extra_review_within_the_week_does_not_advance_the_streak(
    db_session: Session,
) -> None:
    """A review right after a research tick must not turn 3 weekly reviews into 3 days."""
    _seed_swap(db_session)
    _mode(db_session, PortfolioReviewMode.ADVISORY, review_swap_interval_days=7)
    service = _service(db_session)

    service.run(now=T0 - timedelta(days=7))
    service.run(now=T0 - timedelta(days=4))  # extra review (e.g. after research)
    latest = service.run(now=T0)

    assert latest is not None
    assert [(d.decision_type.value, d.streak) for d in latest.decisions] == [("challenge", 2)]


def test_swap_eligible_once_per_interval(db_session: Session) -> None:
    _seed_swap(db_session)
    _mode(db_session, PortfolioReviewMode.ADVISORY)
    service = _service(db_session)

    flags = [
        service.run(now=T0 + timedelta(days=days)).swap_eligible  # type: ignore[union-attr]
        for days in (0, 7, 14, 21, 28, 35)
    ]

    assert flags == [True, False, False, False, True, False]


# ---------------------------------------------------------------------------
# Auto: applying decisions
# ---------------------------------------------------------------------------


def _auto_swap(session: Session, **service_kw: Any) -> Any:
    _mode(session, PortfolioReviewMode.ADVISORY, review_swap_interval_days=7)
    service = _service(session, **service_kw)
    for weeks in (2, 1):
        _review_week(session, service, weeks)
    _mode(session, PortfolioReviewMode.AUTO, review_swap_interval_days=7)
    return service.run(now=T0)


def test_auto_swap_moves_both_sides_and_reweights(db_session: Session) -> None:
    _seed_swap(db_session)
    _hold(db_session, "a1")
    reallocation = _FakeReallocation()

    result = _auto_swap(db_session, reallocation=reallocation)

    statuses = _statuses(db_session)
    assert statuses["c"] == "active"
    assert statuses["a1"] == "winding_down"  # still holds positions: sells out first
    assert statuses["a2"] == "active"
    kinds = {d.decision_type: d for d in result.decisions}
    assert kinds[ReviewDecisionType.SWAP].applied is True
    assert kinds[ReviewDecisionType.REWEIGHT].applied is True
    assert kinds[ReviewDecisionType.REWEIGHT].reason == "reweighted"
    assert reallocation.calls and reallocation.calls[0]["now"] == T0
    # Auto-rebalance actor, or the next rebalance treats these overrides as manual.
    assert reallocation.calls[0]["actor"] == "auto_rebalance"
    assert reallocation.calls[0]["trigger_source"] == "portfolio_review"
    transitions = PortfolioMembershipRepository(db_session).get_transitions("c")
    assert transitions[-1].review_id == result.review_id
    assert transitions[-1].created_at == T0


def test_auto_swap_of_a_flat_incumbent_goes_straight_to_on_deck(db_session: Session) -> None:
    _seed_swap(db_session)

    _auto_swap(db_session)

    assert _statuses(db_session)["a1"] == "on_deck"


def test_auto_promotes_a_candidate_challenger_via_system_portfolio(db_session: Session) -> None:
    _seed_swap(db_session, challenger_state="candidate")
    governance = _FakeGovernance(db_session)

    result = _auto_swap(db_session, governance=governance)

    (call,) = governance.calls
    assert call["strategy_id"] == "c"
    assert call["to_state"] == "approved_for_paper_trading"
    assert call["actor_role"] == REVIEW_ROLE
    assert call["now"] == T0
    swap = next(d for d in result.decisions if d.decision_type == ReviewDecisionType.SWAP)
    assert swap.guardrails["governance"] is True
    assert _statuses(db_session)["c"] == "active"


def test_rejected_promotion_cancels_the_swap(db_session: Session) -> None:
    _seed_swap(db_session, challenger_state="candidate")

    result = _auto_swap(db_session, governance=_FakeGovernance(db_session, fail=True))

    swap = next(d for d in result.decisions if d.decision_type == ReviewDecisionType.SWAP)
    assert swap.applied is False
    assert swap.reason.endswith("governance_rejected")
    statuses = _statuses(db_session)
    assert (statuses["c"], statuses["a1"]) == ("on_deck", "active")
    stored = PortfolioReviewRepository(db_session).decisions(result.review_id)
    assert next(d for d in stored if d.decision_type == "swap").applied is False


# ---------------------------------------------------------------------------
# Refresh once the review drives selection
# ---------------------------------------------------------------------------


def _refresh(session: Session, scores: dict[str, float], at: datetime) -> Any:
    return ActivePortfolioService(
        session, quality_score_fn=lambda sid: Decimal(str(scores.get(sid, 1.0)))
    ).refresh(now=at)


def test_refresh_bootstraps_before_the_first_review(db_session: Session) -> None:
    for sid in ("a1", "a2"):
        _eligible(db_session, sid)
    _mode(db_session, PortfolioReviewMode.AUTO)

    result = _refresh(db_session, {"a1": 1.2, "a2": 1.1}, T0)

    assert result.active == ["a1", "a2"]


def test_refresh_does_not_select_once_the_review_drives(db_session: Session) -> None:
    _eligible(db_session, "a1")
    _member(db_session, "a1", MembershipStatus.ACTIVE)
    _eligible(db_session, "x")  # approved, better, open seat available
    _mode(db_session, PortfolioReviewMode.AUTO)
    _service(db_session, scores={"a1": ("active", "1.1")}).run(now=T0)

    result = _refresh(db_session, {"a1": 1.0, "x": 2.0}, T0 + timedelta(hours=1))

    assert result.active == ["a1"]
    assert result.added == []


def test_refresh_fills_a_protective_vacancy_from_review_scores(db_session: Session) -> None:
    for sid in ("a1", "a2"):
        _eligible(db_session, sid)
        _member(db_session, sid, MembershipStatus.ACTIVE)
    for sid in ("o1", "o2"):
        _eligible(db_session, sid)
        _member(db_session, sid, MembershipStatus.ON_DECK)
    _mode(db_session, PortfolioReviewMode.AUTO)
    scores = {
        "a1": ("active", "1.2"),
        "a2": ("active", "1.2"),
        "o1": ("on_deck", "1.05"),
        "o2": ("on_deck", "1.15"),
    }
    _service(db_session, scores=scores).run(now=T0)
    _set_state(db_session, "a2", "candidate")  # demoted by governance mid-week

    # Blended quality would pick o1; the review ranked o2 higher.
    result = _refresh(db_session, {"o1": 3.0, "o2": 1.0}, T0 + timedelta(days=1))

    assert result.added == ["o2"]
    assert sorted(result.active) == ["a1", "o2"]


def test_refresh_returns_a_finished_wind_down_to_on_deck(db_session: Session) -> None:
    _eligible(db_session, "a1")
    _member(db_session, "a1", MembershipStatus.ACTIVE)
    _eligible(db_session, "w")
    _member(db_session, "w", MembershipStatus.WINDING_DOWN)
    _mode(db_session, PortfolioReviewMode.AUTO)
    _service(db_session, scores={"a1": ("active", "1.1")}).run(now=T0)

    _refresh(db_session, {}, T0 + timedelta(hours=1))

    assert _statuses(db_session)["w"] == "on_deck"
