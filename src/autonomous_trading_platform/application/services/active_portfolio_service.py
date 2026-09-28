"""
Active portfolio set — which eligible strategies hold capital, and how much.

Governance decides eligibility (approved for paper/live). This service decides
membership: a dynamic set of ACTIVE strategies bounded by the operator's
min/max, plus WINDING_DOWN strategies that left the set but still hold
positions and must trade out of them.

Selection here is a deliberately simple placeholder until the portfolio review
(rotation step 4): eligible incumbents keep their seat, open seats go to the
highest blended-quality eligible strategies. It never swaps a healthy incumbent
out for a better challenger — that decision belongs to the review.
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
        latest_state: dict[str, str] = {}
        for row in self._session.scalars(
            select(StrategyGovernance).order_by(
                StrategyGovernance.updated_at.desc(), StrategyGovernance.strategy_id
            )
        ):
            latest_state.setdefault(row.strategy_id, row.current_state)

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
        """Recompute the active set and record every membership change."""
        now = now or datetime.now(UTC)
        self._as_of = now
        min_active, max_active = self._limits()
        members = {row.strategy_id: row for row in self._memberships.get_all()}
        eligible = self.eligible_strategy_ids()
        scores = {strategy_id: self._quality_score_fn(strategy_id) for strategy_id in eligible}

        def rank(ids: list[str]) -> list[str]:
            return sorted(ids, key=lambda sid: (-scores[sid], sid))

        incumbents = [sid for sid, row in members.items() if row.status == MembershipStatus.ACTIVE]
        keep = rank([sid for sid in incumbents if sid in scores])
        keep, over_max = keep[:max_active], keep[max_active:]
        challengers = rank([sid for sid in eligible if sid not in keep])
        added = challengers[: max_active - len(keep)]
        new_active = keep + added

        transitions: list[MembershipTransition] = []
        removed: list[str] = []
        wind_down_completed: list[str] = []

        for sid in added:
            prior = members.get(sid)
            reason = (
                "reactivated"
                if prior is not None and prior.status == MembershipStatus.WINDING_DOWN
                else "selected"
            )
            transitions.append(
                self._set_status(sid, MembershipStatus.ACTIVE, reason, actor, now, scores[sid])
            )

        for sid in keep:
            row = members[sid]
            row.quality_score = float(scores[sid])
            row.updated_at = now

        for sid in incumbents:
            if sid in new_active:
                continue
            reason = "over_max_active" if sid in over_max else "no_longer_eligible"
            target = (
                MembershipStatus.WINDING_DOWN
                if self._sleeves.get_positions(sid)
                else MembershipStatus.INACTIVE
            )
            transitions.append(self._set_status(sid, target, reason, actor, now, scores.get(sid)))
            removed.append(sid)

        for sid, row in members.items():
            if row.status != MembershipStatus.WINDING_DOWN or sid in new_active:
                continue
            if not self._sleeves.get_positions(sid):
                transitions.append(
                    self._set_status(
                        sid, MembershipStatus.INACTIVE, "wind_down_complete", actor, now, None
                    )
                )
                wind_down_completed.append(sid)

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
            },
        )
        return result

    def trading_members(self) -> list[PortfolioMember]:
        """Members the trading cycle must run: ACTIVE and WINDING_DOWN."""
        return [
            _member_contract(row)
            for row in self._memberships.get_by_statuses([s.value for s in TRADING_STATUSES])
        ]

    # ------------------------------------------------------------------
    # Budgets
    # ------------------------------------------------------------------

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

    def _set_status(
        self,
        strategy_id: str,
        status: MembershipStatus,
        reason: str,
        actor: str,
        now: datetime,
        quality_score: Decimal | None,
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
