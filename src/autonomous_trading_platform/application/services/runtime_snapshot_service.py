from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.audit_log_service import AuditLogService
from autonomous_trading_platform.application.services.operator_settings_service import (
    OperatorSettingsService,
)
from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.application.services.strategy_catalog_service import (
    ExperimentCatalogService,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.runtime_snapshot import (
    DatasetVersionEntry,
    ExperimentEntry,
    OperatorControlsSnapshot,
    OperatorSettingsSnapshot,
    RecentActivityEntry,
    RuntimeSnapshot,
    StrategyAllocationEntry,
    StrategyControlEntry,
)
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

_KNOWN_DATASETS = ["raw_bars", "adjusted_bars", "features"]


class RuntimeSnapshotService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def capture(self) -> RuntimeSnapshot:
        controls, strategy_controls = self._capture_controls_and_strategies()
        return RuntimeSnapshot(
            snapshot_timestamp=datetime.now(UTC),
            operator_controls=controls,
            operator_settings=self._capture_settings(),
            strategy_controls=strategy_controls,
            strategy_allocations=self._capture_allocations(),
            datasets=self._capture_datasets(),
            recent_activity=self._capture_recent_activity(),
            experiments=self._capture_experiments(),
        )

    def _capture_controls_and_strategies(
        self,
    ) -> tuple[OperatorControlsSnapshot | None, list[StrategyControlEntry]]:
        try:
            svc = RuntimeControlService(session=self._session)
            state = svc.get_controls_state()
            controls = OperatorControlsSnapshot(
                trading_enabled=state.trading_enabled,
                trading_paused=state.trading_paused,
                kill_switch_active=state.kill_switch_active,
                trading_mode=state.trading_mode,
            )
            strategy_controls = [
                StrategyControlEntry(
                    strategy_id=s.strategy_id,
                    enabled=s.enabled,
                    pause_reason=s.reason,
                )
                for s in state.strategies
            ]
            return controls, strategy_controls
        except Exception:
            return None, []

    def _capture_settings(self) -> OperatorSettingsSnapshot | None:
        try:
            svc = OperatorSettingsService(
                settings_repo=OperatorSettingsRepository(self._session),
                audit_log_repo=AuditLogRepository(self._session),
            )
            s = svc.get_settings()
            return OperatorSettingsSnapshot(
                max_portfolio_drawdown=s.max_drawdown_limit,
                max_strategy_drawdown=s.max_strategy_drawdown,
                risk_tolerance=s.risk_tolerance,
                per_strategy_cap=s.per_strategy_cap,
                target_portfolio_volatility=s.target_portfolio_volatility,
                min_sharpe_for_promotion=s.min_sharpe_for_promotion,
                min_paper_trading_period_days=s.min_paper_trading_period_days,
                rebalance_frequency=s.rebalance_frequency,
                auto_demote_on_breach=s.auto_demote_on_breach,
                auto_promote_enabled=s.auto_promote_enabled,
                slippage_model=s.slippage_model,
                transaction_cost_model=s.transaction_cost_model,
                notify_drawdown_alerts=s.notify_drawdown_alerts,
                notify_strategy_promotion_events=s.notify_strategy_promotion_events,
                notify_pipeline_failures=s.notify_pipeline_failures,
            )
        except Exception:
            return None

    def _capture_allocations(self) -> list[StrategyAllocationEntry]:
        try:
            svc = StrategyAllocationService(session=self._session)
            rows = svc.get_allocations_for_active_strategies()
            return [
                StrategyAllocationEntry(
                    strategy_id=row["strategy_id"],
                    override_active=row["is_overridden"],
                    override_amount=row["allocated_capital"],
                    override_reason=row["reason"],
                    total_portfolio_capital=row["total_portfolio_capital"],
                )
                for row in rows
            ]
        except Exception:
            return []

    def _capture_datasets(self) -> list[DatasetVersionEntry]:
        try:
            repo = DatasetVersionsRepository(self._session)
            results = []
            for name in _KNOWN_DATASETS:
                row = None
                for basis in (PriceBasis.ADJUSTED, PriceBasis.RAW):
                    row = repo.get_latest_validated(dataset_name=name, price_basis=basis)
                    if row:
                        break
                results.append(
                    DatasetVersionEntry(
                        dataset_name=name,
                        version_id=row.dataset_version_id if row else None,
                    )
                )

            # Feature dataset versions live in a separate table
            feature_row = self._session.scalars(
                select(FeatureDatasetVersions)
                .order_by(FeatureDatasetVersions.created_at.desc())
                .limit(1)
            ).first()
            results.append(
                DatasetVersionEntry(
                    dataset_name="feature_version",
                    version_id=feature_row.dataset_version_id if feature_row else None,
                )
            )

            return results
        except Exception:
            return []

    def _capture_recent_activity(self) -> list[RecentActivityEntry]:
        try:
            svc = AuditLogService(session=self._session)
            result = svc.list_events(page=1, page_size=10)
            return [
                RecentActivityEntry(
                    timestamp=event.timestamp,
                    action_type=event.action_type,
                    actor=event.user,
                    details=event.description,
                )
                for event in result.events
            ]
        except Exception:
            return []

    def _capture_experiments(self) -> list[ExperimentEntry]:
        try:
            svc = ExperimentCatalogService(session=self._session)
            rows = svc.list_experiments()
            return [
                ExperimentEntry(
                    experiment_id=row["experiment_id"],
                    experiment_name=row["experiment_name"],
                    status=row["status"],
                    created_at=row["created_at"],
                    total_strategies=row["total_strategies"],
                    strategies_passed_filters=row["strategies_passed_filters"],
                )
                for row in rows[:10]
            ]
        except Exception:
            return []
