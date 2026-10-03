"""
Portfolio review decision rules (portfolio rotation step 4). Pure: no storage access.

Given one review's scorecards and the current tiers, decide who moves:

  1. Challenges and swaps (ACTIVE <-> ON_DECK). A challenger beats an incumbent when
     its slot score (score_for_slot: correlation re-scored without the incumbent),
     less the incumbent's turnover cost, exceeds the incumbent's score by
     swap_margin. Every beat is recorded (CHALLENGE); it becomes a SWAP only when:
       streak   beaten some incumbent on swap_consecutive consecutive reviews
       review   this is a swap-eligible (monthly) review
       tenure   the incumbent has held its seat >= min_tenure_days
       shadow   a governance candidate has a shadow record >= min shadow days/trades
       cap      fewer than max_swaps_per_review swaps so far this review
     Each incumbent and each challenger takes part in at most one swap.
  2. Set size. Below max_active, the best on-deck strategy scoring at least
     score_floor takes the open seat (ADD_SEAT); below min_active the floor is
     waived. An active below the floor leaves (DROP_SEAT) after swap_consecutive
     consecutive reviews below it, past min tenure, never below min_active;
     otherwise it is kept (KEEP rows form the streak).
  3. On-deck <-> bench. Open on-deck slots go to the best bench members; a bench
     member beating the weakest on-deck member (past on-deck tenure) by
     swap_margin replaces it; on-deck above its cap loses the weakest.

A candidate entering the active set needs the shadow record (and, when applied, a
governance promotion); an approved strategy does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
    ScorecardSet,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    ReviewDecision,
    ReviewDecisionType,
)

_CANDIDATE_DB_STATE = "candidate"
# Score weight of total return in metrics_quality_score: turns a return drag into score.
_RETURN_SCORE_WEIGHT = Decimal("1.50")

ACTIVE = MembershipStatus.ACTIVE.value
ON_DECK = MembershipStatus.ON_DECK.value
BENCH = MembershipStatus.BENCH.value


@dataclass(frozen=True)
class ReviewSettings:
    min_active: int
    max_active: int
    max_on_deck: int
    swap_margin: Decimal
    swap_consecutive: int
    min_tenure_days: int
    max_swaps_per_review: int
    swap_interval_days: int
    turnover_cost_bps: Decimal
    min_shadow_days: int
    min_shadow_trades: int
    score_floor: Decimal
    on_deck_min_tenure_days: int

    @classmethod
    def from_row(cls, row: Any) -> ReviewSettings:
        def dec(value: Any, default: str) -> Decimal:
            return Decimal(str(value)) if value is not None else Decimal(default)

        min_active = max(int(row.min_active_strategies or 0), 0)
        max_active = max(int(row.max_active_strategies or 0), 1)
        return cls(
            min_active=min(min_active, max_active),
            max_active=max_active,
            max_on_deck=max(int(row.max_on_deck_strategies or 0), 0),
            swap_margin=dec(row.review_swap_margin, "0.10"),
            swap_consecutive=max(int(row.review_swap_consecutive or 0), 1),
            min_tenure_days=max(int(row.review_min_tenure_days or 0), 0),
            max_swaps_per_review=max(int(row.review_max_swaps_per_review or 0), 0),
            swap_interval_days=max(int(row.review_swap_interval_days or 0), 1),
            turnover_cost_bps=dec(row.review_turnover_cost_bps, "20"),
            min_shadow_days=max(int(row.review_min_shadow_days or 0), 0),
            min_shadow_trades=max(int(row.review_min_shadow_trades or 0), 0),
            score_floor=dec(row.review_score_floor, "1.0"),
            on_deck_min_tenure_days=max(int(row.review_on_deck_min_tenure_days or 0), 0),
        )


@dataclass
class ReviewInputs:
    review_id: str
    now: datetime
    swap_eligible: bool
    scorecards: ScorecardSet
    # strategy_id -> (membership status, since) for ACTIVE / ON_DECK / BENCH members.
    members: dict[str, tuple[str, datetime]]
    # Latest governance state (DB string) per strategy.
    governance: dict[str, str]
    # Fraction of each active incumbent's budget currently invested (sleeve market
    # value ÷ allocated capital); what a swap would have to trade out of.
    invested_fraction: dict[str, Decimal] = field(default_factory=dict)
    # Earlier reviews, most recent first: strategy ids that beat an incumbent
    # (CHALLENGE / SWAP rows) and actives held below the floor (KEEP / DROP_SEAT rows).
    prior_challengers: list[set[str]] = field(default_factory=list)
    prior_below_floor: list[set[str]] = field(default_factory=list)


def streak(strategy_id: str, prior: list[set[str]]) -> int:
    """1 (this review) + consecutive immediately preceding reviews containing the id."""
    count = 1
    for ids in prior:
        if strategy_id not in ids:
            break
        count += 1
    return count


def weekly_review_dates(
    previous: list[datetime], *, now: datetime, depth: int, min_gap_days: int
) -> list[datetime]:
    """Earlier reviews that count toward streaks, most recent first, at most `depth`.

    Streaks count weekly reviews: a review within min_gap_days of the next counted
    one (e.g. an extra review right after a research tick) neither counts nor breaks.
    """
    kept: list[datetime] = []
    anchor = now
    for reviewed_at in sorted((d for d in previous if d < now), reverse=True):
        if (anchor - reviewed_at).days < min_gap_days:
            continue
        kept.append(reviewed_at)
        anchor = reviewed_at
        if len(kept) >= depth:
            break
    return kept


def decide(inputs: ReviewInputs, settings: ReviewSettings) -> list[ReviewDecision]:
    engine = _Engine(inputs, settings)
    engine.challenges_and_swaps()
    engine.set_size()
    engine.on_deck_exchange()
    return engine.decisions


class _Engine:
    def __init__(self, inputs: ReviewInputs, settings: ReviewSettings) -> None:
        self.inputs = inputs
        self.settings = settings
        self.cards = inputs.scorecards.cards
        self.decisions: list[ReviewDecision] = []
        # Tier after the decisions so far (strategy_id -> status).
        self.tier = {sid: status for sid, (status, _) in inputs.members.items()}

    # ------------------------------------------------------------------ helpers

    def _ids(self, status: str) -> list[str]:
        return sorted(sid for sid, s in self.tier.items() if s == status)

    def _score(self, sid: str) -> Decimal | None:
        card = self.cards.get(sid)
        return card.score if card is not None else None

    def _tenure_days(self, sid: str) -> int:
        _, since = self.inputs.members[sid]
        return (self.inputs.now - since).days

    def _is_candidate(self, sid: str) -> bool:
        return self.inputs.governance.get(sid) == _CANDIDATE_DB_STATE

    def _shadow_ok(self, sid: str) -> bool:
        if not self._is_candidate(sid):
            return True
        card = self.cards.get(sid)
        return (
            card is not None
            and (card.forward_days or 0) >= self.settings.min_shadow_days
            and (card.forward_trades or 0) >= self.settings.min_shadow_trades
        )

    def _turnover_cost(self, incumbent: str) -> Decimal:
        invested = self.inputs.invested_fraction.get(incumbent, Decimal("1"))
        return _RETURN_SCORE_WEIGHT * invested * self.settings.turnover_cost_bps / Decimal("10000")

    def _ranked(self, ids: list[str], *, reverse: bool = True) -> list[str]:
        """Scored ids, best first (or weakest first); unscored ids are left out."""
        scored = [sid for sid in ids if self._score(sid) is not None]
        return sorted(
            scored,
            key=lambda sid: ((self._score(sid) or Decimal("0")) * (-1 if reverse else 1), sid),
        )

    def _add(self, decision_type: ReviewDecisionType, sid: str, reason: str, **kw: Any) -> None:
        self.decisions.append(
            ReviewDecision(
                review_id=self.inputs.review_id,
                reviewed_at=self.inputs.now,
                decision_type=decision_type,
                strategy_id=sid,
                strategy_score=self._score(sid),
                reason=reason,
                **kw,
            )
        )

    # ------------------------------------------------------------------ 1. swaps

    def challenges_and_swaps(self) -> None:
        s = self.settings
        swaps = 0
        swapped_incumbents: set[str] = set()
        for challenger in self._ranked(self._ids(ON_DECK)):
            best: tuple[Decimal, str, Decimal] | None = None  # (edge, incumbent, slot score)
            best_eligible: tuple[Decimal, str, Decimal] | None = None
            for incumbent in self._ranked(self._ids(ACTIVE), reverse=False):
                if incumbent in swapped_incumbents:
                    continue
                incumbent_score = self._score(incumbent)
                slot = self.inputs.scorecards.score_for_slot(challenger, replacing=incumbent)
                if incumbent_score is None or slot is None or incumbent_score <= 0:
                    continue
                edge = (slot - self._turnover_cost(incumbent) - incumbent_score) / incumbent_score
                if edge < s.swap_margin:
                    continue
                if best is None or edge > best[0]:
                    best = (edge, incumbent, slot)
                if self._tenure_days(incumbent) >= s.min_tenure_days and (
                    best_eligible is None or edge > best_eligible[0]
                ):
                    best_eligible = (edge, incumbent, slot)
            if best is None:
                continue

            run = streak(challenger, self.inputs.prior_challengers)
            edge, incumbent, slot = best_eligible or best
            guardrails: dict[str, Any] = {
                "margin": True,
                "edge": float(edge),
                "streak": run >= s.swap_consecutive,
                "streak_count": run,
                "swap_review": self.inputs.swap_eligible,
                "tenure": best_eligible is not None,
                "incumbent_tenure_days": self._tenure_days(incumbent),
                "shadow": self._shadow_ok(challenger),
                "cap": swaps < s.max_swaps_per_review,
            }
            failed = [
                name
                for name in ("streak", "swap_review", "tenure", "shadow", "cap")
                if not guardrails[name]
            ]
            common: dict[str, Any] = dict(
                counterpart_id=incumbent,
                counterpart_score=self._score(incumbent),
                margin=float(edge),
                streak=run,
                guardrails=guardrails,
            )
            if failed:
                self._add(
                    ReviewDecisionType.CHALLENGE,
                    challenger,
                    f"held_by_{failed[0]}",
                    from_status=ON_DECK,
                    to_status=ON_DECK,
                    **common,
                )
                continue
            swaps += 1
            swapped_incumbents.add(incumbent)
            self.tier[challenger] = ACTIVE
            self.tier[incumbent] = MembershipStatus.WINDING_DOWN.value
            self._add(
                ReviewDecisionType.SWAP,
                challenger,
                "challenger_beat_incumbent",
                from_status=ON_DECK,
                to_status=ACTIVE,
                **common,
            )

    # ------------------------------------------------------------------ 2. set size

    def set_size(self) -> None:
        s = self.settings
        # Drop actives below the floor (weakest first) while above min_active.
        for sid in self._ranked(self._ids(ACTIVE), reverse=False):
            score = self._score(sid)
            if score is None or score >= s.score_floor:
                continue
            run = streak(sid, self.inputs.prior_below_floor)
            guardrails = {
                "streak": run >= s.swap_consecutive,
                "streak_count": run,
                "tenure": self._tenure_days(sid) >= s.min_tenure_days,
                "min_active": len(self._ids(ACTIVE)) > s.min_active,
            }
            failed = [n for n in ("streak", "tenure", "min_active") if not guardrails[n]]
            if failed:
                self._add(
                    ReviewDecisionType.KEEP,
                    sid,
                    f"below_floor_held_by_{failed[0]}",
                    from_status=ACTIVE,
                    to_status=ACTIVE,
                    streak=run,
                    guardrails=guardrails,
                )
                continue
            self.tier[sid] = MembershipStatus.WINDING_DOWN.value
            self._add(
                ReviewDecisionType.DROP_SEAT,
                sid,
                "below_score_floor",
                from_status=ACTIVE,
                to_status=MembershipStatus.WINDING_DOWN.value,
                streak=run,
                guardrails=guardrails,
            )

        # Fill open seats from on-deck: best first, floor waived below min_active.
        for sid in self._ranked(self._ids(ON_DECK)):
            active_count = len(self._ids(ACTIVE))
            if active_count >= s.max_active:
                break
            score = self._score(sid) or Decimal("0")
            below_min = active_count < s.min_active
            if not below_min and score < s.score_floor:
                break
            if not self._shadow_ok(sid):
                continue
            self.tier[sid] = ACTIVE
            self._add(
                ReviewDecisionType.ADD_SEAT,
                sid,
                "below_min_active" if below_min else "open_seat",
                from_status=ON_DECK,
                to_status=ACTIVE,
                guardrails={"floor": score >= s.score_floor, "below_min": below_min},
            )

    # ------------------------------------------------------------------ 3. on-deck

    def on_deck_exchange(self) -> None:
        s = self.settings

        def demote(sid: str, reason: str, **kw: Any) -> None:
            to_status = BENCH if self._is_candidate(sid) else MembershipStatus.INACTIVE.value
            self.tier[sid] = to_status
            self._add(
                ReviewDecisionType.DEMOTE_ON_DECK,
                sid,
                reason,
                from_status=ON_DECK,
                to_status=to_status,
                **kw,
            )

        def promote(sid: str, reason: str, **kw: Any) -> None:
            self.tier[sid] = ON_DECK
            self._add(
                ReviewDecisionType.PROMOTE_ON_DECK,
                sid,
                reason,
                from_status=BENCH,
                to_status=ON_DECK,
                **kw,
            )

        # Over the cap: the weakest on-deck members leave (unscored first).
        on_deck = self._ids(ON_DECK)
        excess = len(on_deck) - s.max_on_deck
        if excess > 0:
            unscored = [sid for sid in on_deck if self._score(sid) is None]
            for sid in (unscored + self._ranked(on_deck, reverse=False))[:excess]:
                demote(sid, "on_deck_over_max")

        bench = self._ranked(self._ids(BENCH))
        # Open slots go to the best bench members.
        while bench and len(self._ids(ON_DECK)) < s.max_on_deck:
            promote(bench.pop(0), "open_on_deck_slot")

        # Exchanges: best bench vs weakest on-deck past its tenure.
        while bench:
            candidates = [
                sid
                for sid in self._ranked(self._ids(ON_DECK), reverse=False)
                if self._tenure_days(sid) >= s.on_deck_min_tenure_days
                and self.inputs.members.get(sid, ("", self.inputs.now))[0] == ON_DECK
            ]
            if not candidates:
                break
            weakest, challenger = candidates[0], bench[0]
            weakest_score = self._score(weakest) or Decimal("0")
            challenger_score = self._score(challenger) or Decimal("0")
            if weakest_score <= 0:
                edge = Decimal("1")
            else:
                edge = (challenger_score - weakest_score) / weakest_score
            if edge < s.swap_margin:
                break
            bench.pop(0)
            kw: dict[str, Any] = dict(counterpart_score=weakest_score, margin=float(edge), streak=1)
            demote(weakest, "replaced_by_bench", counterpart_id=challenger, **kw)
            promote(challenger, "beat_weakest_on_deck", counterpart_id=weakest, **kw)
