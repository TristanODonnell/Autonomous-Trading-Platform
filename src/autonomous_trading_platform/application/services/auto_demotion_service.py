from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.execution.services.trading_freeze_service import (
    TradingFreezeService,
)
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.operator_settings import OperatorSettingsRow
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
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)

AUTO_DEMOTION_ACTOR = "auto_demotion"
AUTO_DEMOTION_EVENT = "STRATEGY_AUTO_DEMOTED"

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


@dataclass(frozen=True)
class AutoDemotionRunResult:
    run_id: str | None
    auto_demote_on_breach: bool
    max_strategy_drawdown: float
    candidates: list[DemotionCandidate]
    demotions_executed: list[dict[str, Any]]
    skipped_reason: str | None
    audit_event_type: str


class AutoDemotionService:
    def __init__(
        self,
        *,
        session: Session,
        operator_settings_repo: OperatorSettingsRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
        control_state_repo: StrategyControlStateRepository | None = None,
        allocation_overrides_repo: AllocationOverridesRepository | None = None,
        freeze_service: TradingFreezeService | None = None,
    ) -> None:
        self._session = session
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._control_state_repo = control_state_repo or StrategyControlStateRepository(session)
        self._allocation_overrides_repo = (
            allocation_overrides_repo or AllocationOverridesRepository(session)
        )
        self._freeze_service = freeze_service or TradingFreezeService()

    def scan(self) -> list[DemotionCandidate]:
        settings = self._operator_settings_repo.get_or_create_default()
        threshold = float(settings.max_strategy_drawdown)
        rows = self._candidate_governance_rows()
        return [self._evaluate_strategy(row, max_strategy_drawdown=threshold) for row in rows]

    def run(
        self,
        *,
        run_id: str | None = None,
        actor: str = AUTO_DEMOTION_ACTOR,
        enforce_enabled: bool = True,
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
                audit_event_type="STRATEGY_AUTO_DEMOTION_SKIPPED",
            )
            self._audit_run(result=result, actor=actor, occurred_at=now)
            if self._should_notify_drawdown_alert(settings):
                for candidate in candidates:
                    if candidate.breach:
                        self._emit_drawdown_alert(candidate=candidate, actor=actor, occurred_at=now)
            self._session.flush()
            self._session.commit()
            return result

        executed: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.status != "breach" or candidate.recommended_state is None:
                continue
            row = self._latest_governance(candidate.strategy_id)
            if row is None or row.current_state != candidate.old_state:
                continue

            old_state = row.current_state
            row.current_state = candidate.recommended_state
            row.updated_at = now
            reason = "; ".join(candidate.reasons)

            if candidate.should_disable:
                self._control_state_repo.set_enabled(
                    strategy_id=candidate.strategy_id,
                    enabled=False,
                    reason=reason,
                    updated_by=actor,
                    updated_at=now,
                )

            if candidate.should_zero_allocation:
                self._zero_allocation(candidate=candidate, actor=actor, now=now)

            if candidate.should_freeze:
                self._freeze_service.freeze_trading(
                    reason=reason,
                    source="auto_demotion",
                )

            self._audit_log_repo.record_operator_action(
                action=AUTO_DEMOTION_EVENT,
                actor=actor,
                reason=reason,
                occurred_at=now,
                component="governance",
                metadata={
                    "strategy_id": candidate.strategy_id,
                    "from_state": old_state,
                    "to_state": candidate.recommended_state,
                    "observed_drawdown": candidate.observed_drawdown,
                    "max_strategy_drawdown": candidate.max_strategy_drawdown,
                    "source_id": candidate.source_id,
                    "source_type": candidate.source_type,
                    "breach_key": candidate.breach_key,
                    "allocation_after": 0.0 if candidate.should_zero_allocation else None,
                    "strategy_disabled": candidate.should_disable,
                },
            )
            if self._should_notify_demotion(settings):
                self._audit_log_repo.record_operator_action(
                    action="STRATEGY_DEMOTION_EVENT",
                    actor=actor,
                    reason=reason,
                    occurred_at=now,
                    component="notifications",
                    metadata={
                        "channel": "notify_strategy_demotion_events",
                        "strategy_id": candidate.strategy_id,
                        "from_state": old_state,
                        "to_state": candidate.recommended_state,
                        "observed_drawdown": candidate.observed_drawdown,
                        "breach_key": candidate.breach_key,
                    },
                )
            if self._should_notify_drawdown_alert(settings):
                self._emit_drawdown_alert(candidate=candidate, actor=actor, occurred_at=now)
            executed.append(
                {
                    "strategy_id": candidate.strategy_id,
                    "old_governance_state": old_state,
                    "new_governance_state": candidate.recommended_state,
                    "observed_drawdown": candidate.observed_drawdown,
                    "max_strategy_drawdown": candidate.max_strategy_drawdown,
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
            audit_event_type="STRATEGY_AUTO_DEMOTION_COMPLETED",
        )
        self._audit_run(result=result, actor=actor, occurred_at=now)
        self._session.flush()
        self._session.commit()
        return result

    def _evaluate_strategy(
        self,
        governance: StrategyGovernance,
        *,
        max_strategy_drawdown: float,
    ) -> DemotionCandidate:
        observed = self._latest_strategy_drawdown(governance.strategy_id)
        old_state = governance.current_state
        new_state = _NEXT_STATE.get(old_state)
        observed_drawdown = observed["drawdown"]
        source_id = observed["source_id"]
        source_type = observed["source_type"]
        breach = (
            observed_drawdown is not None and abs(float(observed_drawdown)) > max_strategy_drawdown
        )
        breach_key = (
            f"{governance.strategy_id}:{source_type}:{source_id}:{max_strategy_drawdown}"
            if breach and source_id is not None
            else None
        )

        if not breach:
            return DemotionCandidate(
                strategy_id=governance.strategy_id,
                source_id=source_id,
                source_type=source_type,
                observed_drawdown=observed_drawdown,
                max_strategy_drawdown=max_strategy_drawdown,
                breach=False,
                old_state=old_state,
                recommended_state=None,
                should_disable=False,
                should_zero_allocation=False,
                should_freeze=False,
                breach_key=breach_key,
                status="no_breach",
                reasons=[
                    (
                        f"observed drawdown {observed_drawdown} does not exceed "
                        f"max_strategy_drawdown {max_strategy_drawdown}"
                    )
                ],
            )

        if breach_key is not None and self._breach_already_demoted(breach_key):
            return DemotionCandidate(
                strategy_id=governance.strategy_id,
                source_id=source_id,
                source_type=source_type,
                observed_drawdown=observed_drawdown,
                max_strategy_drawdown=max_strategy_drawdown,
                breach=True,
                old_state=old_state,
                recommended_state=None,
                should_disable=False,
                should_zero_allocation=False,
                should_freeze=False,
                breach_key=breach_key,
                status="already_demoted",
                reasons=[f"breach key {breach_key} has already produced a demotion"],
            )

        severe = abs(float(observed_drawdown or 0)) >= max_strategy_drawdown * 2
        return DemotionCandidate(
            strategy_id=governance.strategy_id,
            source_id=source_id,
            source_type=source_type,
            observed_drawdown=observed_drawdown,
            max_strategy_drawdown=max_strategy_drawdown,
            breach=True,
            old_state=old_state,
            recommended_state=new_state,
            should_disable=True,
            should_zero_allocation=True,
            should_freeze=severe,
            breach_key=breach_key,
            status="breach",
            reasons=[
                (
                    f"observed drawdown {observed_drawdown} exceeds "
                    f"max_strategy_drawdown {max_strategy_drawdown}"
                ),
                f"recommended governance state: {new_state}",
            ],
        )

    def _latest_strategy_drawdown(self, strategy_id: str) -> dict[str, Any]:
        metrics = self._latest_metrics_for_strategy(strategy_id)
        risk = self._latest_risk_for_strategy(strategy_id)
        if risk is not None and (metrics is None or risk["timestamp"] >= metrics["timestamp"]):
            return risk
        if metrics is not None:
            return metrics
        return {
            "drawdown": None,
            "source_id": None,
            "source_type": "none",
            "timestamp": datetime.min.replace(tzinfo=UTC),
        }

    def _latest_metrics_for_strategy(self, strategy_id: str) -> dict[str, Any] | None:
        rows = self._session.scalars(
            select(MetricsSummary).order_by(
                MetricsSummary.created_at.desc(),
                MetricsSummary.metrics_snapshot_id.asc(),
            )
        ).all()
        for row in rows:
            if (row.metrics_json or {}).get("strategy_id") != strategy_id:
                continue
            return {
                "drawdown": row.max_drawdown,
                "source_id": row.metrics_snapshot_id,
                "source_type": "metrics_summary",
                "timestamp": row.created_at,
            }
        return None

    def _latest_risk_for_strategy(self, strategy_id: str) -> dict[str, Any] | None:
        rows = self._session.scalars(
            select(RiskSnapshot).order_by(RiskSnapshot.timestamp.desc())
        ).all()
        for row in rows:
            utilization = row.utilization or {}
            strategy_drawdowns = utilization.get("strategy_drawdowns")
            if isinstance(strategy_drawdowns, dict) and strategy_id in strategy_drawdowns:
                return {
                    "drawdown": float(strategy_drawdowns[strategy_id]),
                    "source_id": str(row.snapshot_id),
                    "source_type": "risk_snapshot",
                    "timestamp": row.timestamp,
                }
            strategy_id_value = utilization.get("strategy_id")
            if strategy_id_value == strategy_id:
                return {
                    "drawdown": row.drawdown_pct,
                    "source_id": str(row.snapshot_id),
                    "source_type": "risk_snapshot",
                    "timestamp": row.timestamp,
                }
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

    def _breach_already_demoted(self, breach_key: str) -> bool:
        return (
            self._session.query(AuditLogRow)
            .filter(AuditLogRow.event_type == AUTO_DEMOTION_EVENT)
            .filter(AuditLogRow.metadata_["breach_key"].as_string() == breach_key)
            .first()
            is not None
        )

    def _should_notify_demotion(self, settings: OperatorSettingsRow) -> bool:
        return bool(settings.notify_strategy_demotion_events)

    def _should_notify_drawdown_alert(self, settings: OperatorSettingsRow) -> bool:
        return bool(settings.notify_drawdown_alerts)

    def _emit_drawdown_alert(
        self,
        *,
        candidate: DemotionCandidate,
        actor: str,
        occurred_at: datetime,
    ) -> None:
        self._audit_log_repo.record_operator_action(
            action="DRAWDOWN_ALERT_EVENT",
            actor=actor,
            reason="; ".join(candidate.reasons),
            occurred_at=occurred_at,
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
            "candidate_strategies": [
                {
                    "strategy_id": row.strategy_id,
                    "source_id": row.source_id,
                    "source_type": row.source_type,
                    "observed_drawdown": row.observed_drawdown,
                    "max_strategy_drawdown": row.max_strategy_drawdown,
                    "breach_status": row.breach,
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
