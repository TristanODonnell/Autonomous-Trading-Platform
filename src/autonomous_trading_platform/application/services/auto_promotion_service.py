from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.operator_settings import OperatorSettingsRow
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)

AUTO_PROMOTION_ACTOR = "auto_promotion"

_CANDIDATE_STATES = {
    "approved_research",
    "approved_for_paper_trading",
    "approved_paper",
}

_RULE_FROM_STATUS = {
    "approved_research": "approved_research",
    "approved_for_paper_trading": "approved_paper",
    "approved_paper": "approved_paper",
}

_TARGET_STATE = {
    "approved_research": "paper",
    "approved_for_paper_trading": "live",
    "approved_paper": "live",
}

_TARGET_ROLE = {
    "paper": "risk_manager",
    "live": "admin",
}

_SERVICE_TO_STATE = {
    "paper": "approved_for_paper_trading",
    "live": "approved_for_live_trading",
}


@dataclass(frozen=True)
class PromotionCriterionResult:
    name: str
    passed: bool
    required: float | int | None
    actual: float | int | None
    reason: str


@dataclass(frozen=True)
class PromotionEligibilityResult:
    strategy_id: str
    from_state: str
    to_state: str | None
    rule_id: str | None
    eligible: bool
    status: str
    metrics: dict[str, float | int | None]
    criteria: list[PromotionCriterionResult]
    reasons: list[str]


@dataclass(frozen=True)
class AutoPromotionRunResult:
    run_id: str | None
    auto_promote_enabled: bool
    promotion_rules_used: list[dict[str, Any]]
    candidates: list[PromotionEligibilityResult]
    promotions_executed: list[dict[str, Any]]
    skipped_reason: str | None
    audit_event_type: str


class AutoPromotionService:
    def __init__(
        self,
        *,
        session: Session,
        promotion_rules_repo: PromotionRulesRepository | None = None,
        operator_settings_repo: OperatorSettingsRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
        governance_service: StrategyGovernanceService | None = None,
    ) -> None:
        self._session = session
        self._promotion_rules_repo = promotion_rules_repo or PromotionRulesRepository(session)
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._governance_service = governance_service or StrategyGovernanceService(
            session=session,
            promotion_rules_repo=self._promotion_rules_repo,
            audit_log_repo=self._audit_log_repo,
        )

    def scan(self) -> list[PromotionEligibilityResult]:
        rules = self._active_rules_by_transition()
        candidates = self._candidate_rows()
        return [self._evaluate_candidate(row, rules=rules) for row in candidates]

    def run(
        self,
        *,
        run_id: str | None = None,
        actor: str = AUTO_PROMOTION_ACTOR,
        enforce_enabled: bool = True,
    ) -> AutoPromotionRunResult:
        now = datetime.now(UTC)
        settings = self._operator_settings_repo.get_or_create_default()
        candidates = self.scan()
        if enforce_enabled and not bool(settings.auto_promote_enabled):
            result = AutoPromotionRunResult(
                run_id=run_id,
                auto_promote_enabled=False,
                promotion_rules_used=self._active_rule_payloads(),
                candidates=candidates,
                promotions_executed=[],
                skipped_reason="auto_promote_disabled",
                audit_event_type="STRATEGY_AUTO_PROMOTION_SKIPPED",
            )
            self._audit(result=result, actor=actor, occurred_at=now)
            self._session.flush()
            self._session.commit()
            return result

        promotions: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate.eligible or candidate.to_state is None:
                continue
            try:
                transition = self._governance_service.transition(
                    strategy_id=candidate.strategy_id,
                    to_state=candidate.to_state,
                    reason=f"automatic promotion via rule {candidate.rule_id}",
                    updated_by=actor,
                    actor_role=_TARGET_ROLE[candidate.to_state],
                )
                promotions.append(
                    {
                        "strategy_id": candidate.strategy_id,
                        "from_state": transition.from_state,
                        "to_state": transition.to_state,
                        "rule_id": candidate.rule_id,
                        "status": "promoted",
                    }
                )
            except Exception as exc:
                self._session.rollback()
                promotions.append(
                    {
                        "strategy_id": candidate.strategy_id,
                        "from_state": candidate.from_state,
                        "to_state": _SERVICE_TO_STATE.get(candidate.to_state, candidate.to_state),
                        "rule_id": candidate.rule_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        result = AutoPromotionRunResult(
            run_id=run_id,
            auto_promote_enabled=bool(settings.auto_promote_enabled),
            promotion_rules_used=self._active_rule_payloads(),
            candidates=candidates,
            promotions_executed=promotions,
            skipped_reason=None,
            audit_event_type="STRATEGY_AUTO_PROMOTION_COMPLETED",
        )
        self._audit(result=result, actor=actor, occurred_at=now)
        self._session.flush()
        self._session.commit()
        return result

    def _evaluate_candidate(
        self,
        governance: StrategyGovernance,
        *,
        rules: dict[tuple[str, str], PromotionRules],
    ) -> PromotionEligibilityResult:
        from_status = _RULE_FROM_STATUS[governance.current_state]
        to_status = "approved_paper" if from_status == "approved_research" else "approved_live"
        to_state = _TARGET_STATE[governance.current_state]
        rule = rules.get((from_status, to_status))
        metrics = self._metrics_for_strategy(governance)

        if rule is None:
            return PromotionEligibilityResult(
                strategy_id=governance.strategy_id,
                from_state=governance.current_state,
                to_state=to_state,
                rule_id=None,
                eligible=False,
                status="missing_rule",
                metrics=metrics,
                criteria=[],
                reasons=[f"No active PromotionRules row for {from_status} -> {to_status}."],
            )

        criteria = self._evaluate_rule(rule=rule, metrics=metrics)
        failures = [criterion.reason for criterion in criteria if not criterion.passed]
        eligible = not failures
        return PromotionEligibilityResult(
            strategy_id=governance.strategy_id,
            from_state=governance.current_state,
            to_state=to_state,
            rule_id=rule.rule_id,
            eligible=eligible,
            status="eligible" if eligible else "ineligible",
            metrics=metrics,
            criteria=criteria,
            reasons=["eligible"] if eligible else failures,
        )

    def _evaluate_rule(
        self,
        *,
        rule: PromotionRules,
        metrics: dict[str, float | int | None],
    ) -> list[PromotionCriterionResult]:
        criteria: list[PromotionCriterionResult] = []
        self._append_min(criteria, "min_sharpe", rule.min_sharpe, metrics.get("sharpe"))
        self._append_max_drawdown(criteria, rule.max_drawdown, metrics.get("max_drawdown"))
        self._append_min(
            criteria,
            "min_days_tested",
            rule.min_days_tested,
            metrics.get("days_tested"),
        )
        self._append_min(
            criteria,
            "min_trade_count",
            rule.min_trade_count,
            metrics.get("trade_count"),
        )
        self._append_min(criteria, "min_cagr", rule.min_cagr, metrics.get("cagr"))
        self._append_min(criteria, "min_win_rate", rule.min_win_rate, metrics.get("win_rate"))
        return criteria

    def _append_min(
        self,
        criteria: list[PromotionCriterionResult],
        name: str,
        required: float | int | None,
        actual: float | int | None,
    ) -> None:
        if required is None:
            return
        passed = actual is not None and actual >= required
        reason = (
            f"{name} {actual} >= required {required}"
            if passed
            else f"{name} {actual} < required {required}"
        )
        criteria.append(PromotionCriterionResult(name, passed, required, actual, reason))

    def _append_max_drawdown(
        self,
        criteria: list[PromotionCriterionResult],
        required: float | None,
        actual: float | int | None,
    ) -> None:
        if required is None:
            return
        if actual is None:
            passed = False
        elif required < 0:
            passed = float(actual) >= required
        else:
            passed = abs(float(actual)) <= required
        reason = (
            f"max_drawdown {actual} within limit {required}"
            if passed
            else f"max_drawdown {actual} exceeds limit {required}"
        )
        criteria.append(PromotionCriterionResult("max_drawdown", passed, required, actual, reason))

    def _candidate_rows(self) -> list[StrategyGovernance]:
        rows = self._session.scalars(
            select(StrategyGovernance)
            .where(StrategyGovernance.current_state.in_(_CANDIDATE_STATES))
            .order_by(StrategyGovernance.strategy_id.asc(), StrategyGovernance.updated_at.desc())
        ).all()
        latest_by_strategy: dict[str, StrategyGovernance] = {}
        for row in rows:
            latest_by_strategy.setdefault(row.strategy_id, row)
        return list(latest_by_strategy.values())

    def _active_rules_by_transition(self) -> dict[tuple[str, str], PromotionRules]:
        return {
            (row.from_status, row.to_status): row
            for row in self._promotion_rules_repo.get_all_active()
        }

    def _metrics_for_strategy(
        self,
        governance: StrategyGovernance,
    ) -> dict[str, float | int | None]:
        metrics_row: MetricsSummary | None = None
        run_row: SimulationRuns | None = None

        if governance.source_run_id is not None:
            run_id = str(governance.source_run_id)
            run_row = self._session.get(SimulationRuns, run_id)
            metrics_row = self._session.scalars(
                select(MetricsSummary).where(MetricsSummary.run_id == run_id)
            ).one_or_none()

        if metrics_row is None:
            row = self._session.execute(
                select(MetricsSummary, SimulationRuns)
                .join(SimulationRuns, MetricsSummary.run_id == SimulationRuns.run_id)
                .where(SimulationRuns.strategy_id == governance.strategy_id)
                .order_by(
                    MetricsSummary.created_at.desc(), MetricsSummary.metrics_snapshot_id.asc()
                )
                .limit(1)
            ).one_or_none()
            if row is not None:
                metrics_row, run_row = row

        if metrics_row is None:
            metrics_row = self._latest_metrics_from_json(governance.strategy_id)

        if metrics_row is None:
            return {
                "sharpe": None,
                "max_drawdown": None,
                "days_tested": None,
                "trade_count": None,
                "cagr": None,
                "win_rate": None,
            }

        metrics_json = metrics_row.metrics_json or {}
        trade_count = metrics_row.trade_count
        win_rate = metrics_json.get("win_rate")
        if win_rate is None and trade_count and metrics_row.winning_trade_count is not None:
            win_rate = metrics_row.winning_trade_count / trade_count

        days_tested = metrics_json.get("days_tested")
        if days_tested is None and run_row is not None:
            days_tested = (run_row.end_date - run_row.start_date).days + 1

        return {
            "sharpe": metrics_row.sharpe_ratio,
            "max_drawdown": metrics_row.max_drawdown,
            "days_tested": days_tested,
            "trade_count": trade_count,
            "cagr": metrics_json.get("cagr", metrics_row.total_return),
            "win_rate": win_rate,
        }

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

    def _active_rule_payloads(self) -> list[dict[str, Any]]:
        return [
            {
                "rule_id": row.rule_id,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "min_sharpe": row.min_sharpe,
                "max_drawdown": row.max_drawdown,
                "min_days_tested": row.min_days_tested,
                "min_trade_count": row.min_trade_count,
                "min_cagr": row.min_cagr,
                "min_win_rate": row.min_win_rate,
                "is_active": row.is_active,
            }
            for row in self._promotion_rules_repo.get_all_active()
        ]

    def _audit(
        self,
        *,
        result: AutoPromotionRunResult,
        actor: str,
        occurred_at: datetime,
    ) -> None:
        self._audit_log_repo.record_operator_action(
            action=result.audit_event_type,
            actor=actor,
            reason=result.skipped_reason or "automatic promotion scan completed",
            occurred_at=occurred_at,
            component="governance",
            metadata=self.result_to_jsonable(result),
        )

    @staticmethod
    def result_to_jsonable(result: AutoPromotionRunResult) -> dict[str, Any]:
        return {
            "run_id": result.run_id,
            "auto_promote_enabled": result.auto_promote_enabled,
            "promotion_rules_used": result.promotion_rules_used,
            "candidate_strategies": [
                {
                    "strategy_id": candidate.strategy_id,
                    "from_state": candidate.from_state,
                    "to_state": candidate.to_state,
                    "rule_id": candidate.rule_id,
                    "eligible": candidate.eligible,
                    "status": candidate.status,
                    "metrics": candidate.metrics,
                    "criteria": [
                        {
                            "name": criterion.name,
                            "passed": criterion.passed,
                            "required": criterion.required,
                            "actual": criterion.actual,
                            "reason": criterion.reason,
                        }
                        for criterion in candidate.criteria
                    ],
                    "reasons": candidate.reasons,
                }
                for candidate in result.candidates
            ],
            "promotions_executed": result.promotions_executed,
            "skipped_reason": result.skipped_reason,
            "audit_event_emitted": result.audit_event_type,
            "threshold_source": "promotion_rules",
            "deprecated_operator_settings_ignored": [
                "min_sharpe_for_promotion",
                "min_paper_trading_period_days",
            ],
        }

    @staticmethod
    def settings_payload(settings: OperatorSettingsRow) -> dict[str, Any]:
        return {
            "auto_promote_enabled": bool(settings.auto_promote_enabled),
            "notify_strategy_promotion_events": bool(settings.notify_strategy_promotion_events),
            "deprecated_fields_ignored": {
                "min_sharpe_for_promotion": float(settings.min_sharpe_for_promotion),
                "min_paper_trading_period_days": int(settings.min_paper_trading_period_days),
            },
        }
