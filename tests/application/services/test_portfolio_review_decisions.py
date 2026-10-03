"""Portfolio review decision rules (portfolio rotation step 4C)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from autonomous_trading_platform.application.services.portfolio_review_decisions import (
    ReviewInputs,
    ReviewSettings,
    decide,
    streak,
)
from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
    ScorecardSet,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    ReviewDecision,
    ReviewDecisionType,
    Scorecard,
)

T0 = datetime(2024, 4, 1, 21, 0, tzinfo=UTC)
LONG_AGO = T0 - timedelta(days=90)
PAPER = "approved_for_paper_trading"
CANDIDATE = "candidate"

SETTINGS = ReviewSettings(
    min_active=1,
    max_active=2,
    max_on_deck=2,
    swap_margin=Decimal("0.10"),
    swap_consecutive=3,
    min_tenure_days=30,
    max_swaps_per_review=1,
    swap_interval_days=28,
    turnover_cost_bps=Decimal("20"),
    min_shadow_days=20,
    min_shadow_trades=10,
    score_floor=Decimal("1.0"),
    on_deck_min_tenure_days=21,
)


def _card(sid: str, tier: str, score: str, *, days: int = 60, trades: int = 40) -> Scorecard:
    value = Decimal(score)
    return Scorecard(
        review_id="r1",
        strategy_id=sid,
        reviewed_at=T0,
        tier=tier,
        evidence_score=value,
        score=value,
        forward_days=days,
        forward_trades=trades,
    )


def _inputs(
    cards: list[Scorecard],
    *,
    since: dict[str, datetime] | None = None,
    governance: dict[str, str] | None = None,
    swap_eligible: bool = True,
    prior_challengers: list[set[str]] | None = None,
    prior_below_floor: list[set[str]] | None = None,
    invested: dict[str, Decimal] | None = None,
) -> ReviewInputs:
    since = since or {}
    governance = governance or {}
    return ReviewInputs(
        review_id="r1",
        now=T0,
        swap_eligible=swap_eligible,
        scorecards=ScorecardSet(
            cards={c.strategy_id: c for c in cards},
            returns={},
            active_ids=sorted(c.strategy_id for c in cards if c.tier == "active"),
        ),
        members={c.strategy_id: (c.tier, since.get(c.strategy_id, LONG_AGO)) for c in cards},
        governance={c.strategy_id: governance.get(c.strategy_id, PAPER) for c in cards},
        invested_fraction=invested or {},
        prior_challengers=prior_challengers or [],
        prior_below_floor=prior_below_floor or [],
    )


def _of(decisions: list[ReviewDecision], kind: ReviewDecisionType) -> list[ReviewDecision]:
    return [d for d in decisions if d.decision_type == kind]


# Two actives (weak "a1" 1.00, strong "a2" 1.40), a strong challenger "c" (1.30),
# challenging for three reviews in a row.
STREAK_OF_3 = [{"c"}, {"c"}]


def _swap_setup(**kw: object) -> ReviewInputs:
    cards = [
        _card("a1", "active", "1.00"),
        _card("a2", "active", "1.40"),
        _card("c", "on_deck", "1.30"),
    ]
    return _inputs(cards, prior_challengers=STREAK_OF_3, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Swaps and guardrails
# ---------------------------------------------------------------------------


def test_streak_counts_consecutive_prior_reviews() -> None:
    assert streak("c", []) == 1
    assert streak("c", [{"c"}, {"c"}, {"x"}, {"c"}]) == 3


def test_challenger_swaps_in_for_the_weakest_incumbent() -> None:
    decisions = decide(_swap_setup(), SETTINGS)

    swaps = _of(decisions, ReviewDecisionType.SWAP)
    assert len(swaps) == 1
    swap = swaps[0]
    assert (swap.strategy_id, swap.counterpart_id) == ("c", "a1")
    assert (swap.from_status, swap.to_status) == ("on_deck", "active")
    assert swap.streak == 3
    assert swap.margin is not None and swap.margin > 0.10
    assert all(swap.guardrails[g] for g in ("streak", "swap_review", "tenure", "shadow", "cap"))


def _held_reason(inputs: ReviewInputs, settings: ReviewSettings = SETTINGS) -> str:
    decisions = decide(inputs, settings)
    assert not _of(decisions, ReviewDecisionType.SWAP)
    (challenge,) = _of(decisions, ReviewDecisionType.CHALLENGE)
    assert challenge.strategy_id == "c"
    return challenge.reason


def test_streak_guardrail_blocks_the_swap_alone() -> None:
    cards = [
        _card("a1", "active", "1.00"),
        _card("a2", "active", "1.40"),
        _card("c", "on_deck", "1.30"),
    ]

    assert _held_reason(_inputs(cards, prior_challengers=[{"c"}])) == "held_by_streak"


def test_weekly_review_records_the_challenge_but_does_not_swap() -> None:
    assert _held_reason(_swap_setup(swap_eligible=False)) == "held_by_swap_review"


def test_tenure_guardrail_blocks_the_swap_alone() -> None:
    since = {"a1": T0 - timedelta(days=10), "a2": T0 - timedelta(days=10)}

    assert _held_reason(_swap_setup(since=since)) == "held_by_tenure"


def test_tenure_picks_an_eligible_incumbent_when_the_weakest_is_too_new() -> None:
    cards = [
        _card("a1", "active", "1.00"),
        _card("a2", "active", "1.10"),
        _card("c", "on_deck", "1.40"),
    ]
    inputs = _inputs(cards, prior_challengers=STREAK_OF_3, since={"a1": T0 - timedelta(days=10)})

    (swap,) = _of(decide(inputs, SETTINGS), ReviewDecisionType.SWAP)

    assert swap.counterpart_id == "a2"


def test_shadow_minimum_blocks_a_candidate_alone() -> None:
    cards = [
        _card("a1", "active", "1.00"),
        _card("a2", "active", "1.40"),
        _card("c", "on_deck", "1.30", days=12, trades=30),
    ]
    inputs = _inputs(cards, prior_challengers=STREAK_OF_3, governance={"c": CANDIDATE})

    assert _held_reason(inputs) == "held_by_shadow"


def test_candidate_with_enough_shadow_record_swaps() -> None:
    inputs = _swap_setup(governance={"c": CANDIDATE})

    (swap,) = _of(decide(inputs, SETTINGS), ReviewDecisionType.SWAP)

    assert swap.strategy_id == "c"


def test_swap_cap_holds_the_second_challenger() -> None:
    cards = [
        _card("a1", "active", "1.00"),
        _card("a2", "active", "1.05"),
        _card("c", "on_deck", "1.40"),
        _card("d", "on_deck", "1.30"),
    ]
    inputs = _inputs(cards, prior_challengers=[{"c", "d"}, {"c", "d"}])

    decisions = decide(inputs, SETTINGS)

    (swap,) = _of(decisions, ReviewDecisionType.SWAP)
    (held,) = _of(decisions, ReviewDecisionType.CHALLENGE)
    assert (swap.strategy_id, swap.counterpart_id) == ("c", "a1")
    assert (held.strategy_id, held.reason) == ("d", "held_by_cap")
    assert held.counterpart_id == "a2"


def test_below_margin_is_not_a_challenge() -> None:
    cards = [
        _card("a1", "active", "1.00"),
        _card("a2", "active", "1.40"),
        _card("c", "on_deck", "1.08"),
    ]

    decisions = decide(_inputs(cards, prior_challengers=STREAK_OF_3), SETTINGS)

    assert not _of(decisions, ReviewDecisionType.CHALLENGE)
    assert not _of(decisions, ReviewDecisionType.SWAP)


def test_turnover_cost_counts_against_the_edge() -> None:
    # Edge before cost 0.102; cost of a fully invested sleeve at 20 bps = 1.5 × 0.002.
    cards = [
        _card("a1", "active", "1.000"),
        _card("a2", "active", "1.40"),
        _card("c", "on_deck", "1.102"),
    ]

    invested = decide(_inputs(cards, prior_challengers=STREAK_OF_3), SETTINGS)
    in_cash = decide(
        _inputs(cards, prior_challengers=STREAK_OF_3, invested={"a1": Decimal("0")}), SETTINGS
    )

    assert not _of(invested, ReviewDecisionType.SWAP)
    assert _of(in_cash, ReviewDecisionType.SWAP)


# ---------------------------------------------------------------------------
# Set size
# ---------------------------------------------------------------------------


def test_open_seat_goes_to_the_best_on_deck_above_the_floor() -> None:
    cards = [
        _card("a1", "active", "1.20"),
        _card("o1", "on_deck", "1.10"),
        _card("o2", "on_deck", "1.05"),
    ]

    (add,) = _of(decide(_inputs(cards), SETTINGS), ReviewDecisionType.ADD_SEAT)

    assert (add.strategy_id, add.reason) == ("o1", "open_seat")


def test_open_seat_stays_empty_below_the_floor() -> None:
    cards = [_card("a1", "active", "1.20"), _card("o1", "on_deck", "0.95")]

    assert not _of(decide(_inputs(cards), SETTINGS), ReviewDecisionType.ADD_SEAT)


def test_floor_is_waived_below_min_active() -> None:
    cards = [_card("o1", "on_deck", "0.95")]

    (add,) = _of(decide(_inputs(cards), SETTINGS), ReviewDecisionType.ADD_SEAT)

    assert add.reason == "below_min_active"


def test_candidate_without_shadow_record_does_not_take_an_open_seat() -> None:
    cards = [_card("a1", "active", "1.20"), _card("o1", "on_deck", "1.30", days=5, trades=2)]

    decisions = decide(_inputs(cards, governance={"o1": CANDIDATE}), SETTINGS)

    assert not _of(decisions, ReviewDecisionType.ADD_SEAT)


def test_active_below_floor_is_dropped_after_the_streak() -> None:
    cards = [_card("a1", "active", "1.20"), _card("a2", "active", "0.90")]

    decisions = decide(_inputs(cards, prior_below_floor=[{"a2"}, {"a2"}]), SETTINGS)

    (drop,) = _of(decisions, ReviewDecisionType.DROP_SEAT)
    assert (drop.strategy_id, drop.to_status) == ("a2", "winding_down")


def test_active_below_floor_is_kept_until_the_streak() -> None:
    cards = [_card("a1", "active", "1.20"), _card("a2", "active", "0.90")]

    (keep,) = _of(decide(_inputs(cards), SETTINGS), ReviewDecisionType.KEEP)

    assert (keep.strategy_id, keep.reason, keep.streak) == ("a2", "below_floor_held_by_streak", 1)


def test_min_active_keeps_an_active_below_the_floor() -> None:
    cards = [_card("a1", "active", "0.90")]

    decisions = decide(_inputs(cards, prior_below_floor=[{"a1"}, {"a1"}]), SETTINGS)

    (keep,) = _of(decisions, ReviewDecisionType.KEEP)
    assert keep.reason == "below_floor_held_by_min_active"


# ---------------------------------------------------------------------------
# On-deck <-> bench
# ---------------------------------------------------------------------------


def test_open_on_deck_slots_go_to_the_best_bench_members() -> None:
    cards = [
        _card("a1", "active", "1.20"),
        _card("a2", "active", "1.30"),
        _card("b1", "bench", "1.00"),
        _card("b2", "bench", "1.10"),
        _card("b3", "bench", "1.05"),
    ]

    promoted = _of(decide(_inputs(cards), SETTINGS), ReviewDecisionType.PROMOTE_ON_DECK)

    assert sorted(d.strategy_id for d in promoted) == ["b2", "b3"]


def test_bench_member_replaces_the_weakest_on_deck_by_margin() -> None:
    cards = [
        _card("a1", "active", "1.50"),
        _card("a2", "active", "1.60"),
        _card("o1", "on_deck", "1.00"),
        _card("o2", "on_deck", "1.20"),
        _card("b1", "bench", "1.15"),
    ]

    decisions = decide(_inputs(cards, governance={"o1": CANDIDATE}), SETTINGS)

    (demoted,) = _of(decisions, ReviewDecisionType.DEMOTE_ON_DECK)
    (promoted,) = _of(decisions, ReviewDecisionType.PROMOTE_ON_DECK)
    assert (demoted.strategy_id, demoted.to_status) == ("o1", "bench")
    assert (promoted.strategy_id, promoted.counterpart_id) == ("b1", "o1")


def test_on_deck_tenure_protects_a_new_on_deck_member() -> None:
    cards = [
        _card("a1", "active", "1.50"),
        _card("a2", "active", "1.60"),
        _card("o1", "on_deck", "1.00"),
        _card("o2", "on_deck", "1.20"),
        _card("b1", "bench", "1.15"),
    ]
    since = {"o1": T0 - timedelta(days=7), "o2": T0 - timedelta(days=7)}

    decisions = decide(_inputs(cards, since=since), SETTINGS)

    assert not _of(decisions, ReviewDecisionType.DEMOTE_ON_DECK)


def test_on_deck_over_the_cap_loses_the_weakest() -> None:
    cards = [
        _card("a1", "active", "1.50"),
        _card("a2", "active", "1.60"),
        _card("o1", "on_deck", "1.00"),
        _card("o2", "on_deck", "1.20"),
        _card("o3", "on_deck", "1.10"),
    ]

    (demoted,) = _of(decide(_inputs(cards), SETTINGS), ReviewDecisionType.DEMOTE_ON_DECK)

    # An approved strategy leaving on-deck is not a bench candidate.
    assert (demoted.strategy_id, demoted.reason, demoted.to_status) == (
        "o1",
        "on_deck_over_max",
        "inactive",
    )


def test_swap_leaves_an_on_deck_slot_for_the_bench() -> None:
    cards = [
        _card("a1", "active", "1.00"),
        _card("a2", "active", "1.40"),
        _card("c", "on_deck", "1.30"),
        _card("o2", "on_deck", "1.20"),
        _card("b1", "bench", "1.05"),
    ]
    settings = replace(SETTINGS, max_on_deck=2)

    decisions = decide(_inputs(cards, prior_challengers=STREAK_OF_3), settings)

    assert _of(decisions, ReviewDecisionType.SWAP)
    (promoted,) = _of(decisions, ReviewDecisionType.PROMOTE_ON_DECK)
    assert (promoted.strategy_id, promoted.reason) == ("b1", "open_on_deck_slot")
