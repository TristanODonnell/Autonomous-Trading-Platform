"""
Portfolio review (portfolio rotation step 4).

Weekly, right after the bench review (whose shared-window re-sims it reuses):

  1. Scorecards for every ACTIVE / ON_DECK / BENCH strategy (PortfolioScorecardService).
  2. Decisions (portfolio_review_decisions.decide): challenges and swaps (only on the
     monthly, swap-eligible review), set size, on-deck <-> bench.
  3. mode `auto`: apply them — a candidate entering the active set is promoted to
     paper by the system_portfolio role (promotion rules still apply; a rejected
     promotion cancels that move), membership changes carry the review_id, then the
     active set is re-weighted by QualityBasedReallocationService (which honours
     auto_rebalance_enabled). mode `advisory`: record only.

Everything is persisted: portfolio_reviews, portfolio_scorecards,
portfolio_review_decisions.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.bench_resimulation_service import (
    ResimOutcome,
)
from autonomous_trading_platform.application.services.portfolio_review_decisions import (
    ReviewInputs,
    ReviewSettings,
    decide,
)
from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
    PortfolioScorecardService,
)
from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.governance.portfolio_review import (
    PortfolioReviewMode,
    PortfolioReviewResult,
    ReviewDecision,
    ReviewDecisionType,
    Scorecard,
)
from autonomous_trading_platform.observability.logging import get_logger
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
from autonomous_trading_platform.storage.sor.repositories.core.strategy_sleeve_repository import (
    StrategySleeveRepository,
)

logger = get_logger(__name__)

REVIEW_ACTOR = "portfolio_review"
REVIEW_ROLE = "system_portfolio"
_CANDIDATE_DB_STATE = "candidate"
_PAPER_DB_STATE = "approved_for_paper_trading"
_REVIEWED_TIERS = (MembershipStatus.ACTIVE, MembershipStatus.ON_DECK, MembershipStatus.BENCH)
_CHALLENGE_TYPES = {ReviewDecisionType.CHALLENGE.value, ReviewDecisionType.SWAP.value}
_BELOW_FLOOR_TYPES = {ReviewDecisionType.KEEP.value, ReviewDecisionType.DROP_SEAT.value}
# Reviews closer together than this count once toward a streak (weekly cadence).
STREAK_MIN_GAP_DAYS = 6


def review_mode(session: Session) -> PortfolioReviewMode:
    row = OperatorSettingsRepository(session).get_or_create_default()
    try:
        return PortfolioReviewMode(str(row.portfolio_review_mode or "off"))
    except ValueError:
        logger.warning("portfolio_review.invalid_mode", extra={"mode": row.portfolio_review_mode})
        return PortfolioReviewMode.OFF


class PortfolioReviewService:
    def __init__(
        self,
        session: Session,
        *,
        scorecards: PortfolioScorecardService | None = None,
        portfolio: ActivePortfolioService | None = None,
        governance: StrategyGovernanceService | None = None,
        reallocation: QualityBasedReallocationService | None = None,
    ) -> None:
        self._session = session
        self._scorecards = scorecards or PortfolioScorecardService(session)
        self._portfolio = portfolio or ActivePortfolioService(session)
        self._governance = governance or StrategyGovernanceService(session=session)
        self._reallocation = reallocation
        self._reviews = PortfolioReviewRepository(session)
        self._memberships = PortfolioMembershipRepository(session)
        self._sleeves = StrategySleeveRepository(session)

    def run(
        self,
        *,
        now: datetime,
        resim_outcomes: dict[str, ResimOutcome] | None = None,
        bench_review_id: str | None = None,
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> PortfolioReviewResult | None:
        """Run one review as of `now`. Returns None when the review mode is off."""
        mode = review_mode(self._session)
        if mode == PortfolioReviewMode.OFF:
            return None
        row = OperatorSettingsRepository(self._session).get_or_create_default()
        settings = ReviewSettings.from_row(row)
        review_id = f"review_{now:%Y%m%dT%H%M%S}"
        swap_eligible = self._swap_eligible(now=now, interval_days=settings.swap_interval_days)

        scorecards = self._scorecards.build(
            review_id=review_id, now=now, resim_outcomes=resim_outcomes
        )
        members = {
            m.strategy_id: (m.status, m.since)
            for m in self._memberships.get_by_statuses([t.value for t in _REVIEWED_TIERS])
        }
        governance = self._portfolio.latest_governance_states()
        prior_challengers, prior_below_floor = self._prior_sets(
            now=now, depth=settings.swap_consecutive
        )
        decisions = decide(
            ReviewInputs(
                review_id=review_id,
                now=now,
                swap_eligible=swap_eligible,
                scorecards=scorecards,
                members=members,
                governance=governance,
                invested_fraction=self._invested_fractions(
                    [sid for sid, (status, _) in members.items() if status == "active"]
                ),
                prior_challengers=prior_challengers,
                prior_below_floor=prior_below_floor,
            ),
            settings,
        )

        if mode == PortfolioReviewMode.AUTO:
            for decision in decisions:
                self._apply(decision, governance=governance, now=now)
            decisions.append(self._reweight(review_id=review_id, now=now))

        result = PortfolioReviewResult(
            review_id=review_id,
            reviewed_at=now,
            mode=mode,
            swap_eligible=swap_eligible,
            window_start=window_start,
            window_end=window_end,
            bench_review_id=bench_review_id,
            scorecards=scorecards.ranked(),
            decisions=decisions,
        )
        self._persist(result)
        logger.info(
            "portfolio_review.completed",
            extra={
                "review_id": review_id,
                "mode": mode.value,
                "swap_eligible": swap_eligible,
                "scorecards": len(result.scorecards),
                "decisions": {d.decision_type.value: d.strategy_id for d in decisions},
            },
        )
        return result

    # ------------------------------------------------------------------ inputs

    def _swap_eligible(self, *, now: datetime, interval_days: int) -> bool:
        """Monthly review: none swap-eligible within the last interval_days."""
        last = self._reviews.last_swap_eligible(before=now)
        return last is None or (now - last.reviewed_at).days >= interval_days

    def _prior_sets(self, *, now: datetime, depth: int) -> tuple[list[set[str]], list[set[str]]]:
        """Challenger / below-floor ids of earlier reviews, one per week, most recent first.

        Streaks count weekly reviews: an extra review within STREAK_MIN_GAP_DAYS of the
        next counted one (e.g. right after a research tick) neither counts nor breaks.
        """
        reviews = []
        anchor = now
        for review in self._reviews.recent_reviews(before=now, limit=max(depth, 1) * 4):
            if (anchor - review.reviewed_at).days < STREAK_MIN_GAP_DAYS:
                continue
            reviews.append(review)
            anchor = review.reviewed_at
            if len(reviews) >= depth:
                break
        rows = self._reviews.decisions_for_reviews([r.review_id for r in reviews])
        challengers: list[set[str]] = []
        below_floor: list[set[str]] = []
        for review in reviews:
            mine = [r for r in rows if r.review_id == review.review_id]
            challengers.append({r.strategy_id for r in mine if r.decision_type in _CHALLENGE_TYPES})
            below_floor.append(
                {r.strategy_id for r in mine if r.decision_type in _BELOW_FLOOR_TYPES}
            )
        return challengers, below_floor

    def _invested_fractions(self, active_ids: list[str]) -> dict[str, Decimal]:
        fractions: dict[str, Decimal] = {}
        for sid in active_ids:
            snapshot = self._sleeves.get_latest_snapshot(sid)
            if snapshot is None or not snapshot.allocated_capital:
                continue
            fraction = Decimal(str(snapshot.market_value)) / Decimal(
                str(snapshot.allocated_capital)
            )
            fractions[sid] = min(max(fraction, Decimal("0")), Decimal("1"))
        return fractions

    # ------------------------------------------------------------------ apply

    def _apply(
        self, decision: ReviewDecision, *, governance: dict[str, str], now: datetime
    ) -> None:
        kind = decision.decision_type
        sid = decision.strategy_id
        if kind in (ReviewDecisionType.SWAP, ReviewDecisionType.ADD_SEAT):
            if governance.get(sid) == _CANDIDATE_DB_STATE and not self._promote(decision, now=now):
                return
            if kind == ReviewDecisionType.SWAP and decision.counterpart_id:
                self._leave_active(decision.counterpart_id, decision, now=now)
            self._move(sid, MembershipStatus.ACTIVE, decision, now=now)
        elif kind == ReviewDecisionType.DROP_SEAT:
            self._leave_active(sid, decision, now=now)
        elif kind in (ReviewDecisionType.PROMOTE_ON_DECK, ReviewDecisionType.DEMOTE_ON_DECK):
            self._move(sid, MembershipStatus(decision.to_status or "inactive"), decision, now=now)
        else:
            return
        decision.applied = True

    def _promote(self, decision: ReviewDecision, *, now: datetime) -> bool:
        """Governance candidate -> paper for a review winner; False if governance refuses."""
        try:
            self._governance.transition(
                strategy_id=decision.strategy_id,
                to_state=_PAPER_DB_STATE,
                reason=f"portfolio review {decision.review_id}: {decision.decision_type.value}",
                updated_by=REVIEW_ACTOR,
                actor_role=REVIEW_ROLE,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "portfolio_review.promotion_rejected",
                extra={"strategy_id": decision.strategy_id, "error": str(exc)},
            )
            decision.guardrails = {**decision.guardrails, "governance": False, "error": str(exc)}
            decision.reason = f"{decision.reason}:governance_rejected"
            return False
        decision.guardrails = {**decision.guardrails, "governance": True}
        return True

    def _leave_active(self, sid: str, decision: ReviewDecision, *, now: datetime) -> None:
        """Out of the active set: sell out first if holding, else straight to on-deck."""
        status = (
            MembershipStatus.WINDING_DOWN
            if self._sleeves.get_positions(sid)
            else MembershipStatus.ON_DECK
        )
        self._move(sid, status, decision, now=now)

    def _move(
        self, sid: str, status: MembershipStatus, decision: ReviewDecision, *, now: datetime
    ) -> None:
        self._portfolio.set_status(
            sid,
            status,
            f"review_{decision.decision_type.value}",
            actor=REVIEW_ACTOR,
            now=now,
            quality_score=decision.strategy_score if sid == decision.strategy_id else None,
            review_id=decision.review_id,
        )

    def _reweight(self, *, review_id: str, now: datetime) -> ReviewDecision:
        """Weights for the (new) active set from the existing allocation service."""
        self._session.flush()
        guardrails: dict[str, Any] = {}
        try:
            reallocation = self._reallocation or QualityBasedReallocationService(
                session=self._session
            )
            outcome = reallocation.rebalance(
                actor=REVIEW_ACTOR, trigger_source="portfolio_review", now=now
            )
            applied = outcome.skipped_reason is None
            reason = outcome.skipped_reason or ("reweighted" if outcome.changed else "unchanged")
            guardrails = {
                "changes": outcome.allocation_changes_count,
                "after": {k: float(v) for k, v in outcome.after_allocation.items()},
            }
        except Exception as exc:
            logger.warning("portfolio_review.reweight_failed", extra={"error": str(exc)})
            applied, reason = False, "reweight_failed"
            guardrails = {"error": str(exc)}
        return ReviewDecision(
            review_id=review_id,
            reviewed_at=now,
            decision_type=ReviewDecisionType.REWEIGHT,
            strategy_id="__portfolio__",
            guardrails=guardrails,
            applied=applied,
            reason=reason[:128],
        )

    # ------------------------------------------------------------------ persist

    def _persist(self, result: PortfolioReviewResult) -> None:
        self._reviews.insert_review(
            PortfolioReviewRow(
                review_id=result.review_id,
                reviewed_at=result.reviewed_at,
                mode=result.mode.value,
                swap_eligible=result.swap_eligible,
                window_start=result.window_start,
                window_end=result.window_end,
                bench_review_id=result.bench_review_id,
                scorecard_count=len(result.scorecards),
                decision_count=len(result.decisions),
            )
        )
        for card in result.scorecards:
            self._reviews.insert_scorecard(_scorecard_row(card))
        for decision in result.decisions:
            self._reviews.insert_decision(
                PortfolioReviewDecisionRow(
                    decision_id=uuid4(),
                    review_id=decision.review_id,
                    reviewed_at=decision.reviewed_at,
                    decision_type=decision.decision_type.value,
                    strategy_id=decision.strategy_id,
                    counterpart_id=decision.counterpart_id,
                    from_status=decision.from_status,
                    to_status=decision.to_status,
                    strategy_score=_f(decision.strategy_score),
                    counterpart_score=_f(decision.counterpart_score),
                    margin=decision.margin,
                    streak=decision.streak,
                    guardrails=decision.guardrails,
                    applied=decision.applied,
                    reason=decision.reason[:128],
                )
            )


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _scorecard_row(card: Scorecard) -> PortfolioScorecardRow:
    return PortfolioScorecardRow(
        scorecard_id=uuid4(),
        review_id=card.review_id,
        strategy_id=card.strategy_id,
        reviewed_at=card.reviewed_at,
        tier=card.tier,
        strategy_type=card.strategy_type,
        forward_source=card.forward_source.value if card.forward_source else None,
        forward_score=_f(card.forward_score),
        forward_weight=float(card.forward_weight),
        forward_days=card.forward_days,
        forward_trades=card.forward_trades,
        resim_score=_f(card.resim_score),
        resim_weight=float(card.resim_weight),
        backtest_score=_f(card.backtest_score),
        backtest_weight=float(card.backtest_weight),
        backtest_age_days=card.backtest_age_days,
        evidence_score=_f(card.evidence_score),
        decay_penalty=float(card.decay_penalty),
        health_status=card.health_status,
        health_penalty=float(card.health_penalty),
        mean_correlation=card.mean_correlation,
        correlation_penalty=float(card.correlation_penalty),
        blocked_ratio=card.blocked_ratio,
        blocked_penalty=float(card.blocked_penalty),
        regime_label=card.regime_label,
        score=_f(card.score),
        rank=card.rank,
    )
