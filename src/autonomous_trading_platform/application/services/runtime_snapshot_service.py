from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.alpaca_portfolio_service import (
    AlpacaPortfolioService,
)
from autonomous_trading_platform.application.services.audit_log_service import AuditLogService
from autonomous_trading_platform.application.services.operator_settings_service import (
    OperatorSettingsService,
)
from autonomous_trading_platform.application.services.portfolio_analytics_service import (
    PortfolioAnalyticsService,
)
from autonomous_trading_platform.application.services.portfolio_summary_service import (
    PortfolioSummaryService,
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
    AggregateAllocationSnapshot,
    DatasetVersionEntry,
    ExperimentEntry,
    OperatorControlsSnapshot,
    OperatorSettingsSnapshot,
    PortfolioAllocationItem,
    PortfolioHoldingEntry,
    PortfolioPerformanceSnapshot,
    PortfolioPeriodReturn,
    PortfolioRiskSnapshot,
    PortfolioSnapshot,
    PortfolioSummarySnapshot,
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
_ALL_SECTIONS = frozenset(
    {"controls", "settings", "portfolio", "allocations", "datasets", "experiments", "activity"}
)


class RuntimeSnapshotService:
    def __init__(self, session: Session, *, local_only: bool = False) -> None:
        self._session = session
        self._local_only = local_only

    def capture(self, *, sections: frozenset[str] | None = None) -> RuntimeSnapshot:
        active = sections if sections is not None else _ALL_SECTIONS
        controls, strategy_controls = (
            self._capture_controls_and_strategies() if "controls" in active else (None, [])
        )
        return RuntimeSnapshot(
            snapshot_timestamp=datetime.now(UTC),
            operator_controls=controls,
            operator_settings=self._capture_settings() if "settings" in active else None,
            strategy_controls=strategy_controls,
            strategy_allocations=self._capture_allocations() if "allocations" in active else [],
            aggregate_allocation=(
                self._capture_aggregate_allocation() if "allocations" in active else None
            ),
            datasets=self._capture_datasets() if "datasets" in active else [],
            recent_activity=self._capture_recent_activity() if "activity" in active else [],
            experiments=self._capture_experiments() if "experiments" in active else [],
            portfolio=self._capture_portfolio() if "portfolio" in active else None,
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

    def _capture_aggregate_allocation(self) -> AggregateAllocationSnapshot | None:
        try:
            status = StrategyAllocationService(
                session=self._session
            ).get_aggregate_allocation_status()
            return AggregateAllocationSnapshot(
                aggregate_allocation_pct=Decimal(str(status["aggregate_allocation_pct"])),
                max_total_strategy_allocation_pct=Decimal(
                    str(status["max_total_strategy_allocation_pct"])
                ),
                remaining_allocation_capacity_pct=Decimal(
                    str(status["remaining_allocation_capacity_pct"])
                ),
                allocation_utilization=Decimal(str(status["allocation_utilization"])),
                allocation_budget_exceeded=bool(status["allocation_budget_exceeded"]),
            )
        except Exception:
            return None

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

    def _alpaca_portfolio_service(self) -> AlpacaPortfolioService | None:
        if self._local_only:
            return None
        try:
            from autonomous_trading_platform.config.settings import Settings
            from autonomous_trading_platform.execution.clients.alpaca_broker_client import (
                AlpacaBrokerClient,
            )

            settings = Settings()
            client = AlpacaBrokerClient(settings=settings)
            return AlpacaPortfolioService(client=client, initial_capital=settings.initial_capital)
        except Exception:
            return None

    def _capture_portfolio(self) -> PortfolioSnapshot | None:
        try:
            holdings = self._capture_portfolio_holdings()
            summary = self._capture_portfolio_summary()
            if summary is not None:
                summary = summary.model_copy(update={"open_positions": len(holdings)})
            return PortfolioSnapshot(
                summary=summary,
                holdings=holdings,
                allocation_by_strategy=self._capture_portfolio_allocation(),
                performance=self._capture_portfolio_performance(),
                risk=self._capture_portfolio_risk(),
            )
        except Exception:
            return None

    def _capture_portfolio_summary(self) -> PortfolioSummarySnapshot | None:
        try:
            alpaca = self._alpaca_portfolio_service()
            if alpaca is not None:
                raw = alpaca.get_summary()
            else:
                raw = PortfolioSummaryService(session=self._session).get_summary()

            total = raw["current_portfolio_value"]
            cash = raw["cash_balance"]
            invested = total - cash
            # holdings count filled in separately; set to 0 here
            return PortfolioSummarySnapshot(
                current_portfolio_value=total,
                cash_balance=cash,
                invested_capital=invested,
                open_positions=0,  # patched after holdings captured
                todays_pnl_amount=raw["todays_pnl_amount"],
                todays_pnl_percent=raw["todays_pnl_percent"],
                total_pnl_amount=raw["total_pnl_amount"],
                total_pnl_percent=raw["total_pnl_percent"],
            )
        except Exception:
            return None

    def _capture_portfolio_holdings(self) -> list[PortfolioHoldingEntry]:
        try:
            alpaca = self._alpaca_portfolio_service()
            if alpaca is not None:
                raw = alpaca.get_holdings()
            else:
                raw = PortfolioAnalyticsService(session=self._session).get_holdings()

            entries = []
            for h in raw["holdings"]:
                qty = Decimal(str(h["quantity"]))
                avg = Decimal(str(h["average_entry_price"]))
                mv = Decimal(str(h["market_value"]))
                entries.append(
                    PortfolioHoldingEntry(
                        symbol=h["symbol"],
                        quantity=qty,
                        average_entry_price=avg,
                        current_price=Decimal(str(h["current_price"])),
                        market_value=mv,
                        unrealized_pnl=mv - (avg * qty),
                        strategy_id=h["strategy_id"],
                    )
                )
            return entries
        except Exception:
            return []

    def _capture_portfolio_allocation(self) -> list[PortfolioAllocationItem]:
        try:
            alpaca = self._alpaca_portfolio_service()
            if alpaca is not None:
                raw = alpaca.get_allocation()
                if raw["by_strategy"]:
                    return [
                        PortfolioAllocationItem(
                            name=item["name"],
                            percent_of_portfolio=Decimal(str(item["percent_of_portfolio"])),
                            allocated_capital=Decimal(str(item["allocated_capital"])),
                        )
                        for item in raw["by_strategy"]
                    ]

            raw = PortfolioAnalyticsService(session=self._session).get_allocation()
            items = raw["by_strategy"]

            # No live positions — fall back to configured strategy allocation percentages
            if not items:
                rows = StrategyAllocationService(
                    session=self._session
                ).get_allocations_for_active_strategies()
                return [
                    PortfolioAllocationItem(
                        name=row["display_name"],
                        percent_of_portfolio=Decimal(str(row["allocation_pct"] or 0)),
                        allocated_capital=Decimal(str(row["allocated_capital"] or 0)),
                    )
                    for row in rows
                ]

            return [
                PortfolioAllocationItem(
                    name=item["name"],
                    percent_of_portfolio=Decimal(str(item["percent_of_portfolio"])),
                    allocated_capital=Decimal(str(item["allocated_capital"])),
                )
                for item in items
            ]
        except Exception:
            return []

    def _capture_portfolio_performance(self) -> PortfolioPerformanceSnapshot | None:
        try:
            svc = PortfolioAnalyticsService(session=self._session)
            perf = svc.get_performance()
            by_period_raw = svc.get_performance_by_period()
            return PortfolioPerformanceSnapshot(
                total_return=Decimal(str(perf["total_return"])),
                sharpe_ratio=Decimal(str(perf["sharpe_ratio"])),
                sortino_ratio=Decimal(str(perf["sortino_ratio"])),
                max_drawdown=Decimal(str(perf["max_drawdown"])),
                volatility=Decimal(str(perf["volatility"])),
                by_period=[
                    PortfolioPeriodReturn(
                        period=row["period"],
                        return_percent=Decimal(str(row["return_percent"])),
                    )
                    for row in by_period_raw["periods"]
                ],
            )
        except Exception:
            return None

    def _capture_portfolio_risk(self) -> PortfolioRiskSnapshot | None:
        try:
            raw = PortfolioAnalyticsService(session=self._session).get_risk()
            return PortfolioRiskSnapshot(
                portfolio_volatility=Decimal(str(raw["portfolio_volatility"])),
                beta=Decimal(str(raw["beta"])),
                value_at_risk_1d_95=Decimal(str(raw["value_at_risk_1d_95"])),
                current_drawdown=Decimal(str(raw["current_drawdown"])),
                average_pairwise_correlation=Decimal(str(raw["average_pairwise_correlation"])),
            )
        except Exception:
            return None

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
