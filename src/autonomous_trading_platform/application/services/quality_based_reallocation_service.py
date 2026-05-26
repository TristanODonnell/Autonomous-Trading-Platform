from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.operator_settings import OperatorSettingsRow
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

ACTIVE_STATES = {"approved_for_paper_trading", "approved_for_live_trading"}
AUTO_REBALANCE_ACTOR = "auto_rebalance"
DEFAULT_TOTAL_ALLOCATION = Decimal("1")
PCT_QUANT = Decimal("0.000001")


@dataclass(frozen=True)
class StrategyQualityInput:
    strategy_id: str
    governance_state: str
    performance_tier: str | None
    enabled: bool
    policy_cap: Decimal
    effective_cap: Decimal
    floor: Decimal
    manual_override_pct: Decimal | None
    current_allocation_pct: Decimal
    quality_score: Decimal
    metrics: dict[str, float | int | None]


@dataclass(frozen=True)
class StrategyAllocationProposal:
    strategy_id: str
    before_pct: Decimal
    after_pct: Decimal
    quality_score: Decimal
    cap_pct: Decimal
    floor_pct: Decimal
    disabled: bool
    manual_override_respected: bool
    reason: str


@dataclass(frozen=True)
class QualityReallocationResult:
    run_id: str | None
    auto_rebalance_enabled: bool
    before_allocation: dict[str, Decimal]
    after_allocation: dict[str, Decimal]
    proposals: list[StrategyAllocationProposal]
    quality_metrics: dict[str, dict[str, float | int | None]]
    active_policies: list[dict[str, str | float | bool | None]]
    allocation_overrides: list[dict[str, str | float | bool | None]]
    skipped_reason: str | None
    audit_event_type: str

    @property
    def changed(self) -> bool:
        return self.before_allocation != self.after_allocation


class QualityBasedReallocationService:
    def __init__(
        self,
        *,
        session: Session,
        audit_log_repo: AuditLogRepository | None = None,
        operator_settings_repo: OperatorSettingsRepository | None = None,
        allocation_overrides_repo: AllocationOverridesRepository | None = None,
    ) -> None:
        self._session = session
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._allocation_overrides_repo = (
            allocation_overrides_repo or AllocationOverridesRepository(session)
        )

    def rebalance(
        self,
        *,
        run_id: str | None = None,
        actor: str = AUTO_REBALANCE_ACTOR,
        enforce_enabled: bool = True,
    ) -> QualityReallocationResult:
        now = datetime.now(UTC)
        settings = self._operator_settings_repo.get_or_create_default()
        if enforce_enabled and not bool(settings.auto_rebalance_enabled):
            result = self._build_skipped_result(
                run_id=run_id,
                settings=settings,
                skipped_reason="auto_rebalance_disabled",
            )
            self._audit(result=result, actor=actor, occurred_at=now)
            self._session.flush()
            self._session.commit()
            return result

        inputs = self._load_strategy_inputs(settings=settings, now=now)
        before = {item.strategy_id: item.current_allocation_pct for item in inputs}
        proposals = self._compute_proposals(inputs)
        after = {proposal.strategy_id: proposal.after_pct for proposal in proposals}

        self._write_auto_overrides(proposals=proposals, actor=actor, now=now)

        result = QualityReallocationResult(
            run_id=run_id,
            auto_rebalance_enabled=bool(settings.auto_rebalance_enabled),
            before_allocation=before,
            after_allocation=after,
            proposals=proposals,
            quality_metrics={item.strategy_id: item.metrics for item in inputs},
            active_policies=self._active_policy_payloads(),
            allocation_overrides=self._active_override_payloads(now=now),
            skipped_reason=None,
            audit_event_type="STRATEGY_ALLOCATION_REBALANCED",
        )
        self._audit(result=result, actor=actor, occurred_at=now)
        if bool(settings.notify_allocation_rebalance_events):
            self._emit_rebalance_notification(result=result, actor=actor, occurred_at=now)
        self._session.flush()
        self._session.commit()
        return result

    def _build_skipped_result(
        self,
        *,
        run_id: str | None,
        settings: OperatorSettingsRow,
        skipped_reason: str,
    ) -> QualityReallocationResult:
        now = datetime.now(UTC)
        inputs = self._load_strategy_inputs(settings=settings, now=now)
        before = {item.strategy_id: item.current_allocation_pct for item in inputs}
        return QualityReallocationResult(
            run_id=run_id,
            auto_rebalance_enabled=bool(settings.auto_rebalance_enabled),
            before_allocation=before,
            after_allocation=before,
            proposals=[],
            quality_metrics={item.strategy_id: item.metrics for item in inputs},
            active_policies=self._active_policy_payloads(),
            allocation_overrides=self._active_override_payloads(now=now),
            skipped_reason=skipped_reason,
            audit_event_type="STRATEGY_ALLOCATION_REBALANCE_SKIPPED",
        )

    def _load_strategy_inputs(
        self,
        *,
        settings: OperatorSettingsRow,
        now: datetime,
    ) -> list[StrategyQualityInput]:
        rows = list(
            self._session.scalars(
                select(StrategyGovernance)
                .where(StrategyGovernance.current_state.in_(ACTIVE_STATES))
                .order_by(StrategyGovernance.strategy_id.asc())
            ).all()
        )
        policies = self._policies_by_status_and_tier()
        controls = self._controls_by_strategy()
        overrides = self._active_overrides_by_strategy(now=now)

        inputs: list[StrategyQualityInput] = []
        for governance in rows:
            config = self._session.get(StrategyConfigs, governance.strategy_id)
            approval_status = self._policy_status(governance.current_state)
            performance_tier = self._performance_tier(config)
            policy = self._resolve_policy(
                policies=policies,
                approval_status=approval_status,
                performance_tier=performance_tier,
            )
            if policy is None:
                continue

            control = controls.get(governance.strategy_id)
            override = overrides.get(governance.strategy_id)
            manual_override_pct = self._manual_override_pct(override)
            current_pct = (
                manual_override_pct
                if manual_override_pct is not None
                else self._decimal_pct(policy.max_pct_of_capital)
            )
            metrics = self._latest_metrics(governance.strategy_id)
            quality_score = self._quality_score(metrics)
            policy_cap = self._decimal_pct(policy.max_pct_of_capital)
            inputs.append(
                StrategyQualityInput(
                    strategy_id=governance.strategy_id,
                    governance_state=governance.current_state,
                    performance_tier=performance_tier,
                    enabled=bool(control.enabled) if control is not None else True,
                    policy_cap=policy_cap,
                    effective_cap=policy_cap,
                    floor=Decimal("0"),
                    manual_override_pct=manual_override_pct,
                    current_allocation_pct=current_pct,
                    quality_score=quality_score,
                    metrics=metrics,
                )
            )

        return inputs

    def _compute_proposals(
        self,
        inputs: list[StrategyQualityInput],
    ) -> list[StrategyAllocationProposal]:
        fixed: dict[str, Decimal] = {}
        variable: list[StrategyQualityInput] = []

        for item in inputs:
            if not item.enabled:
                fixed[item.strategy_id] = Decimal("0")
            elif item.manual_override_pct is not None:
                fixed[item.strategy_id] = min(item.manual_override_pct, item.effective_cap)
            else:
                variable.append(item)

        remaining = max(DEFAULT_TOTAL_ALLOCATION - sum(fixed.values(), Decimal("0")), Decimal("0"))
        weighted = self._allocate_weighted(variable, remaining=remaining)

        proposals: list[StrategyAllocationProposal] = []
        for item in inputs:
            if not item.enabled:
                after = Decimal("0")
                reason = "disabled_strategy"
                manual = False
            elif item.manual_override_pct is not None:
                after = fixed[item.strategy_id]
                reason = "manual_override_respected"
                manual = True
            else:
                after = weighted.get(item.strategy_id, Decimal("0"))
                reason = "quality_weighted"
                manual = False

            proposals.append(
                StrategyAllocationProposal(
                    strategy_id=item.strategy_id,
                    before_pct=self._quantize(item.current_allocation_pct),
                    after_pct=self._quantize(after),
                    quality_score=self._quantize(item.quality_score),
                    cap_pct=self._quantize(item.effective_cap),
                    floor_pct=self._quantize(item.floor),
                    disabled=not item.enabled,
                    manual_override_respected=manual,
                    reason=reason,
                )
            )

        return sorted(proposals, key=lambda row: row.strategy_id)

    def _allocate_weighted(
        self,
        items: list[StrategyQualityInput],
        *,
        remaining: Decimal,
    ) -> dict[str, Decimal]:
        allocations = {item.strategy_id: Decimal("0") for item in items}
        candidates = list(items)
        budget = remaining

        while candidates and budget > Decimal("0"):
            total_weight = sum(
                (max(item.quality_score, Decimal("0")) for item in candidates), Decimal("0")
            )
            if total_weight <= Decimal("0"):
                equal = budget / Decimal(len(candidates))
                weights = {item.strategy_id: equal for item in candidates}
            else:
                weights = {
                    item.strategy_id: budget * max(item.quality_score, Decimal("0")) / total_weight
                    for item in candidates
                }

            capped: list[StrategyQualityInput] = []
            next_candidates: list[StrategyQualityInput] = []
            for item in candidates:
                proposed = allocations[item.strategy_id] + weights[item.strategy_id]
                if proposed >= item.effective_cap:
                    allocations[item.strategy_id] = item.effective_cap
                    capped.append(item)
                else:
                    allocations[item.strategy_id] = proposed
                    next_candidates.append(item)

            if not capped:
                break

            used = sum(allocations.values(), Decimal("0"))
            budget = max(remaining - used, Decimal("0"))
            candidates = next_candidates

        return {key: self._quantize(value) for key, value in allocations.items()}

    def _write_auto_overrides(
        self,
        *,
        proposals: list[StrategyAllocationProposal],
        actor: str,
        now: datetime,
    ) -> None:
        active = self._active_overrides_by_strategy(now=now)
        for proposal in proposals:
            existing = active.get(proposal.strategy_id)
            if existing is not None and existing.overridden_by != AUTO_REBALANCE_ACTOR:
                continue
            if existing is not None:
                existing.is_active = False
        self._session.flush()

        for proposal in proposals:
            existing = active.get(proposal.strategy_id)
            if existing is not None and existing.overridden_by != AUTO_REBALANCE_ACTOR:
                continue
            if proposal.manual_override_respected:
                continue
            self._allocation_overrides_repo.create_override(
                AllocationOverrides(
                    override_id=str(uuid4()),
                    strategy_id=proposal.strategy_id,
                    overridden_by=actor,
                    override_reason=f"quality-based reallocation: {proposal.reason}",
                    max_pct_of_capital=float(proposal.after_pct),
                    max_position_size_usd=None,
                    max_drawdown_allowed=None,
                    is_active=True,
                    created_at=now,
                    expires_at=None,
                )
            )

    def _audit(
        self,
        *,
        result: QualityReallocationResult,
        actor: str,
        occurred_at: datetime,
    ) -> None:
        reason = result.skipped_reason or "quality-based allocation rebalance"
        self._audit_log_repo.record_operator_action(
            action=result.audit_event_type,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
            component="allocations",
            metadata=self.result_to_jsonable(result),
        )

    def _emit_rebalance_notification(
        self,
        *,
        result: QualityReallocationResult,
        actor: str,
        occurred_at: datetime,
    ) -> None:
        self._audit_log_repo.record_operator_action(
            action="STRATEGY_ALLOCATION_REBALANCE_EVENT",
            actor=actor,
            reason="quality-based allocation rebalance completed",
            occurred_at=occurred_at,
            component="notifications",
            metadata={
                "channel": "notify_allocation_rebalance_events",
                "run_id": result.run_id,
                "before_allocation": {k: float(v) for k, v in result.before_allocation.items()},
                "after_allocation": {k: float(v) for k, v in result.after_allocation.items()},
            },
        )

    @staticmethod
    def result_to_jsonable(result: QualityReallocationResult) -> dict:
        return {
            "run_id": result.run_id,
            "auto_rebalance_enabled": result.auto_rebalance_enabled,
            "before_allocation": {
                key: float(value) for key, value in result.before_allocation.items()
            },
            "after_allocation": {
                key: float(value) for key, value in result.after_allocation.items()
            },
            "proposals": [
                {
                    "strategy_id": row.strategy_id,
                    "before_pct": float(row.before_pct),
                    "after_pct": float(row.after_pct),
                    "quality_score": float(row.quality_score),
                    "cap_pct": float(row.cap_pct),
                    "floor_pct": float(row.floor_pct),
                    "disabled": row.disabled,
                    "manual_override_respected": row.manual_override_respected,
                    "reason": row.reason,
                }
                for row in result.proposals
            ],
            "quality_metrics": result.quality_metrics,
            "active_capital_policies": result.active_policies,
            "allocation_overrides": result.allocation_overrides,
            "skipped_reason": result.skipped_reason,
            "audit_event_emitted": result.audit_event_type,
        }

    def _latest_metrics(self, strategy_id: str) -> dict[str, float | int | None]:
        row = self._session.execute(
            select(MetricsSummary, SimulationRuns)
            .join(SimulationRuns, MetricsSummary.run_id == SimulationRuns.run_id)
            .where(SimulationRuns.strategy_id == strategy_id)
            .order_by(MetricsSummary.created_at.desc(), MetricsSummary.metrics_snapshot_id.asc())
            .limit(1)
        ).one_or_none()
        if row is None:
            metrics = self._latest_metrics_from_json(strategy_id)
            if metrics is None:
                return {
                    "sharpe_ratio": None,
                    "total_return": None,
                    "max_drawdown": None,
                    "trade_count": None,
                    "win_rate": None,
                }
            return metrics

        metrics, _run = row
        win_rate = None
        if metrics.trade_count and metrics.winning_trade_count is not None:
            win_rate = metrics.winning_trade_count / metrics.trade_count
        metrics_json = metrics.metrics_json or {}
        return {
            "sharpe_ratio": metrics.sharpe_ratio,
            "total_return": metrics.total_return,
            "max_drawdown": metrics.max_drawdown,
            "trade_count": metrics.trade_count,
            "win_rate": metrics_json.get("win_rate", win_rate),
        }

    def _latest_metrics_from_json(
        self,
        strategy_id: str,
    ) -> dict[str, float | int | None] | None:
        rows = self._session.scalars(
            select(MetricsSummary).order_by(
                MetricsSummary.created_at.desc(),
                MetricsSummary.metrics_snapshot_id.asc(),
            )
        ).all()
        for metrics in rows:
            metrics_json = metrics.metrics_json or {}
            if metrics_json.get("strategy_id") != strategy_id:
                continue
            win_rate = None
            if metrics.trade_count and metrics.winning_trade_count is not None:
                win_rate = metrics.winning_trade_count / metrics.trade_count
            return {
                "sharpe_ratio": metrics.sharpe_ratio,
                "total_return": metrics.total_return,
                "max_drawdown": metrics.max_drawdown,
                "trade_count": metrics.trade_count,
                "win_rate": metrics_json.get("win_rate", win_rate),
            }
        return None

    def _quality_score(self, metrics: dict[str, float | int | None]) -> Decimal:
        sharpe = Decimal(str(metrics["sharpe_ratio"] if metrics["sharpe_ratio"] is not None else 0))
        total_return = Decimal(
            str(metrics["total_return"] if metrics["total_return"] is not None else 0)
        )
        drawdown = Decimal(
            str(metrics["max_drawdown"] if metrics["max_drawdown"] is not None else 0)
        )
        win_rate = Decimal(str(metrics["win_rate"] if metrics["win_rate"] is not None else 0))
        trade_count = Decimal(
            str(metrics["trade_count"] if metrics["trade_count"] is not None else 0)
        )

        score = Decimal("1")
        score += max(sharpe, Decimal("-1")) * Decimal("0.40")
        score += total_return * Decimal("1.50")
        score += win_rate * Decimal("0.40")
        score += min(trade_count / Decimal("100"), Decimal("0.25"))
        score -= abs(drawdown) * Decimal("2.00")
        return max(score, Decimal("0.01"))

    def _policies_by_status_and_tier(
        self,
    ) -> dict[tuple[str, str | None], CapitalAllocationPolicies]:
        return {
            (row.approval_status, row.performance_tier): row
            for row in self._session.scalars(
                select(CapitalAllocationPolicies).where(
                    CapitalAllocationPolicies.is_active.is_(True)
                )
            ).all()
        }

    def _controls_by_strategy(self) -> dict[str, StrategyControlState]:
        return {
            row.strategy_id: row
            for row in self._session.scalars(select(StrategyControlState)).all()
        }

    def _active_overrides_by_strategy(self, *, now: datetime) -> dict[str, AllocationOverrides]:
        rows = self._session.scalars(
            select(AllocationOverrides)
            .where(AllocationOverrides.is_active.is_(True))
            .order_by(AllocationOverrides.created_at.desc(), AllocationOverrides.override_id.asc())
        ).all()
        result: dict[str, AllocationOverrides] = {}
        for row in rows:
            if row.expires_at is not None and row.expires_at <= now:
                continue
            result.setdefault(row.strategy_id, row)
        return result

    def _resolve_policy(
        self,
        *,
        policies: dict[tuple[str, str | None], CapitalAllocationPolicies],
        approval_status: str,
        performance_tier: str | None,
    ) -> CapitalAllocationPolicies | None:
        if performance_tier is not None:
            policy = policies.get((approval_status, performance_tier))
            if policy is not None:
                return policy
        return policies.get((approval_status, None))

    def _active_policy_payloads(self) -> list[dict[str, str | float | bool | None]]:
        rows = self._session.scalars(
            select(CapitalAllocationPolicies)
            .where(CapitalAllocationPolicies.is_active.is_(True))
            .order_by(
                CapitalAllocationPolicies.approval_status.asc(),
                CapitalAllocationPolicies.performance_tier.asc(),
            )
        ).all()
        return [
            {
                "policy_id": row.policy_id,
                "approval_status": row.approval_status,
                "performance_tier": row.performance_tier,
                "max_pct_of_capital": row.max_pct_of_capital,
                "max_position_size_usd": row.max_position_size_usd,
                "max_drawdown_allowed": row.max_drawdown_allowed,
                "is_active": row.is_active,
            }
            for row in rows
        ]

    def _active_override_payloads(
        self,
        *,
        now: datetime,
    ) -> list[dict[str, str | float | bool | None]]:
        return [
            {
                "override_id": row.override_id,
                "strategy_id": row.strategy_id,
                "overridden_by": row.overridden_by,
                "override_reason": row.override_reason,
                "max_pct_of_capital": row.max_pct_of_capital,
                "max_position_size_usd": row.max_position_size_usd,
                "max_drawdown_allowed": row.max_drawdown_allowed,
                "is_active": row.is_active,
            }
            for row in self._active_overrides_by_strategy(now=now).values()
        ]

    def _manual_override_pct(self, override: AllocationOverrides | None) -> Decimal | None:
        if override is None or override.overridden_by == AUTO_REBALANCE_ACTOR:
            return None
        if override.max_pct_of_capital is None:
            return None
        return self._decimal_pct(override.max_pct_of_capital)

    def _performance_tier(self, config: StrategyConfigs | None) -> str | None:
        if config is None or config.metadata_json is None:
            return None
        value = config.metadata_json.get("performance_tier")
        return str(value) if value is not None else None

    def _policy_status(self, governance_state: str) -> str:
        if governance_state == "approved_for_live_trading":
            return "approved_live"
        if governance_state == "approved_for_paper_trading":
            return "approved_paper"
        return "approved_research"

    def _decimal_pct(self, value: float | Decimal) -> Decimal:
        return self._quantize(Decimal(str(value)))

    def _quantize(self, value: Decimal) -> Decimal:
        return value.quantize(PCT_QUANT, rounding=ROUND_HALF_UP)
