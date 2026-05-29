from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.governance_audit_service import (
    GovernanceAuditService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.contracts.governance.governance_audit import TriggerSource
from autonomous_trading_platform.execution.services.trading_freeze_service import (
    TradingFreezeService,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    auto_demotion_breach_total,
    auto_demotion_scan_duration_seconds,
    auto_demotion_scan_total,
    strategy_demotion_total,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.operator_settings import OperatorSettingsRow
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.governance_audit_repository import (
    GovernanceAuditRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)

AUTO_DEMOTION_ACTOR = "system_risk"
AUTO_DEMOTION_EVENT = "STRATEGY_DEMOTION_EVENT"
AUTO_DEMOTION_AUDIT_EVENT = "STRATEGY_AUTO_DEMOTED"
AUTO_DEMOTION_COMPLETED_EVENT = "STRATEGY_AUTO_DEMOTION_COMPLETED"
AUTO_DEMOTION_SKIPPED_EVENT = "STRATEGY_AUTO_DEMOTION_SKIPPED"

_DEMOTABLE_STATES = {
    "approved_for_live_trading",
    "approved_live",
    "approved_for_paper_trading",
    "approved_paper",
}

_NEXT_STATE = {
    "approved_for_live_trading": "approved_for_paper_trading",
    "approved_live": "approved_for_paper_trading",
    "approved_for_paper_trading": "approved_research",
    "approved_paper": "approved_research",
}

_MAINTENANCE_RULE_STATUS = {
    "approved_for_live_trading": ("approved_paper", "approved_live"),
    "approved_live": ("approved_paper", "approved_live"),
    "approved_for_paper_trading": ("approved_research", "approved_paper"),
    "approved_paper": ("approved_research", "approved_paper"),
}

logger = get_logger(__name__)


@dataclass(frozen=True)
class PerformanceObservation:
    drawdown: float | None
    sharpe: float | None
    win_rate: float | None
    trade_count: int | None
    bars: int | None
    source_id: str | None
    source_type: str
    timestamp: datetime


@dataclass(frozen=True)
class MaintenanceRuleSet:
    rule_id: str | None
    lookback_window_bars: int | None
    lookback_window_trades: int | None
    maintenance_min_sharpe: float | None
    maintenance_min_win_rate: float | None
    maintenance_max_drawdown: float


@dataclass(frozen=True)
class DemotionCandidate:
    strategy_id: str
    source_id: str | None
    source_type: str
    observed_drawdown: float | None
    max_strategy_drawdown: float
    breach: bool
    old_state: str
    recommended_state: str | None
    should_disable: bool
    should_zero_allocation: bool
    should_freeze: bool
    breach_key: str | None
    status: str
    reasons: list[str]
    breached_metric: str | None = None
    breached_value: float | None = None
    threshold: float | None = None
    observed_sharpe: float | None = None
    observed_win_rate: float | None = None
    trade_count: int | None = None
    lookback_window: dict[str, int | None] | None = None
    rule_id: str | None = None


@dataclass(frozen=True)
class AutoDemotionRunResult:
    run_id: str | None
    auto_demote_on_breach: bool
    max_strategy_drawdown: float
    candidates: list[DemotionCandidate]
    demotions_executed: list[dict[str, Any]]
    skipped_reason: str | None
    audit_event_type: str
    dry_run: bool = False


class AutoDemotionService:
    def __init__(
        self,
        *,
        session: Session,
        operator_settings_repo: OperatorSettingsRepository | None = None,
        promotion_rules_repo: PromotionRulesRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
        control_state_repo: StrategyControlStateRepository | None = None,
        allocation_overrides_repo: AllocationOverridesRepository | None = None,
        governance_service: StrategyGovernanceService | None = None,
        freeze_service: TradingFreezeService | None = None,
        governance_audit_service: GovernanceAuditService | None = None,
    ) -> None:
        self._session = session
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._promotion_rules_repo = promotion_rules_repo or PromotionRulesRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._control_state_repo = control_state_repo or StrategyControlStateRepository(session)
        self._allocation_overrides_repo = (
            allocation_overrides_repo or AllocationOverridesRepository(session)
        )
        self._governance_service = governance_service or StrategyGovernanceService(
            session=session,
            promotion_rules_repo=self._promotion_rules_repo,
            audit_log_repo=self._audit_log_repo,
        )
        self._freeze_service = freeze_service or TradingFreezeService()
        self._governance_audit_service = governance_audit_service or GovernanceAuditService(
            session=session,
            repo=GovernanceAuditRepository(session),
            audit_log_repo=self._audit_log_repo,
        )

    def scan(self) -> list[DemotionCandidate]:
        start = perf_counter()
        settings = self._operator_settings_repo.get_or_create_default()
        threshold = float(settings.max_strategy_drawdown)
        rows = self._candidate_governance_rows()
        rules = self._active_rules_by_transition()

        logger.info(
            "auto_demotion.scan_started",
            extra={"strategies_evaluated": len(rows), "max_strategy_drawdown": threshold},
        )
        auto_demotion_scan_total.add(1)

        candidates = [
            self._evaluate_strategy(row, max_strategy_drawdown=threshold, rules=rules)
            for row in rows
        ]
        breach_count = sum(1 for candidate in candidates if candidate.status == "breach")
        if breach_count:
            auto_demotion_breach_total.add(breach_count)
        auto_demotion_scan_duration_seconds.record(max(0.0, perf_counter() - start))
        logger.info(
            "auto_demotion.scan_completed",
            extra={
                "strategies_evaluated": len(candidates),
                "breach_count": breach_count,
                "skipped_count": sum(1 for row in candidates if row.status.startswith("skipped")),
            },
        )
        return candidates

    def run(
        self,
        *,
        run_id: str | None = None,
        actor: str = AUTO_DEMOTION_ACTOR,
        enforce_enabled: bool = True,
        dry_run: bool = False,
    ) -> AutoDemotionRunResult:
        now = datetime.now(UTC)
        settings = self._operator_settings_repo.get_or_create_default()
        candidates = self.scan()

        if enforce_enabled and not bool(settings.auto_demote_on_breach):
            result = AutoDemotionRunResult(
                run_id=run_id,
                auto_demote_on_breach=False,
                max_strategy_drawdown=float(settings.max_strategy_drawdown),
                candidates=candidates,
                demotions_executed=[],
                skipped_reason="auto_demote_disabled",
                audit_event_type=AUTO_DEMOTION_SKIPPED_EVENT,
                dry_run=dry_run,
            )
            self._audit_run(result=result, actor=actor, occurred_at=now)
            for candidate in candidates:
                self._record_governance_demotion_evidence(
                    candidate=candidate,
                    actor=actor,
                    from_state=candidate.old_state,
                    to_state=candidate.recommended_state,
                    status="auto_demote_disabled",
                    run_id=run_id,
                    now=now,
                )
            self._emit_drawdown_alerts_if_enabled(settings=settings, candidates=candidates, now=now)
            self._session.flush()
            self._session.commit()
            return result

        if dry_run:
            result = AutoDemotionRunResult(
                run_id=run_id,
                auto_demote_on_breach=bool(settings.auto_demote_on_breach),
                max_strategy_drawdown=float(settings.max_strategy_drawdown),
                candidates=candidates,
                demotions_executed=[],
                skipped_reason="dry_run",
                audit_event_type=AUTO_DEMOTION_COMPLETED_EVENT,
                dry_run=True,
            )
            self._audit_run(result=result, actor=actor, occurred_at=now)
            for candidate in candidates:
                self._record_governance_demotion_evidence(
                    candidate=candidate,
                    actor=actor,
                    from_state=candidate.old_state,
                    to_state=candidate.recommended_state,
                    status="dry_run",
                    run_id=run_id,
                    now=now,
                )
            self._session.flush()
            self._session.commit()
            return result

        executed: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.status != "breach" or candidate.recommended_state is None:
                continue
            if candidate.breach_key is not None and self._breach_already_demoted(
                candidate.breach_key
            ):
                continue

            try:
                transition = self._governance_service.transition(
                    strategy_id=candidate.strategy_id,
                    to_state=candidate.recommended_state,
                    reason="; ".join(candidate.reasons),
                    updated_by=actor,
                    actor_role="system_risk",
                    record_governance_audit=False,
                )
            except Exception as exc:
                self._session.rollback()
                logger.warning(
                    "auto_demotion.transition_failed",
                    extra={
                        "strategy_id": candidate.strategy_id,
                        "from_state": candidate.old_state,
                        "to_state": candidate.recommended_state,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                executed.append(
                    {
                        "strategy_id": candidate.strategy_id,
                        "old_governance_state": candidate.old_state,
                        "new_governance_state": candidate.recommended_state,
                        "breached_metric": candidate.breached_metric,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue

            if candidate.should_disable:
                self._control_state_repo.set_enabled(
                    strategy_id=candidate.strategy_id,
                    enabled=False,
                    reason="; ".join(candidate.reasons),
                    updated_by=actor,
                    updated_at=now,
                )
            if candidate.should_zero_allocation:
                self._zero_allocation(candidate=candidate, actor=actor, now=now)
            if candidate.should_freeze:
                self._freeze_service.freeze_trading(
                    reason="; ".join(candidate.reasons),
                    source="auto_demotion",
                )

            self._audit_demotion(
                candidate=candidate,
                transition=transition,
                actor=actor,
                now=now,
                notify=bool(settings.notify_strategy_demotion_events),
            )
            self._record_governance_demotion_evidence(
                candidate=candidate,
                actor=actor,
                from_state=transition.from_state,
                to_state=transition.to_state,
                status="demoted",
                run_id=run_id,
                now=now,
            )
            self._emit_drawdown_alerts_if_enabled(
                settings=settings,
                candidates=[candidate],
                now=now,
            )
            strategy_demotion_total.add(1)
            executed.append(
                {
                    "strategy_id": candidate.strategy_id,
                    "old_governance_state": transition.from_state,
                    "new_governance_state": transition.to_state,
                    "observed_drawdown": candidate.observed_drawdown,
                    "max_strategy_drawdown": candidate.max_strategy_drawdown,
                    "breached_metric": candidate.breached_metric,
                    "breached_value": candidate.breached_value,
                    "threshold": candidate.threshold,
                    "breach_key": candidate.breach_key,
                    "strategy_disabled": candidate.should_disable,
                    "allocation_after": 0.0 if candidate.should_zero_allocation else None,
                    "status": "demoted",
                }
            )

        result = AutoDemotionRunResult(
            run_id=run_id,
            auto_demote_on_breach=bool(settings.auto_demote_on_breach),
            max_strategy_drawdown=float(settings.max_strategy_drawdown),
            candidates=candidates,
            demotions_executed=executed,
            skipped_reason=None,
            audit_event_type=AUTO_DEMOTION_COMPLETED_EVENT,
            dry_run=False,
        )
        self._audit_run(result=result, actor=actor, occurred_at=now)
        executed_strategy_ids = {
            row["strategy_id"] for row in executed if row.get("status") == "demoted"
        }
        for candidate in candidates:
            if candidate.strategy_id in executed_strategy_ids:
                continue
            self._record_governance_demotion_evidence(
                candidate=candidate,
                actor=actor,
                from_state=candidate.old_state,
                to_state=candidate.recommended_state,
                status=candidate.status if not dry_run else "dry_run",
                run_id=run_id,
                now=now,
            )
        self._session.flush()
        self._session.commit()
        logger.info(
            "auto_demotion.run_completed",
            extra={
                "strategies_evaluated": len(candidates),
                "breach_count": sum(1 for row in candidates if row.status == "breach"),
                "demotion_count": sum(1 for row in executed if row.get("status") == "demoted"),
                "failed_transition_count": sum(
                    1 for row in executed if row.get("status") == "failed"
                ),
            },
        )
        return result

    def _record_governance_demotion_evidence(
        self,
        *,
        candidate: DemotionCandidate,
        actor: str,
        from_state: str,
        to_state: str | None,
        status: str,
        run_id: str | None,
        now: datetime,
    ) -> None:
        self._governance_audit_service.record_demotion_decision(
            strategy_id=candidate.strategy_id,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            trigger_source=TriggerSource.AUTO_DEMOTION,
            breach=candidate.breach,
            breached_metric=candidate.breached_metric,
            breached_value=candidate.breached_value,
            threshold=candidate.threshold,
            source_id=candidate.source_id,
            source_type=candidate.source_type,
            reasons=candidate.reasons,
            status=status,
            evaluation_run_id=run_id,
            now=now,
        )

    def _evaluate_strategy(
        self,
        governance: StrategyGovernance,
        *,
        max_strategy_drawdown: float,
        rules: dict[tuple[str, str], PromotionRules],
    ) -> DemotionCandidate:
        observation = self._latest_strategy_observation(governance.strategy_id)
        old_state = governance.current_state
        new_state = _NEXT_STATE.get(old_state)
        maintenance = self._maintenance_rules_for(
            current_state=old_state,
            max_strategy_drawdown=max_strategy_drawdown,
            rules=rules,
        )
        lookback_window = {
            "bars": maintenance.lookback_window_bars,
            "trades": maintenance.lookback_window_trades,
        }

        sample_skip = self._sample_skip_reason(observation, maintenance)
        if sample_skip is not None:
            return self._candidate(
                governance=governance,
                observation=observation,
                maintenance=maintenance,
                breach=False,
                status="skipped_insufficient_sample",
                recommended_state=None,
                reasons=[sample_skip],
                lookback_window=lookback_window,
            )

        breach = self._first_breach(observation=observation, maintenance=maintenance)
        if breach is None:
            return self._candidate(
                governance=governance,
                observation=observation,
                maintenance=maintenance,
                breach=False,
                status="no_breach",
                recommended_state=None,
                reasons=["maintenance metrics are within configured thresholds"],
                lookback_window=lookback_window,
            )

        breach_metric, breach_value, threshold, reason = breach
        breach_key = self._breach_key(
            strategy_id=governance.strategy_id,
            source_type=observation.source_type,
            source_id=observation.source_id,
            breached_metric=breach_metric,
            threshold=threshold,
        )
        if breach_key is not None and self._breach_already_demoted(breach_key):
            return self._candidate(
                governance=governance,
                observation=observation,
                maintenance=maintenance,
                breach=True,
                status="already_demoted",
                recommended_state=None,
                reasons=[f"breach key {breach_key} has already produced a demotion"],
                lookback_window=lookback_window,
                breach_key=breach_key,
                breached_metric=breach_metric,
                breached_value=breach_value,
                threshold=threshold,
            )

        severe = (
            breach_metric == "drawdown"
            and breach_value is not None
            and abs(float(breach_value)) >= maintenance.maintenance_max_drawdown * 2
        )
        return self._candidate(
            governance=governance,
            observation=observation,
            maintenance=maintenance,
            breach=True,
            status="breach",
            recommended_state=new_state,
            reasons=[reason, f"recommended governance state: {new_state}"],
            lookback_window=lookback_window,
            breach_key=breach_key,
            breached_metric=breach_metric,
            breached_value=breach_value,
            threshold=threshold,
            should_disable=True,
            should_zero_allocation=True,
            should_freeze=severe,
        )

    def _candidate(
        self,
        *,
        governance: StrategyGovernance,
        observation: PerformanceObservation,
        maintenance: MaintenanceRuleSet,
        breach: bool,
        status: str,
        recommended_state: str | None,
        reasons: list[str],
        lookback_window: dict[str, int | None],
        breach_key: str | None = None,
        breached_metric: str | None = None,
        breached_value: float | None = None,
        threshold: float | None = None,
        should_disable: bool = False,
        should_zero_allocation: bool = False,
        should_freeze: bool = False,
    ) -> DemotionCandidate:
        return DemotionCandidate(
            strategy_id=governance.strategy_id,
            source_id=observation.source_id,
            source_type=observation.source_type,
            observed_drawdown=observation.drawdown,
            max_strategy_drawdown=maintenance.maintenance_max_drawdown,
            breach=breach,
            old_state=governance.current_state,
            recommended_state=recommended_state,
            should_disable=should_disable,
            should_zero_allocation=should_zero_allocation,
            should_freeze=should_freeze,
            breach_key=breach_key,
            status=status,
            reasons=reasons,
            breached_metric=breached_metric,
            breached_value=breached_value,
            threshold=threshold,
            observed_sharpe=observation.sharpe,
            observed_win_rate=observation.win_rate,
            trade_count=observation.trade_count,
            lookback_window=lookback_window,
            rule_id=maintenance.rule_id,
        )

    def _maintenance_rules_for(
        self,
        *,
        current_state: str,
        max_strategy_drawdown: float,
        rules: dict[tuple[str, str], PromotionRules],
    ) -> MaintenanceRuleSet:
        rule = rules.get(_MAINTENANCE_RULE_STATUS[current_state])
        return MaintenanceRuleSet(
            rule_id=rule.rule_id if rule is not None else None,
            lookback_window_bars=rule.lookback_window_bars if rule is not None else None,
            lookback_window_trades=rule.lookback_window_trades if rule is not None else None,
            maintenance_min_sharpe=(
                rule.maintenance_min_sharpe
                if rule is not None and rule.maintenance_min_sharpe is not None
                else -1.0
            ),
            maintenance_min_win_rate=(rule.maintenance_min_win_rate if rule is not None else None),
            maintenance_max_drawdown=(
                rule.maintenance_max_drawdown
                if rule is not None and rule.maintenance_max_drawdown is not None
                else max_strategy_drawdown
            ),
        )

    def _sample_skip_reason(
        self,
        observation: PerformanceObservation,
        maintenance: MaintenanceRuleSet,
    ) -> str | None:
        if observation.source_id is None:
            return "no recent performance observation available"
        if (
            maintenance.lookback_window_trades is not None
            and observation.trade_count is not None
            and observation.trade_count < maintenance.lookback_window_trades
        ):
            return (
                f"trade_count {observation.trade_count} below lookback_window_trades "
                f"{maintenance.lookback_window_trades}"
            )
        if (
            maintenance.lookback_window_bars is not None
            and observation.bars is not None
            and observation.bars < maintenance.lookback_window_bars
        ):
            return (
                f"bars {observation.bars} below lookback_window_bars "
                f"{maintenance.lookback_window_bars}"
            )
        return None

    def _first_breach(
        self,
        *,
        observation: PerformanceObservation,
        maintenance: MaintenanceRuleSet,
    ) -> tuple[str, float | None, float, str] | None:
        if observation.drawdown is not None and self._drawdown_exceeds(
            observation.drawdown,
            maintenance.maintenance_max_drawdown,
        ):
            return (
                "drawdown",
                observation.drawdown,
                maintenance.maintenance_max_drawdown,
                (
                    f"observed drawdown {observation.drawdown} exceeds "
                    f"max_strategy_drawdown {maintenance.maintenance_max_drawdown}"
                ),
            )
        if (
            maintenance.maintenance_min_sharpe is not None
            and observation.sharpe is not None
            and observation.sharpe < maintenance.maintenance_min_sharpe
        ):
            return (
                "sharpe",
                observation.sharpe,
                maintenance.maintenance_min_sharpe,
                (
                    f"trailing sharpe {observation.sharpe} below maintenance_min_sharpe "
                    f"{maintenance.maintenance_min_sharpe}"
                ),
            )
        if (
            maintenance.maintenance_min_win_rate is not None
            and observation.win_rate is not None
            and observation.win_rate < maintenance.maintenance_min_win_rate
        ):
            return (
                "win_rate",
                observation.win_rate,
                maintenance.maintenance_min_win_rate,
                (
                    f"trailing win_rate {observation.win_rate} below maintenance_min_win_rate "
                    f"{maintenance.maintenance_min_win_rate}"
                ),
            )
        return None

    def _latest_strategy_observation(self, strategy_id: str) -> PerformanceObservation:
        metrics = self._latest_metrics_for_strategy(strategy_id)
        risk = self._latest_risk_for_strategy(strategy_id)
        if risk is not None and (metrics is None or risk.timestamp >= metrics.timestamp):
            return risk
        if metrics is not None:
            return metrics
        return PerformanceObservation(
            drawdown=None,
            sharpe=None,
            win_rate=None,
            trade_count=None,
            bars=None,
            source_id=None,
            source_type="none",
            timestamp=datetime.min.replace(tzinfo=UTC),
        )

    def _latest_metrics_for_strategy(self, strategy_id: str) -> PerformanceObservation | None:
        rows = self._session.scalars(
            select(MetricsSummary).order_by(
                MetricsSummary.created_at.desc(),
                MetricsSummary.metrics_snapshot_id.asc(),
            )
        ).all()
        for row in rows:
            if (row.metrics_json or {}).get("strategy_id") != strategy_id:
                continue
            metrics_json = row.metrics_json or {}
            win_rate = metrics_json.get("win_rate")
            if win_rate is None and row.trade_count and row.winning_trade_count is not None:
                win_rate = row.winning_trade_count / row.trade_count
            return PerformanceObservation(
                drawdown=row.max_drawdown,
                sharpe=row.sharpe_ratio,
                win_rate=float(win_rate) if win_rate is not None else None,
                trade_count=row.trade_count,
                bars=metrics_json.get("bars", metrics_json.get("days_tested")),
                source_id=row.metrics_snapshot_id,
                source_type="metrics_summary",
                timestamp=row.created_at,
            )
        return None

    def _latest_risk_for_strategy(self, strategy_id: str) -> PerformanceObservation | None:
        rows = self._session.scalars(
            select(RiskSnapshot).order_by(RiskSnapshot.timestamp.desc())
        ).all()
        for row in rows:
            utilization = row.utilization or {}
            strategy_drawdowns = utilization.get("strategy_drawdowns")
            if isinstance(strategy_drawdowns, dict) and strategy_id in strategy_drawdowns:
                return PerformanceObservation(
                    drawdown=float(strategy_drawdowns[strategy_id]),
                    sharpe=self._nested_strategy_metric(
                        utilization, "strategy_sharpes", strategy_id
                    ),
                    win_rate=self._nested_strategy_metric(
                        utilization,
                        "strategy_win_rates",
                        strategy_id,
                    ),
                    trade_count=self._nested_strategy_int(
                        utilization,
                        "strategy_trade_counts",
                        strategy_id,
                    ),
                    bars=self._nested_strategy_int(utilization, "strategy_bars", strategy_id),
                    source_id=str(row.snapshot_id),
                    source_type="risk_snapshot",
                    timestamp=row.timestamp,
                )
            if utilization.get("strategy_id") == strategy_id:
                return PerformanceObservation(
                    drawdown=row.drawdown_pct,
                    sharpe=utilization.get("sharpe"),
                    win_rate=utilization.get("win_rate"),
                    trade_count=utilization.get("trade_count"),
                    bars=utilization.get("bars"),
                    source_id=str(row.snapshot_id),
                    source_type="risk_snapshot",
                    timestamp=row.timestamp,
                )
        return None

    def _zero_allocation(
        self,
        *,
        candidate: DemotionCandidate,
        actor: str,
        now: datetime,
    ) -> None:
        self._allocation_overrides_repo.deactivate_override(candidate.strategy_id)
        self._session.flush()
        self._allocation_overrides_repo.create_override(
            AllocationOverrides(
                override_id=str(uuid4()),
                strategy_id=candidate.strategy_id,
                overridden_by=actor,
                override_reason=f"auto demotion: {'; '.join(candidate.reasons)}",
                max_pct_of_capital=0.0,
                max_position_size_usd=None,
                max_drawdown_allowed=None,
                is_active=True,
                created_at=now,
                expires_at=None,
            )
        )

    def _candidate_governance_rows(self) -> list[StrategyGovernance]:
        rows = self._session.scalars(
            select(StrategyGovernance)
            .where(StrategyGovernance.current_state.in_(_DEMOTABLE_STATES))
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

    def _breach_already_demoted(self, breach_key: str) -> bool:
        return (
            self._session.query(AuditLogRow)
            .filter(AuditLogRow.event_type == AUTO_DEMOTION_AUDIT_EVENT)
            .filter(AuditLogRow.metadata_["breach_key"].as_string() == breach_key)
            .first()
            is not None
        )

    def _audit_demotion(
        self,
        *,
        candidate: DemotionCandidate,
        transition,
        actor: str,
        now: datetime,
        notify: bool,
    ) -> None:
        metadata = {
            "strategy_id": candidate.strategy_id,
            "previous_state": transition.from_state,
            "new_state": transition.to_state,
            "from_state": transition.from_state,
            "to_state": transition.to_state,
            "breached_metric": candidate.breached_metric,
            "breached_value": candidate.breached_value,
            "threshold": candidate.threshold,
            "lookback_window": candidate.lookback_window,
            "evaluated_at": now.isoformat(),
            "actor_role": "system_risk",
            "source_id": candidate.source_id,
            "source_type": candidate.source_type,
            "breach_key": candidate.breach_key,
            "allocation_after": 0.0 if candidate.should_zero_allocation else None,
            "strategy_disabled": candidate.should_disable,
        }
        self._audit_log_repo.record_operator_action(
            action=AUTO_DEMOTION_AUDIT_EVENT,
            actor=actor,
            reason="; ".join(candidate.reasons),
            occurred_at=now,
            component="governance",
            metadata=metadata,
        )
        if notify:
            self._audit_log_repo.record_operator_action(
                action=AUTO_DEMOTION_EVENT,
                actor=actor,
                reason="; ".join(candidate.reasons),
                occurred_at=now,
                component="notifications",
                metadata={"channel": "notify_strategy_demotion_events", **metadata},
            )

    def _emit_drawdown_alerts_if_enabled(
        self,
        *,
        settings: OperatorSettingsRow,
        candidates: list[DemotionCandidate],
        now: datetime,
    ) -> None:
        if not bool(settings.notify_drawdown_alerts):
            return
        for candidate in candidates:
            if candidate.breach and candidate.breached_metric == "drawdown":
                self._emit_drawdown_alert(candidate=candidate, actor=AUTO_DEMOTION_ACTOR, now=now)

    def _emit_drawdown_alert(
        self,
        *,
        candidate: DemotionCandidate,
        actor: str,
        now: datetime,
    ) -> None:
        self._audit_log_repo.record_operator_action(
            action="DRAWDOWN_ALERT_EVENT",
            actor=actor,
            reason="; ".join(candidate.reasons),
            occurred_at=now,
            component="notifications",
            metadata={
                "channel": "notify_drawdown_alerts",
                "strategy_id": candidate.strategy_id,
                "observed_drawdown": candidate.observed_drawdown,
                "max_strategy_drawdown": candidate.max_strategy_drawdown,
                "breach_key": candidate.breach_key,
                "governance_state": candidate.old_state,
            },
        )

    def _audit_run(
        self,
        *,
        result: AutoDemotionRunResult,
        actor: str,
        occurred_at: datetime,
    ) -> None:
        self._audit_log_repo.record_operator_action(
            action=result.audit_event_type,
            actor=actor,
            reason=result.skipped_reason or "automatic demotion scan completed",
            occurred_at=occurred_at,
            component="governance",
            metadata=self.result_to_jsonable(result),
        )

    @staticmethod
    def result_to_jsonable(result: AutoDemotionRunResult) -> dict[str, Any]:
        return {
            "run_id": result.run_id,
            "auto_demote_on_breach": result.auto_demote_on_breach,
            "max_strategy_drawdown": result.max_strategy_drawdown,
            "dry_run": result.dry_run,
            "candidate_strategies": [
                {
                    "strategy_id": row.strategy_id,
                    "source_id": row.source_id,
                    "source_type": row.source_type,
                    "observed_drawdown": row.observed_drawdown,
                    "observed_sharpe": row.observed_sharpe,
                    "observed_win_rate": row.observed_win_rate,
                    "trade_count": row.trade_count,
                    "max_strategy_drawdown": row.max_strategy_drawdown,
                    "breach_status": row.breach,
                    "breached_metric": row.breached_metric,
                    "breached_value": row.breached_value,
                    "threshold": row.threshold,
                    "lookback_window": row.lookback_window,
                    "rule_id": row.rule_id,
                    "old_governance_state": row.old_state,
                    "new_governance_state": row.recommended_state,
                    "should_disable": row.should_disable,
                    "should_zero_allocation": row.should_zero_allocation,
                    "should_freeze": row.should_freeze,
                    "breach_key": row.breach_key,
                    "status": row.status,
                    "reasons": row.reasons,
                }
                for row in result.candidates
            ],
            "demotions_executed": result.demotions_executed,
            "skipped_reason": result.skipped_reason,
            "audit_event_emitted": result.audit_event_type,
        }

    @staticmethod
    def _drawdown_exceeds(actual: float, limit: float) -> bool:
        if limit < 0:
            return actual < limit
        return abs(actual) > limit

    @staticmethod
    def _breach_key(
        *,
        strategy_id: str,
        source_type: str,
        source_id: str | None,
        breached_metric: str,
        threshold: float,
    ) -> str | None:
        if source_id is None:
            return None
        return f"{strategy_id}:{source_type}:{source_id}:{breached_metric}:{threshold}"

    @staticmethod
    def _nested_strategy_metric(
        utilization: dict[str, Any],
        key: str,
        strategy_id: str,
    ) -> float | None:
        values = utilization.get(key)
        if not isinstance(values, dict) or strategy_id not in values:
            return None
        return float(values[strategy_id])

    @staticmethod
    def _nested_strategy_int(
        utilization: dict[str, Any],
        key: str,
        strategy_id: str,
    ) -> int | None:
        value = AutoDemotionService._nested_strategy_metric(utilization, key, strategy_id)
        return int(value) if value is not None else None

    def _latest_governance(self, strategy_id: str) -> StrategyGovernance | None:
        return cast(
            StrategyGovernance | None,
            self._session.scalars(
                select(StrategyGovernance)
                .where(StrategyGovernance.strategy_id == strategy_id)
                .order_by(StrategyGovernance.updated_at.desc())
                .limit(1)
            ).one_or_none(),
        )
