from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.live_performance_metrics_service import (
    LivePerformanceMetricsService,
    compute_alpha,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.allocation_rebalance_history import (
    AllocationRebalanceHistory,
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
from autonomous_trading_platform.storage.sor.models.strategy_quality_score_history import (
    StrategyQualityScoreHistory,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.allocation_rebalance_history_repository import (
    AllocationRebalanceHistoryRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_quality_score_repository import (
    StrategyQualityScoreRepository,
)

ACTIVE_STATES = {"approved_for_paper_trading", "approved_for_live_trading"}
AUTO_REBALANCE_ACTOR = "auto_rebalance"
DEFAULT_TOTAL_ALLOCATION = Decimal("1")
PCT_QUANT = Decimal("0.000001")
_DEFAULT_INTERVAL_HOURS = 24.0
_DEFAULT_MIN_CHANGE_PCT = Decimal("0.01")

# Stable positive int64 advisory lock key for the rebalance cycle.
# Transaction-scoped (pg_try_advisory_xact_lock) so it auto-releases on commit/rollback.
_REBALANCE_ADVISORY_LOCK_KEY = (
    int.from_bytes(hashlib.sha256(b"ratp_rebalance_cycle_lock").digest()[:8], "big")
    & 0x7FFFFFFFFFFFFFFF
)

_TERMINAL_STATUSES = frozenset(
    {"completed", "noop", "skipped_interval", "skipped_concurrent", "skipped_disabled", "failed"}
)

logger = get_logger(__name__)


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
    # quality_score is the blended score (alpha * live + (1-alpha) * backtest)
    quality_score: Decimal
    metrics: dict[str, float | int | None]
    # Live performance fields (populated when runtime data is available)
    live_metrics: dict[str, float | int | None] = field(default_factory=dict)
    live_score: Decimal = field(default=Decimal("0"))
    backtest_score: Decimal = field(default=Decimal("0"))
    alpha_weight: Decimal = field(default=Decimal("0"))


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
    rebalance_id: str | None
    auto_rebalance_enabled: bool
    before_allocation: dict[str, Decimal]
    after_allocation: dict[str, Decimal]
    proposals: list[StrategyAllocationProposal]
    quality_metrics: dict[str, dict[str, float | int | None]]
    active_policies: list[dict[str, str | float | bool | None]]
    allocation_overrides: list[dict[str, str | float | bool | None]]
    skipped_reason: str | None
    audit_event_type: str
    turnover_metrics: dict[str, float | int]
    allocation_changes_count: int
    noop: bool
    lock_acquired: bool = field(default=False)
    # Per-strategy breakdown of blended scoring for operator transparency
    blended_score_breakdown: dict[str, dict[str, float | None]] = field(default_factory=dict)

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
        rebalance_history_repo: AllocationRebalanceHistoryRepository | None = None,
        live_perf_service: LivePerformanceMetricsService | None = None,
        quality_score_repo: StrategyQualityScoreRepository | None = None,
    ) -> None:
        self._session = session
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)
        self._operator_settings_repo = operator_settings_repo or OperatorSettingsRepository(session)
        self._allocation_overrides_repo = (
            allocation_overrides_repo or AllocationOverridesRepository(session)
        )
        self._rebalance_history_repo = (
            rebalance_history_repo or AllocationRebalanceHistoryRepository(session)
        )
        self._live_perf_service = live_perf_service or LivePerformanceMetricsService(session)
        self._quality_score_repo = quality_score_repo or StrategyQualityScoreRepository(session)

    def rebalance(
        self,
        *,
        run_id: str | None = None,
        rebalance_run_id: str | None = None,
        actor: str = AUTO_REBALANCE_ACTOR,
        enforce_enabled: bool = True,
        trigger_source: str | None = None,
    ) -> QualityReallocationResult:
        now = datetime.now(UTC)
        # Use caller-supplied ID for idempotency; otherwise generate a fresh one.
        rebalance_id = rebalance_run_id or str(uuid4())
        settings = self._operator_settings_repo.get_or_create_default()

        if enforce_enabled and not bool(settings.auto_rebalance_enabled):
            return self._record_skipped(
                run_id=run_id,
                rebalance_id=rebalance_id,
                actor=actor,
                trigger_source=trigger_source,
                settings=settings,
                skipped_reason="auto_rebalance_disabled",
                status="skipped_disabled",
                now=now,
            )

        # --- Idempotency guard: detect retries of an already-completed run ---
        if rebalance_run_id is not None:
            existing = self._rebalance_history_repo.get_by_rebalance_id(rebalance_run_id)
            if existing is not None and existing.status in _TERMINAL_STATUSES:
                return self._record_retry_detected(
                    run_id=run_id,
                    rebalance_id=rebalance_id,
                    existing=existing,
                    actor=actor,
                    settings=settings,
                    now=now,
                )

        # --- Interval guard ---
        _raw_interval = getattr(settings, "min_rebalance_interval_hours", None)
        interval_hours = (
            float(_raw_interval) if _raw_interval is not None else _DEFAULT_INTERVAL_HOURS
        )
        if interval_hours > 0:
            last = self._rebalance_history_repo.get_last_completed()
            if last is not None and last.completed_at is not None:
                # Normalize to UTC: SQLite returns naive datetimes; treat as UTC.
                last_completed_at = last.completed_at
                if last_completed_at.tzinfo is None:
                    last_completed_at = last_completed_at.replace(tzinfo=UTC)
                next_allowed = last_completed_at + timedelta(hours=interval_hours)
                if now < next_allowed:
                    logger.info(
                        "REBALANCE_SKIPPED_INTERVAL_GUARD",
                        extra={
                            "rebalance_id": rebalance_id,
                            "last_completed_at": last.completed_at.isoformat(),
                            "next_allowed_at": next_allowed.isoformat(),
                            "interval_hours": interval_hours,
                        },
                    )
                    return self._record_skipped(
                        run_id=run_id,
                        rebalance_id=rebalance_id,
                        actor=actor,
                        trigger_source=trigger_source,
                        settings=settings,
                        skipped_reason="interval_guard",
                        status="skipped_interval",
                        now=now,
                    )

        # --- Advisory lock (PostgreSQL): transaction-scoped, auto-releases on commit/rollback ---
        if not self._try_acquire_advisory_lock(self._session):
            logger.warning(
                "REBALANCE_SKIPPED_CONCURRENT",
                extra={
                    "rebalance_id": rebalance_id,
                    "skip_reason": "advisory_lock_unavailable",
                    "lock_key": _REBALANCE_ADVISORY_LOCK_KEY,
                    "actor": actor,
                    "run_id": run_id,
                },
            )
            return self._record_skipped(
                run_id=run_id,
                rebalance_id=rebalance_id,
                actor=actor,
                trigger_source=trigger_source,
                settings=settings,
                skipped_reason="concurrent_lock",
                status="skipped_concurrent",
                now=now,
            )

        # --- Row-based concurrent guard (defense-in-depth; primary guard for SQLite) ---
        active_lock = self._rebalance_history_repo.get_active_lock(now=now)
        if active_lock is not None:
            logger.warning(
                "REBALANCE_SKIPPED_CONCURRENT",
                extra={
                    "rebalance_id": rebalance_id,
                    "skip_reason": "active_running_row",
                    "lock_held_by": active_lock.rebalance_id,
                    "lock_started_at": active_lock.started_at.isoformat(),
                    "actor": actor,
                    "run_id": run_id,
                },
            )
            return self._record_skipped(
                run_id=run_id,
                rebalance_id=rebalance_id,
                actor=actor,
                trigger_source=trigger_source,
                settings=settings,
                skipped_reason="concurrent_lock",
                status="skipped_concurrent",
                now=now,
            )

        # --- Lock acquired: log and persist the running row ---
        logger.info(
            "REBALANCE_LOCK_ACQUIRED",
            extra={
                "rebalance_id": rebalance_id,
                "lock_key": _REBALANCE_ADVISORY_LOCK_KEY,
                "actor": actor,
                "run_id": run_id,
                "started_at": now.isoformat(),
            },
        )
        running_row = AllocationRebalanceHistory(
            rebalance_id=rebalance_id,
            run_id=run_id,
            actor=actor,
            trigger_source=trigger_source,
            status="running",
            skipped_reason=None,
            started_at=now,
            completed_at=None,
        )
        self._rebalance_history_repo.insert(running_row)

        wall_start = perf_counter()

        try:
            inputs = self._load_strategy_inputs(
                settings=settings, now=now, rebalance_id=rebalance_id
            )
            before = {item.strategy_id: item.current_allocation_pct for item in inputs}
            proposals = self._compute_proposals(inputs)
            self._persist_quality_scores(inputs=inputs, rebalance_id=rebalance_id, now=now)

            min_change = Decimal(
                str(
                    getattr(settings, "min_allocation_change_pct", None)
                    or float(_DEFAULT_MIN_CHANGE_PCT)
                )
            )
            changes_count, total_delta = self._write_auto_overrides(
                proposals=proposals,
                actor=actor,
                now=now,
                min_allocation_change_pct=min_change,
                rebalance_id=rebalance_id,
            )
            noop = changes_count == 0
            after = before if noop else {p.strategy_id: p.after_pct for p in proposals}
            audit_event = (
                "STRATEGY_ALLOCATION_REBALANCE_NOOP" if noop else "STRATEGY_ALLOCATION_REBALANCED"
            )

            turnover_metrics = self._compute_turnover_metrics(proposals, min_change, settings)
            duration_seconds = perf_counter() - wall_start

            blended_breakdown = {
                item.strategy_id: {
                    "live_score": float(item.live_score),
                    "backtest_score": float(item.backtest_score),
                    "blended_score": float(item.quality_score),
                    "alpha_weight": float(item.alpha_weight),
                    "live_history_days": item.live_metrics.get("days_live"),
                    "live_trade_count": item.live_metrics.get("trade_count"),
                }
                for item in inputs
            }
            result = QualityReallocationResult(
                run_id=run_id,
                rebalance_id=rebalance_id,
                auto_rebalance_enabled=bool(settings.auto_rebalance_enabled),
                before_allocation=before,
                after_allocation=after,
                proposals=proposals,
                quality_metrics={item.strategy_id: item.metrics for item in inputs},
                active_policies=self._active_policy_payloads(),
                allocation_overrides=self._active_override_payloads(now=now),
                skipped_reason="all_changes_below_threshold" if noop else None,
                audit_event_type=audit_event,
                turnover_metrics={**turnover_metrics, "duration_seconds": duration_seconds},
                allocation_changes_count=changes_count,
                noop=noop,
                lock_acquired=True,
                blended_score_breakdown=blended_breakdown,
            )

            final_status = "noop" if noop else "completed"
            self._rebalance_history_repo.update_status(
                rebalance_id=rebalance_id,
                status=final_status,
                completed_at=datetime.now(UTC),
                strategies_evaluated=len(inputs),
                allocation_changes_count=changes_count,
                total_allocation_delta_pct=float(total_delta),
                churn_pct=float(total_delta),
                skipped_reason="all_changes_below_threshold" if noop else None,
                result_summary_json={
                    "proposals_count": len(proposals),
                    "noop": noop,
                    "changes_count": changes_count,
                },
            )

            if noop:
                logger.info(
                    "REBALANCE_NOOP",
                    extra={
                        "rebalance_id": rebalance_id,
                        "strategies_evaluated": len(inputs),
                        "min_change_pct": float(min_change),
                        "duration_seconds": duration_seconds,
                    },
                )
            else:
                logger.info(
                    "REBALANCE_APPLIED",
                    extra={
                        "rebalance_id": rebalance_id,
                        "run_id": run_id,
                        "actor": actor,
                        "changes_count": changes_count,
                        "churn_pct": float(total_delta),
                        "strategies_evaluated": len(inputs),
                        "duration_seconds": duration_seconds,
                        "lock_key": _REBALANCE_ADVISORY_LOCK_KEY,
                    },
                )

            self._audit(result=result, actor=actor, occurred_at=now)
            if not noop and bool(settings.notify_allocation_rebalance_events):
                self._emit_rebalance_notification(result=result, actor=actor, occurred_at=now)
            self._session.flush()
            self._session.commit()
            return result

        except Exception as exc:
            self._session.rollback()
            self._record_failed(
                run_id=run_id,
                rebalance_id=rebalance_id,
                actor=actor,
                trigger_source=trigger_source,
                now=now,
                exc=exc,
            )
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_quality_scores(
        self,
        *,
        inputs: list[StrategyQualityInput],
        rebalance_id: str,
        now: datetime,
    ) -> None:
        for item in inputs:
            record = StrategyQualityScoreHistory(
                score_id=str(uuid4()),
                strategy_id=item.strategy_id,
                rebalance_run_id=rebalance_id,
                quality_score=float(item.quality_score),
                live_score=float(item.live_score),
                backtest_score=float(item.backtest_score),
                blended_score=float(item.quality_score),
                alpha_weight=float(item.alpha_weight),
                score_components={
                    "sharpe": item.metrics.get("sharpe_ratio"),
                    "total_return": item.metrics.get("total_return"),
                    "win_rate": item.metrics.get("win_rate"),
                    "max_drawdown": item.metrics.get("max_drawdown"),
                    "trade_count": item.metrics.get("trade_count"),
                },
                data_window={
                    "days_live": item.live_metrics.get("days_live"),
                    "trade_count": item.live_metrics.get("trade_count"),
                },
                governance_state=item.governance_state,
                computed_at=now,
            )
            self._quality_score_repo.insert(record)

    def _record_skipped(
        self,
        *,
        run_id: str | None,
        rebalance_id: str,
        actor: str,
        trigger_source: str | None = None,
        settings: OperatorSettingsRow,
        skipped_reason: str,
        status: str,
        now: datetime,
    ) -> QualityReallocationResult:
        result = self._build_skipped_result(
            run_id=run_id,
            rebalance_id=rebalance_id,
            settings=settings,
            skipped_reason=skipped_reason,
        )
        history_row = AllocationRebalanceHistory(
            rebalance_id=rebalance_id,
            run_id=run_id,
            actor=actor,
            trigger_source=trigger_source,
            status=status,
            skipped_reason=skipped_reason,
            started_at=now,
            completed_at=now,
        )
        self._rebalance_history_repo.insert(history_row)
        self._audit(result=result, actor=actor, occurred_at=now)
        self._session.flush()
        self._session.commit()
        return result

    def _record_retry_detected(
        self,
        *,
        run_id: str | None,
        rebalance_id: str,
        existing: AllocationRebalanceHistory,
        actor: str,
        settings: OperatorSettingsRow,
        now: datetime,
    ) -> QualityReallocationResult:
        logger.info(
            "REBALANCE_RETRY_DETECTED",
            extra={
                "rebalance_id": rebalance_id,
                "run_id": run_id,
                "actor": actor,
                "existing_status": existing.status,
                "existing_started_at": existing.started_at.isoformat(),
            },
        )
        inputs = self._load_strategy_inputs(settings=settings, now=now)
        before = {item.strategy_id: item.current_allocation_pct for item in inputs}
        result = QualityReallocationResult(
            run_id=run_id,
            rebalance_id=rebalance_id,
            auto_rebalance_enabled=bool(settings.auto_rebalance_enabled),
            before_allocation=before,
            after_allocation=before,
            proposals=[],
            quality_metrics={item.strategy_id: item.metrics for item in inputs},
            active_policies=self._active_policy_payloads(),
            allocation_overrides=self._active_override_payloads(now=now),
            skipped_reason="retry_detected",
            audit_event_type="STRATEGY_ALLOCATION_REBALANCE_SKIPPED",
            turnover_metrics={},
            allocation_changes_count=0,
            noop=False,
            lock_acquired=False,
        )
        self._audit_log_repo.record_operator_action(
            action="STRATEGY_ALLOCATION_REBALANCE_RETRY_DETECTED",
            actor=actor,
            reason="rebalance retry detected: run already processed",
            occurred_at=now,
            component="allocations",
            metadata={
                "rebalance_id": rebalance_id,
                "run_id": run_id,
                "existing_rebalance_id": existing.rebalance_id,
                "existing_status": existing.status,
            },
        )
        self._session.flush()
        self._session.commit()
        return result

    def _record_failed(
        self,
        *,
        run_id: str | None,
        rebalance_id: str,
        actor: str,
        trigger_source: str | None,
        now: datetime,
        exc: BaseException,
    ) -> None:
        logger.error(
            "REBALANCE_FAILED",
            extra={
                "rebalance_id": rebalance_id,
                "run_id": run_id,
                "actor": actor,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        try:
            failed_row = AllocationRebalanceHistory(
                rebalance_id=str(uuid4()),
                run_id=run_id,
                actor=actor,
                trigger_source=trigger_source,
                status="failed",
                skipped_reason=type(exc).__name__,
                started_at=now,
                completed_at=datetime.now(UTC),
                result_summary_json={"original_rebalance_id": rebalance_id},
            )
            self._rebalance_history_repo.insert(failed_row)
            self._audit_log_repo.record_operator_action(
                action="STRATEGY_ALLOCATION_REBALANCE_FAILED",
                actor=actor,
                reason=f"rebalance failed: {type(exc).__name__}",
                occurred_at=datetime.now(UTC),
                component="allocations",
                metadata={
                    "rebalance_id": rebalance_id,
                    "run_id": run_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            self._session.flush()
            self._session.commit()
        except Exception:
            logger.exception(
                "REBALANCE_FAILED_AUDIT_ERROR",
                extra={"rebalance_id": rebalance_id},
            )

    @staticmethod
    def _try_acquire_advisory_lock(session: Session) -> bool:
        """Attempt a PostgreSQL transaction-scoped advisory lock.

        Returns True when acquired or when running against a non-PostgreSQL backend
        (advisory locks are not available in SQLite; the row-based guard handles that path).
        """
        try:
            dialect = session.bind.dialect.name  # type: ignore[union-attr]
        except Exception:
            return True
        if dialect != "postgresql":
            return True
        result = session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _REBALANCE_ADVISORY_LOCK_KEY},
        ).scalar()
        return bool(result)

    def _build_skipped_result(
        self,
        *,
        run_id: str | None,
        rebalance_id: str,
        settings: OperatorSettingsRow,
        skipped_reason: str,
    ) -> QualityReallocationResult:
        now = datetime.now(UTC)
        inputs = self._load_strategy_inputs(settings=settings, now=now)
        before = {item.strategy_id: item.current_allocation_pct for item in inputs}
        return QualityReallocationResult(
            run_id=run_id,
            rebalance_id=rebalance_id,
            auto_rebalance_enabled=bool(settings.auto_rebalance_enabled),
            before_allocation=before,
            after_allocation=before,
            proposals=[],
            quality_metrics={item.strategy_id: item.metrics for item in inputs},
            active_policies=self._active_policy_payloads(),
            allocation_overrides=self._active_override_payloads(now=now),
            skipped_reason=skipped_reason,
            audit_event_type="STRATEGY_ALLOCATION_REBALANCE_SKIPPED",
            turnover_metrics={},
            allocation_changes_count=0,
            noop=False,
        )

    def _load_strategy_inputs(
        self,
        *,
        settings: OperatorSettingsRow,
        now: datetime,
        rebalance_id: str | None = None,
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
        expired_overrides = self._expired_active_overrides_by_strategy(now=now)
        for strategy_id, expired_list in expired_overrides.items():
            for exp in expired_list:
                if exp.overridden_by != AUTO_REBALANCE_ACTOR:
                    logger.info(
                        "ALLOCATION_OVERRIDE_EXPIRED_IGNORED",
                        extra={
                            "strategy_id": strategy_id,
                            "override_id": exp.override_id,
                            "overridden_by": exp.overridden_by,
                            "expires_at": exp.expires_at.isoformat() if exp.expires_at else None,
                            "rebalance_run_id": rebalance_id,
                        },
                    )

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
            if manual_override_pct is not None:
                current_pct = manual_override_pct
            elif (
                override is not None
                and override.overridden_by == AUTO_REBALANCE_ACTOR
                and override.max_pct_of_capital is not None
            ):
                # Use the previous auto-rebalance value as the baseline so hysteresis
                # detects real drift from the last committed allocation.
                current_pct = self._decimal_pct(override.max_pct_of_capital)
            else:
                # No prior committed allocation: baseline is zero so any quality-
                # weighted assignment above the threshold is written on first run.
                current_pct = Decimal("0")
            metrics = self._latest_metrics(governance.strategy_id)
            backtest_score = self._quality_score(metrics)
            live_metrics_obj = self._live_perf_service.compute_for_strategy(governance.strategy_id)
            live_metrics = {
                "rolling_sharpe": live_metrics_obj.rolling_sharpe,
                "realized_return": live_metrics_obj.realized_return,
                "realized_drawdown": live_metrics_obj.realized_drawdown,
                "live_win_rate": live_metrics_obj.live_win_rate,
                "trade_count": live_metrics_obj.trade_count,
            }
            live_score = self._live_quality_score(live_metrics)
            alpha = Decimal(
                str(
                    round(
                        compute_alpha(
                            days_live=live_metrics_obj.days_live or 0,
                            trade_count=live_metrics_obj.trade_count or 0,
                        ),
                        6,
                    )
                )
            )
            blended_score = alpha * live_score + (Decimal("1") - alpha) * backtest_score
            blended_score = max(blended_score, Decimal("0.01"))
            logger.info(
                "QUALITY_SCORE_BLENDED",
                extra={
                    "strategy_id": governance.strategy_id,
                    "backtest_score": float(backtest_score),
                    "live_score": float(live_score),
                    "blended_score": float(blended_score),
                    "alpha_weight": float(alpha),
                    "days_live": live_metrics_obj.days_live,
                    "live_trade_count": live_metrics_obj.trade_count,
                    "rebalance_run_id": rebalance_id,
                },
            )
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
                    quality_score=blended_score,
                    metrics=metrics,
                    live_metrics=live_metrics,
                    live_score=live_score,
                    backtest_score=backtest_score,
                    alpha_weight=alpha,
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
        min_allocation_change_pct: Decimal,
        rebalance_id: str,
    ) -> tuple[int, Decimal]:
        """Write auto-rebalance overrides, skipping changes below the hysteresis threshold.

        Returns (changes_written, total_absolute_delta).
        """
        active = self._active_overrides_by_strategy(now=now)

        def _should_write(proposal: StrategyAllocationProposal) -> bool:
            existing = active.get(proposal.strategy_id)
            if existing is not None and existing.overridden_by != AUTO_REBALANCE_ACTOR:
                return False
            if proposal.manual_override_respected:
                return False
            delta = abs(proposal.after_pct - proposal.before_pct)
            return delta >= min_allocation_change_pct

        for proposal in proposals:
            if not _should_write(proposal):
                continue
            existing = active.get(proposal.strategy_id)
            if existing is not None:
                existing.is_active = False
        self._session.flush()

        changes_written = 0
        total_delta = Decimal("0")
        for proposal in proposals:
            if not _should_write(proposal):
                continue
            delta = abs(proposal.after_pct - proposal.before_pct)
            self._allocation_overrides_repo.create_override(
                AllocationOverrides(
                    override_id=str(uuid4()),
                    strategy_id=proposal.strategy_id,
                    overridden_by=actor,
                    override_reason=(
                        f"quality-based reallocation: {proposal.reason} [rebalance:{rebalance_id}]"
                    ),
                    max_pct_of_capital=float(proposal.after_pct),
                    max_position_size_usd=None,
                    max_drawdown_allowed=None,
                    is_active=True,
                    created_at=now,
                    expires_at=None,
                )
            )
            changes_written += 1
            total_delta += delta

        return changes_written, total_delta

    def _compute_turnover_metrics(
        self,
        proposals: list[StrategyAllocationProposal],
        min_allocation_change_pct: Decimal,
        settings: OperatorSettingsRow,
    ) -> dict[str, float | int]:
        variable_proposals = [p for p in proposals if not p.manual_override_respected]
        total_delta = sum(abs(p.after_pct - p.before_pct) for p in variable_proposals)
        strategies_changed = sum(
            1
            for p in variable_proposals
            if abs(p.after_pct - p.before_pct) >= min_allocation_change_pct
        )
        churn_pct = float(total_delta)

        metrics: dict[str, float | int] = {
            "total_allocation_delta_pct": churn_pct,
            "strategies_changed": strategies_changed,
            "strategies_evaluated": len(proposals),
            "churn_pct": churn_pct,
        }

        turnover_penalty_weight = getattr(settings, "turnover_penalty_weight", None)
        if turnover_penalty_weight is not None:
            metrics["turnover_penalty"] = float(turnover_penalty_weight) * churn_pct

        return metrics

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
            "rebalance_id": result.rebalance_id,
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
            "turnover_metrics": result.turnover_metrics,
            "allocation_changes_count": result.allocation_changes_count,
            "noop": result.noop,
            "lock_acquired": result.lock_acquired,
            "blended_score_breakdown": result.blended_score_breakdown,
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

    def _live_quality_score(self, live_metrics: dict[str, float | int | None]) -> Decimal:
        """Compute quality score from realized live/runtime metrics.

        Uses the same weighting formula as the backtest score so that the two
        are directly comparable when blended.
        """
        sharpe = Decimal(str(live_metrics.get("rolling_sharpe") or 0))
        total_return = Decimal(str(live_metrics.get("realized_return") or 0))
        drawdown = Decimal(str(live_metrics.get("realized_drawdown") or 0))
        win_rate = Decimal(str(live_metrics.get("live_win_rate") or 0))
        trade_count = Decimal(str(live_metrics.get("trade_count") or 0))

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
        """Return the most-recent non-expired active override keyed by strategy_id."""
        rows = self._allocation_overrides_repo.get_all_active_non_expired(now=now)
        result: dict[str, AllocationOverrides] = {}
        for row in rows:
            result.setdefault(row.strategy_id, row)
        return result

    def _expired_active_overrides_by_strategy(
        self, *, now: datetime
    ) -> dict[str, list[AllocationOverrides]]:
        """Return all expired-but-still-active overrides grouped by strategy_id."""
        result: dict[str, list[AllocationOverrides]] = {}
        for row in self._allocation_overrides_repo.get_all_expired_active(now=now):
            result.setdefault(row.strategy_id, []).append(row)
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
