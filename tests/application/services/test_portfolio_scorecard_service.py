"""Portfolio scorecard (portfolio rotation step 4B)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.bench_resimulation_service import (
    ResimOutcome,
)
from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    compute_alpha,
)
from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
    BACKTEST_HALF_LIFE_DAYS,
    SHADOW_CONFIDENCE,
    ForwardEvidence,
    PortfolioScorecardService,
    StrategyEvidence,
    build_scorecards,
    evidence_weights,
)
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import SleeveBook
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import ForwardSource
from autonomous_trading_platform.contracts.runtime.live_performance_metrics import (
    LivePerformanceMetrics,
)
from autonomous_trading_platform.storage.sor.models.strategy_health_state import (
    StrategyHealthStateRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    ShadowSleeveLedgerRow,
    ShadowSleeveSnapshotRow,
)

T0 = datetime(2024, 3, 4, 21, 0, tzinfo=UTC)
ACTIVE = MembershipStatus.ACTIVE.value
ON_DECK = MembershipStatus.ON_DECK.value
BENCH = MembershipStatus.BENCH.value


def _returns(values: list[float]) -> pd.Series:
    index = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(values))]
    return pd.Series(values, index=index, dtype=float)


# Two return streams with correlation ~1 and one roughly independent of both.
BASE = [0.01, -0.02, 0.015, 0.0, -0.01, 0.02, 0.005, -0.015, 0.01, 0.0, 0.012, -0.004]
CLONE = [v * 1.1 for v in BASE]
_RAW = [0.0, 0.01, 0.01, -0.02, 0.02, -0.01, 0.0, 0.01, -0.01, 0.005, -0.012, 0.004]


def _uncorrelated_with(base: list[float], raw: list[float]) -> list[float]:
    """raw minus its projection on base (demeaned): correlation with base is 0."""
    b = np.array(base) - np.mean(base)
    r = np.array(raw) - np.mean(raw)
    return list(r - (r @ b) / (b @ b) * b)


INDEPENDENT = _uncorrelated_with(BASE, _RAW)


def _card(evidence: list[StrategyEvidence], sid: str) -> Any:
    return build_scorecards(evidence, review_id="r1", now=T0).cards[sid]


# ---------------------------------------------------------------------------
# Evidence weights
# ---------------------------------------------------------------------------


def test_bench_strategy_is_scored_on_resim_and_backtest_only() -> None:
    f, r, b = evidence_weights(forward=None, has_resim=True, has_backtest=True, backtest_age_days=0)

    assert f == 0
    assert b == Decimal("0.5")
    assert r == Decimal("0.5")


def test_backtest_fades_with_age() -> None:
    _, r0, b0 = evidence_weights(
        forward=None, has_resim=True, has_backtest=True, backtest_age_days=0
    )
    _, r1, b1 = evidence_weights(
        forward=None,
        has_resim=True,
        has_backtest=True,
        backtest_age_days=BACKTEST_HALF_LIFE_DAYS,
    )

    assert b1 == pytest.approx(b0 / 2, abs=1e-6)
    assert r1 > r0
    assert r1 + b1 == pytest.approx(1, abs=1e-6)


def test_forward_weight_is_maturity_alpha_and_shadow_is_discounted() -> None:
    live = ForwardEvidence(ForwardSource.LIVE, Decimal("1.2"), days=60, trades=40)
    shadow = ForwardEvidence(ForwardSource.SHADOW, Decimal("1.2"), days=60, trades=40)

    f_live, _, _ = evidence_weights(
        forward=live, has_resim=True, has_backtest=True, backtest_age_days=0
    )
    f_shadow, _, _ = evidence_weights(
        forward=shadow, has_resim=True, has_backtest=True, backtest_age_days=0
    )

    assert float(f_live) == pytest.approx(compute_alpha(60, 40), abs=1e-6)
    assert float(f_shadow) == pytest.approx(
        compute_alpha(60, 40) * float(SHADOW_CONFIDENCE), abs=1e-6
    )


def test_missing_sources_are_renormalised() -> None:
    forward = ForwardEvidence(ForwardSource.LIVE, Decimal("1.2"), days=60, trades=40)

    f, r, b = evidence_weights(
        forward=forward, has_resim=False, has_backtest=True, backtest_age_days=0
    )
    only_f, _, _ = evidence_weights(
        forward=forward, has_resim=False, has_backtest=False, backtest_age_days=0
    )

    assert r == 0
    assert f + b == pytest.approx(1, abs=1e-6)
    assert only_f == 1


def test_no_evidence_means_no_score_and_ranks_last() -> None:
    cards = build_scorecards(
        [
            StrategyEvidence("empty", BENCH),
            StrategyEvidence("b", BENCH, resim_score=Decimal("1.1")),
        ],
        review_id="r1",
        now=T0,
    ).cards

    assert cards["empty"].score is None
    assert cards["empty"].rank == 2
    assert cards["b"].rank == 1


# ---------------------------------------------------------------------------
# Lenses
# ---------------------------------------------------------------------------


def test_evidence_blend_matches_the_weights() -> None:
    forward = ForwardEvidence(ForwardSource.LIVE, Decimal("1.4"), days=60, trades=40)
    card = _card(
        [
            StrategyEvidence(
                "a",
                ACTIVE,
                forward=forward,
                resim_score=Decimal("1.2"),
                backtest_score=Decimal("1.0"),
                backtest_age_days=0,
            )
        ],
        "a",
    )

    expected = (
        card.forward_weight * Decimal("1.4")
        + card.resim_weight * Decimal("1.2")
        + card.backtest_weight * Decimal("1.0")
    )
    assert card.evidence_score == pytest.approx(expected, abs=Decimal("0.00001"))
    assert card.decay_penalty == 0  # forward beat the expectation
    assert card.score == card.evidence_score


def test_decay_penalises_forward_results_below_expectation() -> None:
    forward = ForwardEvidence(ForwardSource.LIVE, Decimal("0.8"), days=60, trades=40)
    card = _card(
        [
            StrategyEvidence(
                "a",
                ACTIVE,
                forward=forward,
                resim_score=Decimal("1.4"),
                backtest_score=Decimal("1.4"),
                backtest_age_days=0,
            )
        ],
        "a",
    )

    # expected 1.4, forward 0.8 -> shortfall 0.6, × 0.25 × w_f
    assert card.decay_penalty == pytest.approx(
        Decimal("0.25") * card.forward_weight * Decimal("0.6"), abs=Decimal("0.00001")
    )
    assert card.score == pytest.approx(
        card.evidence_score - card.decay_penalty, abs=Decimal("0.00001")
    )


@pytest.mark.parametrize(
    ("status", "penalty"),
    [("healthy", "0"), ("watch", "0"), ("degrading", "0.10"), ("critical", "0.25")],
)
def test_health_penalty(status: str, penalty: str) -> None:
    card = _card(
        [StrategyEvidence("a", ACTIVE, resim_score=Decimal("1.5"), health_status=status)], "a"
    )

    assert card.health_penalty == Decimal(penalty)


def test_correlation_penalty_only_against_other_actives() -> None:
    cards = build_scorecards(
        [
            StrategyEvidence("a", ACTIVE, resim_score=Decimal("1.2"), resim_returns=_returns(BASE)),
            StrategyEvidence(
                "clone", ON_DECK, resim_score=Decimal("1.2"), resim_returns=_returns(CLONE)
            ),
            StrategyEvidence(
                "indep", ON_DECK, resim_score=Decimal("1.2"), resim_returns=_returns(INDEPENDENT)
            ),
        ],
        review_id="r1",
        now=T0,
    ).cards

    assert cards["clone"].mean_correlation == pytest.approx(1.0, abs=1e-6)
    assert cards["clone"].correlation_penalty == pytest.approx(Decimal("0.35"), abs=Decimal("1e-5"))
    assert cards["indep"].correlation_penalty < cards["clone"].correlation_penalty
    # The only active has no other active to correlate with.
    assert cards["a"].mean_correlation is None
    assert cards["a"].correlation_penalty == 0
    assert (cards["indep"].rank or 0) < (cards["clone"].rank or 0)


def test_score_for_slot_excludes_the_incumbent_being_replaced() -> None:
    scorecards = build_scorecards(
        [
            StrategyEvidence("a", ACTIVE, resim_score=Decimal("1.2"), resim_returns=_returns(BASE)),
            StrategyEvidence(
                "b", ACTIVE, resim_score=Decimal("1.2"), resim_returns=_returns(INDEPENDENT)
            ),
            StrategyEvidence(
                "clone", ON_DECK, resim_score=Decimal("1.3"), resim_returns=_returns(CLONE)
            ),
        ],
        review_id="r1",
        now=T0,
    )
    card = scorecards.cards["clone"]

    replacing_a = scorecards.score_for_slot("clone", replacing="a")
    replacing_b = scorecards.score_for_slot("clone", replacing="b")

    assert card.correlation_penalty > 0
    # Replacing its twin removes the redundancy: no penalty left against b alone.
    assert replacing_a == pytest.approx(card.evidence_score, abs=Decimal("0.00001"))
    # Replacing b leaves it next to its twin: fully penalised.
    assert replacing_b is not None and card.score is not None
    assert replacing_b < card.score


def test_blocked_orders_penalise_shadow_results() -> None:
    card = _card(
        [StrategyEvidence("o", ON_DECK, resim_score=Decimal("1.2"), blocked_ratio=0.4)], "o"
    )

    assert card.blocked_penalty == Decimal("0.100000")


def test_ties_go_to_the_higher_tier() -> None:
    cards = build_scorecards(
        [
            StrategyEvidence("x_bench", BENCH, resim_score=Decimal("1.2")),
            StrategyEvidence("y_active", ACTIVE, resim_score=Decimal("1.2")),
        ],
        review_id="r1",
        now=T0,
    ).cards

    assert cards["y_active"].rank == 1


# ---------------------------------------------------------------------------
# Gathering evidence from storage
# ---------------------------------------------------------------------------


class _FakeLive:
    def __init__(self, metrics: dict[tuple[str, SleeveBook], LivePerformanceMetrics]) -> None:
        self._metrics = metrics
        self.calls: list[tuple[str, SleeveBook, datetime | None]] = []

    def compute_for_strategy(
        self, strategy_id: str, *, now: datetime | None = None, book: SleeveBook = SleeveBook.REAL
    ) -> LivePerformanceMetrics:
        self.calls.append((strategy_id, book, now))
        return self._metrics.get(
            (strategy_id, book),
            LivePerformanceMetrics(snapshot_id="x", strategy_id=strategy_id, computed_at=T0),
        )


class _FakeBacktest:
    def __init__(self, scores: dict[str, Decimal]) -> None:
        self._scores = scores

    def backtest_quality_score(self, strategy_id: str) -> Decimal | None:
        return self._scores.get(strategy_id)


def _metrics(sid: str, *, days: int, trades: int, ret: float) -> LivePerformanceMetrics:
    return LivePerformanceMetrics(
        snapshot_id=str(uuid4()),
        strategy_id=sid,
        computed_at=T0,
        realized_return=ret,
        rolling_sharpe=1.0,
        realized_drawdown=0.02,
        live_win_rate=0.5,
        trade_count=trades,
        days_live=days,
    )


def _member(session: Session, sid: str, status: MembershipStatus, at: datetime) -> None:
    ActivePortfolioService(session).set_status(sid, status, "test", actor="test", now=at)


def test_service_gathers_evidence_per_tier(db_session: Session) -> None:
    joined = T0 - timedelta(days=45)
    _member(db_session, "a", MembershipStatus.ACTIVE, joined)
    _member(db_session, "o", MembershipStatus.ON_DECK, joined)
    _member(db_session, "b", MembershipStatus.BENCH, joined)
    _member(db_session, "w", MembershipStatus.WINDING_DOWN, joined)
    db_session.add(
        StrategyHealthStateRow(
            health_id="h1",
            strategy_id="a",
            health_status="degrading",
            created_at=T0,
            updated_at=T0,
        )
    )
    for day, blocked in ((1, 3), (2, 1)):
        db_session.add(
            ShadowSleeveSnapshotRow(
                snapshot_id=uuid4(),
                strategy_id="o",
                timestamp=joined + timedelta(days=day),
                market_value=Decimal("0"),
                cost_basis=Decimal("0"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                fees=Decimal("0"),
                net_pnl=Decimal("0"),
                position_count=0,
                blocked_order_count=blocked,
            )
        )
    db_session.add(
        ShadowSleeveLedgerRow(
            entry_id="e1",
            strategy_id="o",
            symbol="AAPL",
            side="buy",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fees=Decimal("0"),
            realized_pnl=Decimal("0"),
            source="shadow_fill",
            timestamp=joined + timedelta(days=1),
        )
    )
    db_session.flush()

    live = _FakeLive(
        {
            ("a", SleeveBook.REAL): _metrics("a", days=40, trades=30, ret=0.05),
            ("o", SleeveBook.SHADOW): _metrics("o", days=40, trades=30, ret=0.03),
        }
    )
    service = PortfolioScorecardService(
        db_session,
        live_metrics=live,  # type: ignore[arg-type]
        backtest=_FakeBacktest({"a": Decimal("1.3"), "b": Decimal("1.1")}),  # type: ignore[arg-type]
        regime_label_fn=lambda now: "trend_up",
    )
    resim = ResimOutcome("b", "momentum", score=Decimal("1.25"), daily_returns=_returns(BASE))

    cards = service.build(review_id="r1", now=T0, resim_outcomes={"b": resim}).cards

    assert set(cards) == {"a", "o", "b"}  # winding-down members are not scored
    assert cards["a"].forward_source == ForwardSource.LIVE
    assert cards["a"].health_status == "degrading"
    assert cards["a"].backtest_age_days == 45
    assert cards["o"].forward_source == ForwardSource.SHADOW
    assert cards["o"].blocked_ratio == pytest.approx(4 / 5)
    assert cards["o"].backtest_score is None
    assert cards["b"].forward_source is None
    assert cards["b"].resim_score == Decimal("1.25")
    assert {c.regime_label for c in cards.values()} == {"trend_up"}
    assert ("a", SleeveBook.REAL, T0) in live.calls
    assert ("o", SleeveBook.SHADOW, T0) in live.calls


def test_forward_evidence_needs_at_least_one_day(db_session: Session) -> None:
    _member(db_session, "a", MembershipStatus.ACTIVE, T0)
    live = _FakeLive({("a", SleeveBook.REAL): _metrics("a", days=0, trades=0, ret=0.0)})
    service = PortfolioScorecardService(
        db_session,
        live_metrics=live,  # type: ignore[arg-type]
        backtest=_FakeBacktest({"a": Decimal("1.3")}),  # type: ignore[arg-type]
    )

    card = service.build(review_id="r1", now=T0).cards["a"]

    assert card.forward_source is None
    assert card.backtest_weight == 1
    assert card.score == Decimal("1.3")
