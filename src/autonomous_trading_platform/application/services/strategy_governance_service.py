from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.operator_settings import (
    OperatorSettingsRow,
)
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    DEFAULT_OPERATOR_SETTINGS_ID,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)

_STATE_ALIASES = {
    "research": "approved_research",
    "approved_research": "approved_research",
    "approved_for_research": "approved_research",
    "paper": "approved_for_paper_trading",
    "approved_paper": "approved_for_paper_trading",
    "approved_for_paper_trading": "approved_for_paper_trading",
    "live": "approved_for_live_trading",
    "approved_live": "approved_for_live_trading",
    "approved_for_live_trading": "approved_for_live_trading",
    "retired": "retired",
}

_RULE_STATE_ALIASES = {
    "approved_research": "approved_research",
    "approved_for_paper_trading": "approved_paper",
    "approved_for_live_trading": "approved_live",
    "retired": "retired",
}

_ALLOWED_TRANSITIONS = {
    "approved_research": {"approved_for_paper_trading"},
    "approved_for_paper_trading": {"approved_for_live_trading"},
    "approved_for_live_trading": {"retired"},
}

_TARGET_STATE_ROLES = {
    "approved_research": {"researcher", "admin"},
    "approved_for_paper_trading": {"risk_manager", "admin"},
    "approved_for_live_trading": {"admin"},
    "retired": {"operator", "risk_manager", "admin"},
}

_PROMOTION_TARGET_STATES = {"approved_for_paper_trading", "approved_for_live_trading"}


@dataclass(frozen=True)
class StrategyGovernanceTransitionResult:
    strategy_id: str
    from_state: str
    to_state: str
    reason: str
    updated_by: str
    updated_at: datetime


class StrategyGovernanceService:
    def __init__(
        self,
        *,
        session: Session,
        promotion_rules_repo: PromotionRulesRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
    ) -> None:
        self._session = session
        self._promotion_rules_repo = promotion_rules_repo or PromotionRulesRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)

    def transition(
        self,
        *,
        strategy_id: str,
        to_state: str,
        reason: str,
        updated_by: str,
        actor_role: str,
    ) -> StrategyGovernanceTransitionResult:
        governance = self._latest_governance(strategy_id)
        if governance is None:
            raise LookupError(f"Strategy not found: {strategy_id}")

        from_state = self._normalize_state(governance.current_state)
        target_state = self._normalize_state(to_state)

        self._assert_role_allowed(target_state=target_state, actor_role=actor_role)
        self._assert_transition_allowed(from_state=from_state, target_state=target_state)
        self._assert_promotion_criteria_met(
            strategy_id=strategy_id,
            source_run_id=str(governance.source_run_id) if governance.source_run_id else None,
            from_state=from_state,
            target_state=target_state,
        )

        now = datetime.now(UTC)
        previous_state = governance.current_state
        governance.current_state = target_state
        governance.updated_at = now

        self._audit_log_repo.record_operator_action(
            action="STRATEGY_GOVERNANCE_TRANSITIONED",
            actor=updated_by,
            reason=reason,
            occurred_at=now,
            component="strategies",
            metadata={
                "strategy_id": strategy_id,
                "from_state": previous_state,
                "to_state": target_state,
            },
        )
        if self._should_notify_strategy_promotion(target_state):
            self._audit_log_repo.record_operator_action(
                action="STRATEGY_PROMOTION_EVENT",
                actor=updated_by,
                reason=reason,
                occurred_at=now,
                component="notifications",
                metadata={
                    "channel": "notify_strategy_promotion_events",
                    "strategy_id": strategy_id,
                    "from_state": previous_state,
                    "to_state": target_state,
                },
            )
        self._session.flush()
        self._session.commit()

        return StrategyGovernanceTransitionResult(
            strategy_id=strategy_id,
            from_state=previous_state,
            to_state=target_state,
            reason=reason,
            updated_by=updated_by,
            updated_at=now,
        )

    def _latest_governance(self, strategy_id: str) -> StrategyGovernance | None:
        stmt = (
            select(StrategyGovernance)
            .where(StrategyGovernance.strategy_id == strategy_id)
            .order_by(StrategyGovernance.updated_at.desc())
            .limit(1)
        )
        return cast(StrategyGovernance | None, self._session.scalars(stmt).one_or_none())

    def _normalize_state(self, state: str) -> str:
        normalized = _STATE_ALIASES.get(state.strip().lower())
        if normalized is None:
            raise ValueError(f"Unsupported governance state: {state}")
        return normalized

    def _should_notify_strategy_promotion(self, target_state: str) -> bool:
        if target_state not in _PROMOTION_TARGET_STATES:
            return False

        settings = self._session.get(OperatorSettingsRow, DEFAULT_OPERATOR_SETTINGS_ID)
        if settings is None:
            return True

        return bool(settings.notify_strategy_promotion_events)

    def _assert_role_allowed(self, *, target_state: str, actor_role: str) -> None:
        allowed_roles = _TARGET_STATE_ROLES[target_state]
        if actor_role not in allowed_roles:
            allowed = ", ".join(sorted(allowed_roles))
            raise PermissionError(
                f"Role '{actor_role}' cannot transition strategies to {target_state}. "
                f"Required role: {allowed}."
            )

    def _assert_transition_allowed(self, *, from_state: str, target_state: str) -> None:
        if target_state not in _ALLOWED_TRANSITIONS.get(from_state, set()):
            raise ValueError(f"Invalid governance transition: {from_state} -> {target_state}")

    def _assert_promotion_criteria_met(
        self,
        *,
        strategy_id: str,
        source_run_id: str | None,
        from_state: str,
        target_state: str,
    ) -> None:
        rule = self._promotion_rules_repo.get_rules_for_transition(
            from_status=_RULE_STATE_ALIASES[from_state],
            to_status=_RULE_STATE_ALIASES[target_state],
        )
        if rule is None:
            return

        metrics = self._metrics_for_strategy(strategy_id=strategy_id, source_run_id=source_run_id)
        failures: list[str] = []

        if rule.min_sharpe is not None:
            actual = metrics.get("sharpe")
            if actual is None or actual < rule.min_sharpe:
                failures.append(f"sharpe {actual} < required {rule.min_sharpe}")

        if rule.max_drawdown is not None:
            actual = metrics.get("max_drawdown")
            if actual is None or self._max_drawdown_exceeds(actual, rule.max_drawdown):
                failures.append(f"max_drawdown {actual} exceeds limit {rule.max_drawdown}")

        if rule.min_days_tested is not None:
            actual = metrics.get("days_tested")
            if actual is None or actual < rule.min_days_tested:
                failures.append(f"days_tested {actual} < required {rule.min_days_tested}")

        if rule.min_trade_count is not None:
            actual = metrics.get("trade_count")
            if actual is None or actual < rule.min_trade_count:
                failures.append(f"trade_count {actual} < required {rule.min_trade_count}")

        if rule.min_cagr is not None:
            actual = metrics.get("cagr")
            if actual is None or actual < rule.min_cagr:
                failures.append(f"cagr {actual} < required {rule.min_cagr}")

        if rule.min_win_rate is not None:
            actual = metrics.get("win_rate")
            if actual is None or actual < rule.min_win_rate:
                failures.append(f"win_rate {actual} < required {rule.min_win_rate}")

        if failures:
            raise ValueError("Strategy does not meet promotion criteria: " + "; ".join(failures))

    def _metrics_for_strategy(
        self,
        *,
        strategy_id: str,
        source_run_id: str | None,
    ) -> dict[str, float]:
        metrics_row: MetricsSummary | None = None
        run_row: SimulationRuns | None = None

        if source_run_id is not None:
            run_row = self._session.get(SimulationRuns, source_run_id)
            metrics_row = self._session.scalars(
                select(MetricsSummary).where(MetricsSummary.run_id == source_run_id)
            ).one_or_none()

        if metrics_row is None:
            stmt = (
                select(MetricsSummary, SimulationRuns)
                .join(SimulationRuns, MetricsSummary.run_id == SimulationRuns.run_id)
                .where(SimulationRuns.strategy_id == strategy_id)
                .order_by(MetricsSummary.created_at.desc())
                .limit(1)
            )
            row = self._session.execute(stmt).one_or_none()
            if row is not None:
                metrics_row, run_row = row

        if metrics_row is None:
            metrics_row = self._latest_metrics_from_json(strategy_id)

        if metrics_row is None:
            return {}

        metrics_json = metrics_row.metrics_json or {}
        metrics: dict[str, float] = {}
        self._set_metric(metrics, "sharpe", metrics_row.sharpe_ratio)
        self._set_metric(metrics, "max_drawdown", metrics_row.max_drawdown)
        self._set_metric(metrics, "trade_count", metrics_row.trade_count)
        self._set_metric(metrics, "cagr", metrics_json.get("cagr"))
        self._set_metric(metrics, "win_rate", metrics_json.get("win_rate"))
        self._set_metric(metrics, "days_tested", metrics_json.get("days_tested"))

        if "win_rate" not in metrics and metrics_row.trade_count:
            winning_trades = metrics_row.winning_trade_count
            if winning_trades is not None:
                metrics["win_rate"] = winning_trades / metrics_row.trade_count

        if "days_tested" not in metrics and run_row is not None:
            metrics["days_tested"] = float((run_row.end_date - run_row.start_date).days + 1)

        return metrics

    def _latest_metrics_from_json(self, strategy_id: str) -> MetricsSummary | None:
        rows = self._session.scalars(
            select(MetricsSummary).order_by(
                MetricsSummary.created_at.desc(),
                MetricsSummary.metrics_snapshot_id.asc(),
            )
        ).all()
        for row in rows:
            if (row.metrics_json or {}).get("strategy_id") == strategy_id:
                return cast(MetricsSummary, row)
        return None

    def _set_metric(
        self,
        metrics: dict[str, float],
        key: str,
        value: int | float | str | None,
    ) -> None:
        if value is None:
            return

        metrics[key] = float(value)

    def _max_drawdown_exceeds(self, actual: float, limit: float) -> bool:
        if limit < 0:
            return actual < limit
        return abs(actual) > limit
