"""
Bench review (portfolio rotation step 3).

One pass keeps the candidate pool small, diverse and current:

  1. Re-simulate every tracked strategy on one recent window (ACTIVE, ON_DECK,
     BENCH, and pending candidates that research produced but nobody reviewed).
  2. Group near-duplicates: daily re-sim returns correlated at or above
     bench_correlation_threshold (single linkage, across families).
  3. One champion per group. ACTIVE / ON_DECK members are protected — never pruned
     here — and always champion their group, so a bench strategy duplicating one of
     them is retired (the higher tier has better evidence).
  4. Admission: a pending candidate joins the bench only if it is its group's
     champion (novel, or better than the members it duplicates) and scores at least
     bench_score_floor.
  5. Expiry: bench members are retired after bench_floor_strikes consecutive reviews
     below the floor, or after bench_max_idle_days on the bench without reaching
     on-deck.
  6. Cap: above max_bench_strategies the lowest-scored are retired (a newcomer loses
     ties to an incumbent).

Retirement is governance `retired` (terminal) by the system_bench role, which may only
retire candidates. Every reviewed strategy gets a bench_evaluations row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.active_portfolio_service import (
    ActivePortfolioService,
)
from autonomous_trading_platform.application.services.bench_resimulation_service import (
    BenchResimulationService,
    BenchWindow,
    ResimOutcome,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.contracts.governance.bench import (
    PENDING_TIER,
    BenchDecision,
    BenchEvaluation,
    BenchReviewResult,
)
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.repositories.core.bench_evaluation_repository import (
    BenchEvaluationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)

logger = get_logger(__name__)

BENCH_ACTOR = "bench_manager"
BENCH_ROLE = "system_bench"
_CANDIDATE_DB_STATE = "candidate"
# Fewer overlapping days than this and a correlation is not trusted (treated as unrelated).
MIN_OVERLAP_DAYS = 10
_PROTECTED = (MembershipStatus.ACTIVE, MembershipStatus.ON_DECK)
# Higher tier wins a group; pending loses ties to bench.
_TIER_RANK = {
    MembershipStatus.ACTIVE.value: 3,
    MembershipStatus.ON_DECK.value: 2,
    MembershipStatus.BENCH.value: 1,
    PENDING_TIER: 0,
}


@dataclass(frozen=True)
class BenchSettings:
    max_bench: int
    correlation_threshold: float
    score_floor: Decimal
    floor_strikes: int
    max_idle_days: int

    @classmethod
    def from_row(cls, row: Any) -> BenchSettings:
        return cls(
            max_bench=max(int(row.max_bench_strategies or 0), 0),
            correlation_threshold=float(row.bench_correlation_threshold or 0.85),
            score_floor=Decimal(
                str(row.bench_score_floor if row.bench_score_floor is not None else 1)
            ),
            floor_strikes=max(int(row.bench_floor_strikes or 0), 1),
            max_idle_days=max(int(row.bench_max_idle_days or 0), 1),
        )


class BenchReviewService:
    def __init__(
        self,
        session: Session,
        *,
        resimulation: BenchResimulationService,
        portfolio: ActivePortfolioService | None = None,
        governance: StrategyGovernanceService | None = None,
    ) -> None:
        self._session = session
        self._resim = resimulation
        self._portfolio = portfolio or ActivePortfolioService(session)
        self._governance = governance or StrategyGovernanceService(session=session)
        self._memberships = PortfolioMembershipRepository(session)
        self._evaluations = BenchEvaluationRepository(session)
        # Re-sim outcomes of the latest review, reused by the portfolio review (step 4).
        self.last_outcomes: dict[str, ResimOutcome] = {}

    # ------------------------------------------------------------------

    def review(self, *, window: BenchWindow, now: datetime) -> BenchReviewResult:
        settings = BenchSettings.from_row(
            OperatorSettingsRepository(self._session).get_or_create_default()
        )
        review_id = f"bench_{now:%Y%m%dT%H%M%S}"
        tiers = self._tiers()
        outcomes = self._resim.resimulate(sorted(tiers), window=window, review_id=review_id)
        self.last_outcomes = outcomes
        members = {row.strategy_id: row for row in self._memberships.get_all()}

        evaluations: dict[str, BenchEvaluation] = {}
        for sid in sorted(tiers):
            evaluations[sid] = self._evaluation(
                review_id, sid, tiers[sid], outcomes[sid], window=window, now=now
            )

        reviewable = [sid for sid in sorted(tiers) if outcomes[sid].ok]
        groups = _group(
            {sid: outcomes[sid].daily_returns for sid in reviewable},
            threshold=settings.correlation_threshold,
        )
        correlations = _max_correlations({sid: outcomes[sid].daily_returns for sid in reviewable})

        for index, group in enumerate(groups):
            group_id = f"{review_id}:g{index:02d}"
            champion = sorted(group, key=lambda sid: _rank_key(tiers[sid], outcomes[sid], sid))[0]
            for sid in group:
                ev = evaluations[sid]
                ev.group_id = group_id
                ev.is_champion = sid == champion
                best = correlations.get(sid)
                if best is not None:
                    ev.correlated_with, ev.max_correlation = best
                self._decide(ev, champion=champion, group_size=len(group), settings=settings)

        self._apply_expiry(evaluations, members=members, settings=settings, now=now)
        self._apply_cap(evaluations, settings=settings)
        self._persist(evaluations.values(), now=now)

        result = BenchReviewResult(
            review_id=review_id,
            reviewed_at=now,
            window_start=window.start_date,
            window_end=window.end_date,
            evaluations=list(evaluations.values()),
            admitted=sorted(s for s, e in evaluations.items() if e.decision == BenchDecision.ADMIT),
            retired=sorted(s for s, e in evaluations.items() if e.decision == BenchDecision.RETIRE),
            bench=sorted(
                s
                for s, e in evaluations.items()
                if e.decision in (BenchDecision.ADMIT, BenchDecision.KEEP)
                or (e.decision == BenchDecision.SKIPPED and e.tier == MembershipStatus.BENCH.value)
            ),
            group_count=len(groups),
        )
        logger.info(
            "bench_review.completed",
            extra={
                "review_id": review_id,
                "reviewed": len(evaluations),
                "admitted": result.admitted,
                "retired": result.retired,
                "bench_size": len(result.bench),
                "groups": result.group_count,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Who is reviewed
    # ------------------------------------------------------------------

    def _tiers(self) -> dict[str, str]:
        """strategy_id -> tier for everything the review looks at."""
        tiers: dict[str, str] = {}
        for row in self._memberships.get_all():
            status = MembershipStatus(row.status)
            if status in (*_PROTECTED, MembershipStatus.BENCH):
                tiers[row.strategy_id] = status.value
        states = self._portfolio.latest_governance_states()
        for sid in self._portfolio.on_deck_eligible_strategy_ids():
            if sid not in tiers and states.get(sid) == _CANDIDATE_DB_STATE:
                member = self._memberships.get(sid)
                if member is None or member.status == MembershipStatus.INACTIVE.value:
                    tiers[sid] = PENDING_TIER
        return tiers

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def _evaluation(
        self,
        review_id: str,
        sid: str,
        tier: str,
        outcome: ResimOutcome,
        *,
        window: BenchWindow,
        now: datetime,
    ) -> BenchEvaluation:
        previous = self._evaluations.latest_for_strategy(sid)
        prior_strikes = previous.floor_strikes if previous is not None else 0
        return BenchEvaluation(
            review_id=review_id,
            strategy_id=sid,
            reviewed_at=now,
            tier=tier,
            strategy_type=outcome.strategy_type,
            window_start=window.start_date,
            window_end=window.end_date,
            resim_run_id=outcome.run_id,
            trade_count=outcome.trade_count,
            total_return=outcome.total_return,
            sharpe_ratio=outcome.sharpe_ratio,
            max_drawdown=outcome.max_drawdown,
            win_rate=outcome.win_rate,
            score=outcome.score,
            floor_strikes=prior_strikes,
            decision=BenchDecision.SKIPPED,
            reason=f"resim_failed:{outcome.error}"[:64] if not outcome.ok else "pending_decision",
        )

    def _decide(
        self, ev: BenchEvaluation, *, champion: str, group_size: int, settings: BenchSettings
    ) -> None:
        below_floor = ev.score is not None and ev.score < settings.score_floor
        ev.floor_strikes = ev.floor_strikes + 1 if below_floor else 0
        if ev.tier in (MembershipStatus.ACTIVE.value, MembershipStatus.ON_DECK.value):
            ev.decision, ev.reason = BenchDecision.PROTECTED, ev.tier
            return
        if ev.strategy_id != champion:
            ev.decision, ev.reason = BenchDecision.RETIRE, "redundant"
            return
        if ev.tier == PENDING_TIER:
            if below_floor:
                ev.decision, ev.reason = BenchDecision.RETIRE, "below_score_floor"
            else:
                reason = "novel" if group_size == 1 else "better_than_group"
                ev.decision, ev.reason = BenchDecision.ADMIT, reason
            return
        ev.decision, ev.reason = BenchDecision.KEEP, "champion"

    def _apply_expiry(
        self,
        evaluations: dict[str, BenchEvaluation],
        *,
        members: dict[str, Any],
        settings: BenchSettings,
        now: datetime,
    ) -> None:
        idle_cutoff = now - timedelta(days=settings.max_idle_days)
        for sid, ev in evaluations.items():
            if ev.decision != BenchDecision.KEEP:
                continue
            if ev.floor_strikes >= settings.floor_strikes:
                ev.decision, ev.reason = BenchDecision.RETIRE, "score_floor_strikes"
                continue
            member = members.get(sid)
            if member is not None and member.since <= idle_cutoff:
                ev.decision, ev.reason = BenchDecision.RETIRE, "idle_on_bench"

    def _apply_cap(
        self, evaluations: dict[str, BenchEvaluation], *, settings: BenchSettings
    ) -> None:
        benched = [
            ev
            for ev in evaluations.values()
            if ev.decision in (BenchDecision.KEEP, BenchDecision.ADMIT)
            or (ev.decision == BenchDecision.SKIPPED and ev.tier == MembershipStatus.BENCH.value)
        ]
        excess = len(benched) - settings.max_bench
        if excess <= 0:
            return
        # Weakest first; on equal scores a newcomer goes before an incumbent. Skipped
        # members (no score this review) are never cut for the cap.
        candidates = [ev for ev in benched if ev.decision != BenchDecision.SKIPPED]
        candidates.sort(
            key=lambda ev: (
                ev.score if ev.score is not None else Decimal("0"),
                _TIER_RANK.get(ev.tier, 0),
                ev.strategy_id,
            )
        )
        for ev in candidates[:excess]:
            ev.decision, ev.reason = BenchDecision.RETIRE, "over_bench_cap"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, evaluations: Any, *, now: datetime) -> None:
        for ev in evaluations:
            if ev.decision == BenchDecision.ADMIT:
                self._portfolio.set_status(
                    ev.strategy_id,
                    MembershipStatus.BENCH,
                    f"bench_{ev.reason}",
                    actor=BENCH_ACTOR,
                    now=now,
                    quality_score=ev.score,
                )
            elif ev.decision == BenchDecision.RETIRE and not self._retire(ev, now=now):
                ev.decision, ev.reason = BenchDecision.SKIPPED, "retire_failed"
            self._evaluations.insert(_row(ev))
        self._session.flush()

    def _retire(self, ev: BenchEvaluation, *, now: datetime) -> bool:
        try:
            self._governance.transition(
                strategy_id=ev.strategy_id,
                to_state="retired",
                reason=f"bench_{ev.reason}",
                updated_by=BENCH_ACTOR,
                actor_role=BENCH_ROLE,
                now=now,
            )
        except (LookupError, PermissionError, ValueError) as exc:
            logger.warning(
                "bench_review.retire_failed",
                extra={"strategy_id": ev.strategy_id, "error": str(exc)},
            )
            return False
        member = self._memberships.get(ev.strategy_id)
        if member is not None and member.status != MembershipStatus.INACTIVE.value:
            self._portfolio.set_status(
                ev.strategy_id,
                MembershipStatus.INACTIVE,
                f"bench_{ev.reason}",
                actor=BENCH_ACTOR,
                now=now,
                quality_score=ev.score,
            )
        return True


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------


def _rank_key(tier: str, outcome: ResimOutcome, sid: str) -> tuple[int, Decimal, int, str]:
    """Group champion sort key, best first.

    Protected tiers (ACTIVE, then ON_DECK) always win. Between bench members and
    pending candidates the higher score wins — a newcomer that beats the bench member
    it duplicates replaces it — and the incumbent wins a tie.
    """
    protected = {MembershipStatus.ACTIVE.value: 2, MembershipStatus.ON_DECK.value: 1}
    return (
        -protected.get(tier, 0),
        -(outcome.score or Decimal("0")),
        -_TIER_RANK.get(tier, 0),
        sid,
    )


def correlation(a: pd.Series, b: pd.Series) -> float | None:
    """Pearson correlation of two daily return series on their shared days."""
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(joined) < MIN_OVERLAP_DAYS:
        return None
    x, y = joined.iloc[:, 0], joined.iloc[:, 1]
    if x.std() == 0 or y.std() == 0:
        return None
    value = float(x.corr(y))
    return None if pd.isna(value) else value


def _group(returns: dict[str, pd.Series], *, threshold: float) -> list[list[str]]:
    """Single-linkage groups over pairs correlated at or above threshold."""
    ids = sorted(returns)
    parent = {sid: sid for sid in ids}

    def find(sid: str) -> str:
        while parent[sid] != sid:
            parent[sid] = parent[parent[sid]]
            sid = parent[sid]
        return sid

    for a, b in combinations(ids, 2):
        value = correlation(returns[a], returns[b])
        if value is not None and value >= threshold:
            parent[find(b)] = find(a)

    groups: dict[str, list[str]] = {}
    for sid in ids:
        groups.setdefault(find(sid), []).append(sid)
    return sorted(groups.values(), key=lambda g: g[0])


def _max_correlations(returns: dict[str, pd.Series]) -> dict[str, tuple[str, float]]:
    best: dict[str, tuple[str, float]] = {}
    for a, b in combinations(sorted(returns), 2):
        value = correlation(returns[a], returns[b])
        if value is None:
            continue
        for x, y in ((a, b), (b, a)):
            if x not in best or value > best[x][1]:
                best[x] = (y, value)
    return best


def _row(ev: BenchEvaluation) -> BenchEvaluationRow:
    return BenchEvaluationRow(
        evaluation_id=uuid4(),
        review_id=ev.review_id,
        strategy_id=ev.strategy_id,
        reviewed_at=ev.reviewed_at,
        tier=ev.tier,
        strategy_type=ev.strategy_type,
        window_start=ev.window_start,
        window_end=ev.window_end,
        resim_run_id=ev.resim_run_id,
        trade_count=ev.trade_count,
        total_return=ev.total_return,
        sharpe_ratio=ev.sharpe_ratio,
        max_drawdown=ev.max_drawdown,
        win_rate=ev.win_rate,
        score=float(ev.score) if ev.score is not None else None,
        group_id=ev.group_id,
        is_champion=ev.is_champion,
        max_correlation=ev.max_correlation,
        correlated_with=ev.correlated_with,
        floor_strikes=ev.floor_strikes,
        decision=ev.decision.value,
        reason=ev.reason,
    )
