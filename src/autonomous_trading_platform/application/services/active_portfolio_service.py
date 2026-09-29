"""
Active portfolio set — which eligible strategies hold capital, and how much.

Governance decides eligibility (approved for paper/live). This service decides
membership: a dynamic set of ACTIVE strategies bounded by the operator's
min/max, plus WINDING_DOWN strategies that left the set but still hold
positions and must trade out of them.

It also maintains the ON_DECK shadow tier (rotation step 2): up to
max_on_deck_strategies candidate or unseated approved strategies that the cycle
shadow-trades (simulated fills, no capital) to build a forward track record.

With bench management on (rotation step 3) candidates reach on-deck only from
the BENCH (admitted by the bench review), and a candidate leaving the active set
or on-deck returns to the bench instead of going inactive. Admission to and
retirement from the bench belong to BenchReviewService, not this refresh.

Selection here is a deliberately simple placeholder until the portfolio review
(rotation step 4), for both tiers: eligible incumbents keep their seat, open
seats go to the highest blended-quality eligible strategies. It never swaps a
healthy incumbent out for a better challenger — that decision belongs to the
review.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    TRADING_STATUSES,
    ActiveSetRefreshResult,
    MembershipStatus,
    MembershipTransition,
    PortfolioMember,
    StrategyBudget,
)
from autonomous_trading_platform.contracts.governance.strategy_health import StrategyHealthStatus
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipRow,
    PortfolioMembershipTransitionRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_state_repository import (
    StrategyHealthStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_sleeve_repository import (
    StrategySleeveRepository,
)

logger = get_logger(__name__)

ACTIVE_PORTFOLIO_ACTOR = "active_portfolio"
_PAPER_DB_STATE = "approved_for_paper_trading"
_LIVE_DB_STATE = "approved_for_live_trading"
_CANDIDATE_DB_STATE = "candidate"
_PCT_QUANT = Decimal("0.000001")


class ActivePortfolioService:
    def __init__(
        self,
        session: Session,
        *,
        trading_environment: TradingEnvironment = TradingEnvironment.PAPER,
        quality_score_fn: Callable[[str], Decimal] | None = None,
    ) -> None:
        self._session = session
        self._trading_environment = trading_environment
        self._memberships = PortfolioMembershipRepository(session)
        self._settings_repo = OperatorSettingsRepository(session)
        self._sleeves = StrategySleeveRepository(session)
        self._scorer: QualityBasedReallocationService | None = None
        # As-of time of the current refresh, so default scoring uses replay time.
        self._as_of: datetime | None = None
        self._quality_score_fn = quality_score_fn or self._default_quality_score

    # ------------------------------------------------------------------
    # Eligibility
    # ------------------------------------------------------------------

    def eligible_strategy_ids(self) -> list[str]:
        """Strategies allowed to hold capital right now, in id order.

        Eligible = latest governance row approved for this environment (paper also
        runs live-approved strategies), has a strategy config, operator-enabled,
        and not SUSPENDED by the health lifecycle.
        """
        allowed = (
            {_LIVE_DB_STATE}
            if self._trading_environment is TradingEnvironment.LIVE
            else {_PAPER_DB_STATE, _LIVE_DB_STATE}
        )
        return self._eligible_in_states(allowed)

    def on_deck_eligible_strategy_ids(self) -> list[str]:
        """Strategies that may be shadow-traded on-deck, in id order.

        Governance `candidate` or approved for paper/live (in any environment:
        shadow trading uses no capital), with the same config / operator-enabled /
        not-SUSPENDED filters as active eligibility. Membership filters (not
        ACTIVE, not WINDING_DOWN) are applied by refresh().
        """
        return self._eligible_in_states({_CANDIDATE_DB_STATE, _PAPER_DB_STATE, _LIVE_DB_STATE})

    def latest_governance_states(self) -> dict[str, str]:
        """Latest governance state (DB string) per strategy."""
        latest_state: dict[str, str] = {}
        for row in self._session.scalars(
            select(StrategyGovernance).order_by(
                StrategyGovernance.updated_at.desc(), StrategyGovernance.strategy_id
            )
        ):
            latest_state.setdefault(row.strategy_id, row.current_state)
        return latest_state

    def _eligible_in_states(self, allowed: set[str]) -> list[str]:
        latest_state = self.latest_governance_states()

        controls = StrategyControlStateRepository(self._session)
        health = StrategyHealthStateRepository(self._session)
        eligible = []
        for strategy_id, state in sorted(latest_state.items()):
            if state not in allowed:
                continue
            if self._session.get(StrategyConfigs, strategy_id) is None:
                continue
            if not controls.is_enabled(strategy_id):
                continue
            health_row = health.get_for_strategy(strategy_id)
            if (
                health_row is not None
                and health_row.health_status == StrategyHealthStatus.SUSPENDED
            ):
                continue
            eligible.append(strategy_id)
        return eligible

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    def refresh(
        self, *, now: datetime | None = None, actor: str = ACTIVE_PORTFOLIO_ACTOR
    ) -> ActiveSetRefreshResult:
        """Recompute the active set and the on-deck tier; record every membership change.

        Target statuses for both tiers are decided first and written once, so a
        strategy leaving the active set moves straight to on-deck (one transition).
        """
        now = now or datetime.now(UTC)
        self._as_of = now
        min_active, max_active = self._limits()
        max_on_deck = self._max_on_deck()
        members = {row.strategy_id: row for row in self._memberships.get_all()}
        prior = {sid: MembershipStatus(row.status) for sid, row in members.items()}
        eligible = self.eligible_strategy_ids()
        eligible_set = set(eligible)
        scores: dict[str, Decimal] = {}

        def score(strategy_id: str) -> Decimal:
            if strategy_id not in scores:
                scores[strategy_id] = self._quality_score_fn(strategy_id)
            return scores[strategy_id]

        def rank(ids: list[str]) -> list[str]:
            return sorted(ids, key=lambda sid: (-score(sid), sid))

        # (status, reason) per strategy whose membership may change this refresh.
        target: dict[str, tuple[MembershipStatus, str]] = {}

        # 1. Active set.
        incumbents = [sid for sid, status in prior.items() if status == MembershipStatus.ACTIVE]
        keep = rank([sid for sid in incumbents if sid in eligible_set])
        keep, over_max = keep[:max_active], keep[max_active:]
        added = rank([sid for sid in eligible if sid not in keep])[: max_active - len(keep)]
        new_active = keep + added

        for sid in added:
            previous = prior.get(sid)
            if previous == MembershipStatus.WINDING_DOWN:
                reason = "reactivated"
            elif previous == MembershipStatus.ON_DECK:
                reason = "promoted_from_on_deck"
            else:
                reason = "selected"
            target[sid] = (MembershipStatus.ACTIVE, reason)

        removed: list[str] = []
        for sid in incumbents:
            if sid in new_active:
                continue
            reason = "over_max_active" if sid in over_max else "no_longer_eligible"
            status = (
                MembershipStatus.WINDING_DOWN
                if self._sleeves.get_positions(sid)
                else MembershipStatus.INACTIVE
            )
            target[sid] = (status, reason)
            removed.append(sid)

        wind_down_completed: list[str] = []
        for sid, status in prior.items():
            if status != MembershipStatus.WINDING_DOWN or sid in new_active:
                continue
            if not self._sleeves.get_positions(sid):
                target[sid] = (MembershipStatus.INACTIVE, "wind_down_complete")
                wind_down_completed.append(sid)

        # Bench management: candidates leaving a tier return to the bench, and only
        # bench members (plus unseated approved strategies) may go on-deck.
        bench_enabled = self._bench_enabled()
        on_deck_eligible = self.on_deck_eligible_strategy_ids()
        approved = set(self._eligible_in_states({_PAPER_DB_STATE, _LIVE_DB_STATE}))
        bench_eligible = {sid for sid in on_deck_eligible if sid not in approved}

        def settle(sid: str) -> MembershipStatus:
            """Where a strategy leaving its tier goes."""
            if bench_enabled and sid in bench_eligible:
                return MembershipStatus.BENCH
            return MembershipStatus.INACTIVE

        for sid, (status, reason) in list(target.items()):
            if status == MembershipStatus.INACTIVE:
                target[sid] = (settle(sid), reason)

        # 2. On-deck tier, from strategies neither active nor still winding down.
        def status_after(sid: str) -> MembershipStatus | None:
            return target[sid][0] if sid in target else prior.get(sid)

        pool = [
            sid
            for sid in on_deck_eligible
            if status_after(sid) not in (MembershipStatus.ACTIVE, MembershipStatus.WINDING_DOWN)
            and (
                not bench_enabled
                or sid in approved
                or status_after(sid) in (MembershipStatus.BENCH, MembershipStatus.ON_DECK)
            )
        ]
        pool_set = set(pool)
        on_deck_incumbents = [
            sid
            for sid, status in prior.items()
            if status == MembershipStatus.ON_DECK and sid not in new_active
        ]
        on_deck_keep = rank([sid for sid in on_deck_incumbents if sid in pool_set])
        on_deck_keep, on_deck_over = on_deck_keep[:max_on_deck], on_deck_keep[max_on_deck:]
        open_on_deck = max(max_on_deck - len(on_deck_keep), 0)
        on_deck_added = rank([sid for sid in pool if sid not in on_deck_incumbents])[:open_on_deck]

        for sid in on_deck_added:
            # Keep the reason it left its previous tier (e.g. over_max_active).
            reason = target[sid][1] if sid in target else "selected_on_deck"
            target[sid] = (MembershipStatus.ON_DECK, reason)

        on_deck_removed: list[str] = []
        for sid in on_deck_incumbents:
            if sid in on_deck_keep:
                continue
            reason = "on_deck_over_max" if sid in on_deck_over else "on_deck_no_longer_eligible"
            target[sid] = (settle(sid), reason)
            on_deck_removed.append(sid)

        # Bench members whose governance state moved off `candidate` (retired,
        # rejected, or approved and not placed above) leave the bench.
        governance_states = self.latest_governance_states()
        for sid, status in prior.items():
            if status != MembershipStatus.BENCH or sid in target:
                continue
            if governance_states.get(sid) != _CANDIDATE_DB_STATE:
                target[sid] = (MembershipStatus.INACTIVE, "bench_no_longer_eligible")

        # 3. Persist.
        transitions: list[MembershipTransition] = []
        for sid, (status, reason) in target.items():
            if prior.get(sid) == status:
                continue
            transitions.append(self._set_status(sid, status, reason, actor, now, scores.get(sid)))

        for sid in keep + on_deck_keep:
            row = members[sid]
            row.quality_score = float(scores[sid])
            row.updated_at = now

        self._session.flush()
        winding_down = sorted(
            row.strategy_id
            for row in self._memberships.get_by_statuses([MembershipStatus.WINDING_DOWN.value])
        )
        result = ActiveSetRefreshResult(
            timestamp=now,
            active=sorted(new_active),
            winding_down=winding_down,
            added=sorted(added),
            removed=sorted(removed),
            wind_down_completed=sorted(wind_down_completed),
            eligible_count=len(eligible),
            min_active=min_active,
            max_active=max_active,
            transitions=transitions,
            on_deck=sorted(on_deck_keep + on_deck_added),
            on_deck_added=sorted(on_deck_added),
            on_deck_removed=sorted(on_deck_removed),
            max_on_deck=max_on_deck,
            bench=sorted(m.strategy_id for m in self.bench_members()),
        )
        if result.below_minimum:
            logger.warning(
                "active_portfolio.below_minimum",
                extra={
                    "active_count": len(result.active),
                    "min_active": min_active,
                    "eligible_count": len(eligible),
                },
            )
        logger.info(
            "active_portfolio.refreshed",
            extra={
                "active": result.active,
                "added": result.added,
                "removed": result.removed,
                "winding_down": result.winding_down,
                "on_deck": result.on_deck,
                "on_deck_added": result.on_deck_added,
                "on_deck_removed": result.on_deck_removed,
            },
        )
        return result

    def trading_members(self) -> list[PortfolioMember]:
        """Members the trading cycle must run: ACTIVE and WINDING_DOWN."""
        return [
            _member_contract(row)
            for row in self._memberships.get_by_statuses([s.value for s in TRADING_STATUSES])
        ]

    def bench_members(self) -> list[PortfolioMember]:
        """Admitted bench candidates (bench management, rotation step 3)."""
        return [
            _member_contract(row)
            for row in self._memberships.get_by_statuses([MembershipStatus.BENCH.value])
        ]

    def on_deck_members(self) -> list[PortfolioMember]:
        """Members the cycle shadow-trades (simulated fills, no capital)."""
        return [
            _member_contract(row)
            for row in self._memberships.get_by_statuses([MembershipStatus.ON_DECK.value])
        ]

    # ------------------------------------------------------------------
    # Budgets
    # ------------------------------------------------------------------

    def on_deck_budget_pct(self) -> Decimal:
        """Notional share of total capital each on-deck strategy is sized against.

        The share an equal-weight active seat gets (deployable / active count, or /
        max_active when nothing is active), capped by per_strategy_cap, so shadow
        results are comparable with the actives and sizing matches promotion. No
        capital is reserved: shadow sleeves never trade.
        """
        settings = self._settings_repo.get_or_create_default()
        deployable = _pct(settings.max_total_strategy_allocation_pct) or Decimal("1")
        per_strategy_cap = _pct(settings.per_strategy_cap)
        active_count = len(
            self._memberships.get_by_statuses([MembershipStatus.ACTIVE.value])
        ) or max(int(settings.max_active_strategies or 0), 1)
        share = deployable / active_count
        if per_strategy_cap is not None and per_strategy_cap > 0:
            share = min(share, per_strategy_cap)
        return max(share, Decimal("0")).quantize(_PCT_QUANT, rounding=ROUND_DOWN)

    def budgets(self, *, now: datetime | None = None) -> list[StrategyBudget]:
        """Per-member share of total capital.

        ACTIVE members start from their active allocation override (manual or
        auto-rebalance) or an equal share, are clamped to per_strategy_cap, then
        scaled down so the set never exceeds max_total_strategy_allocation_pct.
        WINDING_DOWN members get 0 — they may only sell.
        """
        now = now or datetime.now(UTC)
        settings = self._settings_repo.get_or_create_default()
        deployable = _pct(settings.max_total_strategy_allocation_pct) or Decimal("1")
        per_strategy_cap = _pct(settings.per_strategy_cap)
        overrides = AllocationOverridesRepository(self._session)

        members = self.trading_members()
        active = [m for m in members if m.status == MembershipStatus.ACTIVE]
        equal_share = deployable / len(active) if active else Decimal("0")

        weights: dict[str, tuple[Decimal, str]] = {}
        for member in active:
            override = overrides.get_active_override(strategy_id=member.strategy_id, now=now)
            if override is not None and override.max_pct_of_capital is not None:
                weight, source = Decimal(str(override.max_pct_of_capital)), "override"
            else:
                weight, source = equal_share, "equal_weight"
            if per_strategy_cap is not None and per_strategy_cap > 0:
                weight = min(weight, per_strategy_cap)
            weights[member.strategy_id] = (max(weight, Decimal("0")), source)

        total = sum((w for w, _ in weights.values()), Decimal("0"))
        scale = deployable / total if total > deployable else Decimal("1")

        budgets = [
            StrategyBudget(
                strategy_id=sid,
                status=MembershipStatus.ACTIVE,
                pct_of_capital=(weight * scale).quantize(_PCT_QUANT, rounding=ROUND_DOWN),
                source=source,
            )
            for sid, (weight, source) in weights.items()
        ]
        budgets.extend(
            StrategyBudget(
                strategy_id=m.strategy_id,
                status=MembershipStatus.WINDING_DOWN,
                pct_of_capital=Decimal("0"),
                source="winding_down",
            )
            for m in members
            if m.status == MembershipStatus.WINDING_DOWN
        )
        return sorted(budgets, key=lambda b: b.strategy_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _limits(self) -> tuple[int, int]:
        settings = self._settings_repo.get_or_create_default()
        min_active = max(int(settings.min_active_strategies or 0), 0)
        max_active = max(int(settings.max_active_strategies or 0), 1)
        if min_active > max_active:
            logger.warning(
                "active_portfolio.invalid_limits",
                extra={"min_active": min_active, "max_active": max_active},
            )
            min_active = max_active
        return min_active, max_active

    def set_status(
        self,
        strategy_id: str,
        status: MembershipStatus,
        reason: str,
        *,
        actor: str,
        now: datetime,
        quality_score: Decimal | None = None,
        review_id: str | None = None,
    ) -> MembershipTransition:
        """Record a membership change decided elsewhere (bench admission, portfolio review)."""
        return self._set_status(
            strategy_id, status, reason, actor, now, quality_score, review_id=review_id
        )

    def _bench_enabled(self) -> bool:
        settings = self._settings_repo.get_or_create_default()
        return bool(settings.bench_management_enabled)

    def _max_on_deck(self) -> int:
        settings = self._settings_repo.get_or_create_default()
        return max(int(settings.max_on_deck_strategies or 0), 0)

    def _set_status(
        self,
        strategy_id: str,
        status: MembershipStatus,
        reason: str,
        actor: str,
        now: datetime,
        quality_score: Decimal | None,
        *,
        review_id: str | None = None,
    ) -> MembershipTransition:
        existing = self._memberships.get(strategy_id)
        from_status = MembershipStatus(existing.status) if existing is not None else None
        score = float(quality_score) if quality_score is not None else None
        self._memberships.save(
            PortfolioMembershipRow(
                strategy_id=strategy_id,
                status=status.value,
                since=now,
                reason=reason,
                quality_score=score,
                updated_by=actor,
                updated_at=now,
            )
        )
        transition = MembershipTransition(
            transition_id=str(uuid4()),
            strategy_id=strategy_id,
            from_status=from_status,
            to_status=status,
            reason=reason,
            triggered_by=actor,
            quality_score=score,
            review_id=review_id,
            created_at=now,
        )
        self._memberships.insert_transition(
            PortfolioMembershipTransitionRow(
                transition_id=transition.transition_id,
                strategy_id=strategy_id,
                from_status=from_status.value if from_status is not None else None,
                to_status=status.value,
                reason=reason,
                triggered_by=actor,
                quality_score=score,
                review_id=review_id,
                created_at=now,
            )
        )
        return transition

    def _default_quality_score(self, strategy_id: str) -> Decimal:
        if self._scorer is None:
            self._scorer = QualityBasedReallocationService(session=self._session)
        return self._scorer.blended_quality_score(strategy_id, now=self._as_of).blended_score


def _pct(value: float | Decimal | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _member_contract(row: PortfolioMembershipRow) -> PortfolioMember:
    return PortfolioMember(
        strategy_id=row.strategy_id,
        status=MembershipStatus(row.status),
        since=row.since,
        reason=row.reason,
        quality_score=row.quality_score,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )
