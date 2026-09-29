"""Bench review: admission gate, redundancy, protected tiers, expiry and the cap."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.bench_resimulation_service import (
    BenchWindow,
    ResimOutcome,
)
from autonomous_trading_platform.application.services.bench_review_service import (
    BenchReviewService,
    correlation,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.governance.bench import BenchDecision
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)
from tests.application.services.test_active_portfolio_service import _eligible, _settings

_NOW = datetime(2024, 3, 4, 21, 0, tzinfo=UTC)
_WINDOW = BenchWindow(
    dataset_version="raw_bars_test",
    price_basis=PriceBasis.RAW,
    symbols=["AAPL"],
    start_date=date(2024, 1, 2),
    end_date=date(2024, 3, 1),
)
_DAYS = [date(2024, 1, 2) + timedelta(days=i) for i in range(40)]


def _series(seed: int, *, like: pd.Series | None = None) -> pd.Series:
    rng = np.random.default_rng(seed)
    if like is not None:  # near-duplicate: same path plus a sliver of noise
        return like + rng.normal(0, 0.0005, len(like))
    return pd.Series(rng.normal(0, 0.01, len(_DAYS)), index=_DAYS)


class _FakeResim:
    def __init__(self) -> None:
        self.outcomes: dict[str, ResimOutcome] = {}
        self.calls: list[list[str]] = []

    def set(self, sid: str, score: float, returns: pd.Series | None = None) -> None:
        self.outcomes[sid] = ResimOutcome(
            strategy_id=sid,
            strategy_type="momentum",
            trade_count=10,
            total_return=0.01,
            sharpe_ratio=1.0,
            max_drawdown=-0.02,
            win_rate=0.5,
            score=Decimal(str(score)),
            daily_returns=returns if returns is not None else _series(hash(sid) % 10_000),
        )

    def fail(self, sid: str) -> None:
        self.outcomes[sid] = ResimOutcome(sid, "momentum", error="no bars")

    def resimulate(
        self, strategy_ids: list[str], *, window: BenchWindow, review_id: str
    ) -> dict[str, ResimOutcome]:
        self.calls.append(list(strategy_ids))
        return {sid: self.outcomes[sid] for sid in strategy_ids}


def _member(session: Session, sid: str, status: MembershipStatus, since: datetime = _NOW) -> None:
    PortfolioMembershipRepository(session).save(
        PortfolioMembershipRow(
            strategy_id=sid,
            status=status.value,
            since=since,
            reason="test",
            updated_by="test",
            updated_at=since,
        )
    )


def _state(session: Session, sid: str) -> str:
    return str(session.query(StrategyGovernance).filter_by(strategy_id=sid).one().current_state)


def _status(session: Session, sid: str) -> str | None:
    row = PortfolioMembershipRepository(session).get(sid)
    return row.status if row is not None else None


@pytest.fixture
def resim() -> _FakeResim:
    return _FakeResim()


@pytest.fixture
def review(db_session: Session, resim: _FakeResim):
    _settings(
        db_session,
        bench_management_enabled=True,
        max_bench_strategies=25,
        bench_correlation_threshold=0.85,
        bench_score_floor=1.0,
        bench_floor_strikes=3,
        bench_max_idle_days=120,
    )

    def run(now: datetime = _NOW):
        service = BenchReviewService(
            db_session,
            resimulation=resim,  # type: ignore[arg-type]
            portfolio=ActivePortfolioService(db_session),
        )
        return service.review(window=_WINDOW, now=now)

    return run


def _decisions(result) -> dict[str, tuple[str, str]]:
    return {e.strategy_id: (e.decision.value, e.reason) for e in result.evaluations}


class TestAdmission:
    def test_novel_candidate_is_admitted_to_the_bench(self, db_session, resim, review) -> None:
        _eligible(db_session, "new", state="candidate")
        resim.set("new", 1.3)

        result = review()

        assert _decisions(result) == {"new": ("admit", "novel")}
        assert result.admitted == ["new"] and result.bench == ["new"]
        assert _status(db_session, "new") == MembershipStatus.BENCH
        assert _state(db_session, "new") == "candidate"

    def test_candidate_below_the_floor_is_retired(self, db_session, resim, review) -> None:
        _eligible(db_session, "weak", state="candidate")
        resim.set("weak", 0.8)

        result = review()

        assert _decisions(result) == {"weak": ("retire", "below_score_floor")}
        assert _state(db_session, "weak") == "retired"

    def test_duplicate_of_a_better_bench_member_is_retired(self, db_session, resim, review) -> None:
        base = _series(1)
        _eligible(db_session, "incumbent", state="candidate")
        _member(db_session, "incumbent", MembershipStatus.BENCH)
        _eligible(db_session, "copy", state="candidate")
        resim.set("incumbent", 1.4, base)
        resim.set("copy", 1.2, _series(2, like=base))

        result = review()

        assert _decisions(result) == {
            "incumbent": ("keep", "champion"),
            "copy": ("retire", "redundant"),
        }
        copy = next(e for e in result.evaluations if e.strategy_id == "copy")
        assert copy.correlated_with == "incumbent" and copy.max_correlation > 0.85
        assert _state(db_session, "copy") == "retired"

    def test_better_duplicate_replaces_the_bench_member(self, db_session, resim, review) -> None:
        base = _series(1)
        _eligible(db_session, "incumbent", state="candidate")
        _member(db_session, "incumbent", MembershipStatus.BENCH)
        _eligible(db_session, "better", state="candidate")
        resim.set("incumbent", 1.1, base)
        resim.set("better", 1.5, _series(2, like=base))

        result = review()

        assert _decisions(result) == {
            "better": ("admit", "better_than_group"),
            "incumbent": ("retire", "redundant"),
        }
        assert _status(db_session, "better") == MembershipStatus.BENCH
        assert _status(db_session, "incumbent") == MembershipStatus.INACTIVE
        assert _state(db_session, "incumbent") == "retired"

    def test_equal_scored_duplicate_loses_to_the_incumbent(self, db_session, resim, review) -> None:
        base = _series(1)
        _eligible(db_session, "z_incumbent", state="candidate")
        _member(db_session, "z_incumbent", MembershipStatus.BENCH)
        _eligible(db_session, "a_copy", state="candidate")
        resim.set("z_incumbent", 1.3, base)
        resim.set("a_copy", 1.3, _series(2, like=base))

        result = review()

        assert _decisions(result) == {
            "z_incumbent": ("keep", "champion"),
            "a_copy": ("retire", "redundant"),
        }

    def test_redundancy_is_checked_across_families(self, db_session, resim, review) -> None:
        base = _series(1)
        _eligible(db_session, "momentum_x", state="candidate")
        _member(db_session, "momentum_x", MembershipStatus.BENCH)
        _eligible(db_session, "composite_y", state="candidate")
        resim.set("momentum_x", 1.4, base)
        resim.set("composite_y", 1.2, _series(2, like=base))
        resim.outcomes["composite_y"].strategy_type = "composite_rule"

        result = review()

        assert _decisions(result)["composite_y"] == ("retire", "redundant")

    def test_unrelated_strategies_all_stay(self, db_session, resim, review) -> None:
        for i, sid in enumerate(("a", "b", "c")):
            _eligible(db_session, sid, state="candidate")
            resim.set(sid, 1.2, _series(10 + i))

        result = review()

        assert result.admitted == ["a", "b", "c"]
        assert result.group_count == 3


class TestProtectedTiers:
    def test_bench_member_duplicating_an_active_strategy_is_retired(
        self, db_session, resim, review
    ) -> None:
        base = _series(1)
        _eligible(db_session, "live")
        _member(db_session, "live", MembershipStatus.ACTIVE)
        _eligible(db_session, "benched", state="candidate")
        _member(db_session, "benched", MembershipStatus.BENCH)
        resim.set("live", 1.0, base)
        resim.set("benched", 2.0, _series(2, like=base))  # better score, lower tier

        result = review()

        assert _decisions(result) == {
            "live": ("protected", "active"),
            "benched": ("retire", "redundant"),
        }
        assert next(e for e in result.evaluations if e.strategy_id == "live").is_champion

    def test_on_deck_and_active_are_never_pruned(self, db_session, resim, review) -> None:
        _eligible(db_session, "live")
        _member(db_session, "live", MembershipStatus.ACTIVE)
        _eligible(db_session, "od", state="candidate")
        _member(db_session, "od", MembershipStatus.ON_DECK, since=_NOW - timedelta(days=400))
        resim.set("live", 0.2)
        resim.set("od", 0.2)

        result = review()

        assert _decisions(result) == {
            "live": ("protected", "active"),
            "od": ("protected", "on_deck"),
        }
        assert _state(db_session, "od") == "candidate"
        assert _status(db_session, "od") == MembershipStatus.ON_DECK

    def test_winding_down_strategies_are_not_reviewed(self, db_session, resim, review) -> None:
        _eligible(db_session, "wd", state="candidate")
        _member(db_session, "wd", MembershipStatus.WINDING_DOWN)

        review()

        assert resim.calls == [[]]


class TestExpiry:
    def test_retired_after_three_consecutive_reviews_below_the_floor(
        self, db_session, resim, review
    ) -> None:
        _eligible(db_session, "fading", state="candidate")
        _member(db_session, "fading", MembershipStatus.BENCH)
        resim.set("fading", 0.9)

        first = review(_NOW)
        second = review(_NOW + timedelta(days=7))
        third = review(_NOW + timedelta(days=14))

        assert _decisions(first)["fading"] == ("keep", "champion")
        assert _decisions(second)["fading"] == ("keep", "champion")
        assert _decisions(third)["fading"] == ("retire", "score_floor_strikes")
        assert [e.floor_strikes for r in (first, second, third) for e in r.evaluations] == [1, 2, 3]

    def test_a_review_above_the_floor_resets_the_strikes(self, db_session, resim, review) -> None:
        _eligible(db_session, "wobbly", state="candidate")
        _member(db_session, "wobbly", MembershipStatus.BENCH)
        for day, score in enumerate((0.9, 0.9, 1.2, 0.9, 0.9)):
            resim.set("wobbly", score)
            result = review(_NOW + timedelta(days=7 * day))

        assert _decisions(result)["wobbly"] == ("keep", "champion")
        assert result.evaluations[0].floor_strikes == 2

    def test_idle_bench_member_is_retired(self, db_session, resim, review) -> None:
        _eligible(db_session, "stale", state="candidate")
        _member(db_session, "stale", MembershipStatus.BENCH, since=_NOW - timedelta(days=121))
        resim.set("stale", 1.5)

        result = review()

        assert _decisions(result) == {"stale": ("retire", "idle_on_bench")}


class TestCap:
    def test_lowest_scored_are_retired_above_the_cap(self, db_session, resim, review) -> None:
        _settings(db_session, max_bench_strategies=2)
        for i, (sid, score) in enumerate((("b1", 1.5), ("b2", 1.2))):
            _eligible(db_session, sid, state="candidate")
            _member(db_session, sid, MembershipStatus.BENCH)
            resim.set(sid, score, _series(20 + i))
        _eligible(db_session, "newcomer", state="candidate")
        resim.set("newcomer", 1.3, _series(30))

        result = review()

        assert _decisions(result) == {
            "b1": ("keep", "champion"),
            "b2": ("retire", "over_bench_cap"),
            "newcomer": ("admit", "novel"),
        }
        assert result.bench == ["b1", "newcomer"]

    def test_newcomer_loses_a_tie_with_an_incumbent(self, db_session, resim, review) -> None:
        _settings(db_session, max_bench_strategies=1)
        _eligible(db_session, "b1", state="candidate")
        _member(db_session, "b1", MembershipStatus.BENCH)
        _eligible(db_session, "a_new", state="candidate")
        resim.set("b1", 1.3, _series(40))
        resim.set("a_new", 1.3, _series(41))

        result = review()

        assert _decisions(result)["a_new"] == ("retire", "over_bench_cap")
        assert result.bench == ["b1"]


class TestRecordKeeping:
    def test_failed_resim_changes_nothing(self, db_session, resim, review) -> None:
        _eligible(db_session, "benched", state="candidate")
        _member(db_session, "benched", MembershipStatus.BENCH)
        _eligible(db_session, "pending", state="candidate")
        resim.fail("benched")
        resim.fail("pending")

        result = review()

        assert {e.decision for e in result.evaluations} == {BenchDecision.SKIPPED}
        assert _status(db_session, "benched") == MembershipStatus.BENCH
        assert _state(db_session, "pending") == "candidate"
        assert result.bench == ["benched"]

    def test_every_reviewed_strategy_gets_an_evaluation_row(
        self, db_session, resim, review
    ) -> None:
        base = _series(1)
        _eligible(db_session, "keeper", state="candidate")
        _member(db_session, "keeper", MembershipStatus.BENCH)
        _eligible(db_session, "dupe", state="candidate")
        resim.set("keeper", 1.4, base)
        resim.set("dupe", 1.1, _series(2, like=base))

        result = review()

        rows = db_session.query(BenchEvaluationRow).filter_by(review_id=result.review_id).all()
        assert {r.strategy_id: (r.decision, r.is_champion) for r in rows} == {
            "keeper": ("keep", True),
            "dupe": ("retire", False),
        }
        assert len({r.group_id for r in rows}) == 1
        assert all(r.window_end == date(2024, 3, 1) for r in rows)

    def test_retirement_is_recorded_at_the_review_time(self, db_session, resim, review) -> None:
        _eligible(db_session, "weak", state="candidate")
        resim.set("weak", 0.5)

        review()

        row = db_session.query(StrategyGovernance).filter_by(strategy_id="weak").one()
        assert row.updated_at == _NOW


def test_correlation_needs_enough_shared_days() -> None:
    a = _series(1)
    assert correlation(a, a) == pytest.approx(1.0)
    assert correlation(a.iloc[:5], a.iloc[:5]) is None
    assert correlation(a, pd.Series(0.0, index=_DAYS)) is None  # flat: undefined
