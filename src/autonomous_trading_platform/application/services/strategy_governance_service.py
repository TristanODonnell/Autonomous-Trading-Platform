from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.governance_audit_service import (
    GovernanceAuditService,
)
from autonomous_trading_platform.application.services.governance_exceptions import (
    MissingSourceRunError,
    PromotionCriteriaConfigurationError,
    PromotionRulesMissingError,
)
from autonomous_trading_platform.contracts.governance.governance_audit import (
    GovernanceCriteriaEvaluation,
    TriggerSource,
)
from autonomous_trading_platform.observability.logging import get_logger
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

logger = get_logger(__name__)

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
    "approved_for_paper_trading": {"approved_for_live_trading", "approved_research"},
    "approved_for_live_trading": {"approved_for_paper_trading", "retired"},
}

_TARGET_STATE_ROLES = {
    "approved_research": {"researcher", "system_risk", "admin"},
    "approved_for_paper_trading": {"risk_manager", "system_risk", "admin"},
    "approved_for_live_trading": {"admin"},
    "retired": {"operator", "risk_manager", "admin"},
}

_PROMOTION_TARGET_STATES = {"approved_for_paper_trading", "approved_for_live_trading"}

# Transitions that require an explicit source_run_id; no fallback to latest run is allowed.
# Encode the policy here rather than scattering conditionals across the service.
_SOURCE_RUN_REQUIRED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("approved_research", "approved_for_paper_trading"),
        ("approved_for_paper_trading", "approved_for_live_trading"),
    }
)

# Criteria that MUST be non-null on the PromotionRules row for a promotion to proceed.
# Keyed by (rule_from_status, rule_to_status) using _RULE_STATE_ALIASES values.
# An empty frozenset means no hard-required fields for that transition.
# Future extension: add per-tier overrides, operator-approval requirements, etc.
_REQUIRED_CRITERIA_BY_TRANSITION: dict[tuple[str, str], frozenset[str]] = {
    ("approved_paper", "approved_live"): frozenset(
        {"min_sharpe", "min_days_tested", "min_trade_count"}
    ),
    ("approved_research", "approved_paper"): frozenset(),
}


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
        source_run_id: str | None = None,
        record_governance_audit: bool = True,
    ) -> StrategyGovernanceTransitionResult:
        governance = self._latest_governance(strategy_id)
        if governance is None:
            raise LookupError(f"Strategy not found: {strategy_id}")

        from_state = self._normalize_state(governance.current_state)
        target_state = self._normalize_state(to_state)

        # Resolve source_run_id: explicit caller argument wins over governance record.
        resolved_source_run_id = source_run_id or (
            str(governance.source_run_id) if governance.source_run_id else None
        )

        self._assert_role_allowed(target_state=target_state, actor_role=actor_role)
        self._assert_transition_allowed(from_state=from_state, target_state=target_state)
        criteria_summary: dict[str, object] = {}
        if self._is_promotion_transition(from_state=from_state, target_state=target_state):
            criteria_summary = self._assert_promotion_criteria_met(
                strategy_id=strategy_id,
                source_run_id=resolved_source_run_id,
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
                **criteria_summary,
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
        if record_governance_audit:
            self._record_governance_decision(
                strategy_id=strategy_id,
                from_state=previous_state,
                to_state=target_state,
                reason=reason,
                updated_by=updated_by,
                criteria_summary=criteria_summary,
                source_run_id=resolved_source_run_id,
                now=now,
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

    def _record_governance_decision(
        self,
        *,
        strategy_id: str,
        from_state: str,
        to_state: str,
        reason: str,
        updated_by: str,
        criteria_summary: dict[str, object],
        source_run_id: str | None,
        now: datetime,
    ) -> None:
        audit = GovernanceAuditService(
            session=self._session,
            audit_log_repo=self._audit_log_repo,
        )
        criteria = [
            GovernanceCriteriaEvaluation(
                criterion=str(row.get("criterion")),
                threshold=cast(float | None, row.get("threshold", row.get("required"))),
                actual=cast(float | None, row.get("actual")),
                passed=bool(row.get("passed")),
                evaluated_at=now,
            )
            for row in cast(
                list[dict[str, object]],
                criteria_summary.get("promotion_criteria_evaluated", []),
            )
        ]
        if self._is_promotion_transition(from_state=from_state, target_state=to_state):
            audit.record_promotion_decision(
                strategy_id=strategy_id,
                from_state=from_state,
                to_state=to_state,
                actor=updated_by,
                trigger_source=TriggerSource.OPERATOR_MANUAL,
                eligible=True,
                criteria=criteria,
                metrics_snapshot={
                    "metrics_source_type": criteria_summary.get("metrics_source_type"),
                    "fallback_allowed": criteria_summary.get("fallback_allowed"),
                    "rule_from_status": criteria_summary.get("rule_from_status"),
                    "rule_to_status": criteria_summary.get("rule_to_status"),
                },
                source_run_id=source_run_id,
                rule_id=cast(str | None, criteria_summary.get("rule_id")),
                reasons=[reason],
                now=now,
            )
            return

        audit.record_demotion_decision(
            strategy_id=strategy_id,
            from_state=from_state,
            to_state=to_state,
            actor=updated_by,
            trigger_source=TriggerSource.OPERATOR_MANUAL,
            breach=True,
            breached_metric=None,
            breached_value=None,
            threshold=None,
            source_id=source_run_id,
            source_type="operator_manual",
            reasons=[reason],
            status="manual_transition",
            now=now,
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

    def _is_promotion_transition(self, *, from_state: str, target_state: str) -> bool:
        return (
            from_state == "approved_research" and target_state == "approved_for_paper_trading"
        ) or (
            from_state == "approved_for_paper_trading"
            and target_state == "approved_for_live_trading"
        )

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
    ) -> dict[str, object]:
        """Validate that strategy metrics meet all active promotion criteria.

        Returns a criteria audit summary dict to be embedded in the governance transition
        audit log so that successful promotions record which checks passed.

        Raises:
            PromotionRulesMissingError: no active rule row for this transition.
            PromotionCriteriaConfigurationError: rule exists but required fields are null.
            MissingSourceRunError: capital-bearing transition attempted without source_run_id.
            ValueError: strategy metrics fail one or more configured thresholds.
        """
        rule_from = _RULE_STATE_ALIASES[from_state]
        rule_to = _RULE_STATE_ALIASES[target_state]
        rule = self._promotion_rules_repo.get_rules_for_transition(
            from_status=rule_from,
            to_status=rule_to,
        )
        now = datetime.now(UTC)
        is_capital_bearing = (from_state, target_state) in _SOURCE_RUN_REQUIRED_TRANSITIONS

        if rule is None:
            self._audit_log_repo.record_operator_action(
                action="PROMOTION_RULES_CONFIGURATION_ERROR",
                actor="system",
                reason=f"No active PromotionRules row for {rule_from} -> {rule_to}",
                occurred_at=now,
                component="governance",
                metadata={
                    "strategy_id": strategy_id,
                    "from_state": from_state,
                    "to_state": target_state,
                    "error": "missing_rule",
                    "rule_from_status": rule_from,
                    "rule_to_status": rule_to,
                },
            )
            raise PromotionRulesMissingError(from_state=rule_from, to_state=rule_to)

        required_criteria = _REQUIRED_CRITERIA_BY_TRANSITION.get((rule_from, rule_to), frozenset())
        null_required = [f for f in sorted(required_criteria) if getattr(rule, f) is None]
        if null_required:
            self._audit_log_repo.record_operator_action(
                action="PROMOTION_RULES_CONFIGURATION_ERROR",
                actor="system",
                reason=(
                    f"Required criteria null on rule {rule.rule_id!r} "
                    f"for {rule_from} -> {rule_to}: {null_required}"
                ),
                occurred_at=now,
                component="governance",
                metadata={
                    "strategy_id": strategy_id,
                    "from_state": from_state,
                    "to_state": target_state,
                    "rule_id": rule.rule_id,
                    "error": "null_required_criteria",
                    "missing_required_criteria": null_required,
                },
            )
            raise PromotionCriteriaConfigurationError(
                from_state=rule_from,
                to_state=rule_to,
                missing_fields=null_required,
                rule_id=rule.rule_id,
            )

        if is_capital_bearing and source_run_id is None:
            self._audit_log_repo.record_operator_action(
                action="PROMOTION_MISSING_SOURCE_RUN",
                actor="system",
                reason=(
                    f"Capital-bearing promotion to {target_state!r} requires explicit source_run_id"
                ),
                occurred_at=now,
                component="governance",
                metadata={
                    "strategy_id": strategy_id,
                    "from_state": from_state,
                    "to_state": target_state,
                    "error": "missing_source_run_id",
                    "fallback_allowed": False,
                    "rule_id": rule.rule_id,
                },
            )
            raise MissingSourceRunError(strategy_id=strategy_id, target_state=target_state)

        metrics = self._metrics_for_strategy(
            strategy_id=strategy_id,
            source_run_id=source_run_id,
            is_capital_bearing=is_capital_bearing,
        )
        failures: list[str] = []
        evaluated: list[dict[str, object]] = []
        skipped_optional: list[str] = []

        _min_criteria = [
            ("min_sharpe", "sharpe"),
            ("min_days_tested", "days_tested"),
            ("min_trade_count", "trade_count"),
            ("min_cagr", "cagr"),
            ("min_win_rate", "win_rate"),
        ]
        for field_name, metric_key in _min_criteria:
            threshold = getattr(rule, field_name)
            if threshold is None:
                skipped_optional.append(field_name)
                continue
            actual = metrics.get(metric_key)
            passed = actual is not None and actual >= threshold
            evaluated.append(
                {"criterion": field_name, "required": threshold, "actual": actual, "passed": passed}
            )
            if not passed:
                failures.append(f"{metric_key} {actual} < required {threshold}")

        if rule.max_drawdown is not None:
            actual_dd = metrics.get("max_drawdown")
            passed_dd = actual_dd is not None and not self._max_drawdown_exceeds(
                actual_dd, rule.max_drawdown
            )
            evaluated.append(
                {
                    "criterion": "max_drawdown",
                    "required": rule.max_drawdown,
                    "actual": actual_dd,
                    "passed": passed_dd,
                }
            )
            if not passed_dd:
                failures.append(f"max_drawdown {actual_dd} exceeds limit {rule.max_drawdown}")
        else:
            skipped_optional.append("max_drawdown")

        if skipped_optional:
            logger.info(
                "governance.optional_criteria_skipped",
                extra={
                    "strategy_id": strategy_id,
                    "skipped": skipped_optional,
                    "rule_id": rule.rule_id,
                    "from": rule_from,
                    "to": rule_to,
                },
            )

        if failures:
            raise ValueError("Strategy does not meet promotion criteria: " + "; ".join(failures))

        metrics_source_type = "explicit_source_run" if source_run_id else "fallback"
        return {
            "source_run_id": source_run_id,
            "metrics_source_type": metrics_source_type,
            "fallback_allowed": not is_capital_bearing,
            "promotion_criteria_evaluated": evaluated,
            "promotion_criteria_skipped_optional": skipped_optional,
            "rule_id": rule.rule_id,
            "rule_from_status": rule_from,
            "rule_to_status": rule_to,
        }

    def _metrics_for_strategy(
        self,
        *,
        strategy_id: str,
        source_run_id: str | None,
        is_capital_bearing: bool = False,
    ) -> dict[str, float]:
        metrics_row: MetricsSummary | None = None
        run_row: SimulationRuns | None = None

        if source_run_id is not None:
            run_row = self._session.get(SimulationRuns, source_run_id)
            metrics_row = self._session.scalars(
                select(MetricsSummary).where(MetricsSummary.run_id == source_run_id)
            ).one_or_none()

        if not is_capital_bearing and metrics_row is None:
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

        if not is_capital_bearing and metrics_row is None:
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
