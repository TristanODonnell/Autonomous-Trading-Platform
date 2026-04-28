from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.strategy_config import (
    StrategyConfig as RuntimeStrategyConfig,
)
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
    ResearchDatasetResolver,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simulation_execution_engine import (
    SimulationExecutionEngine,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowLoader,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory


@dataclass(slots=True)
class SimulationRunRequest:
    strategy_id: str
    strategy_config: dict[str, Any]
    dataset_version: str
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date
    initial_cash: float = 100_000.0
    experiment_id: str | None = None
    strict_data_loading: bool = True


@dataclass(slots=True)
class SimulationRunResult:
    run_id: UUID
    experiment_id: str
    strategy_id: str
    dataset_version: str
    symbols: list[str]
    start_date: date
    end_date: date
    trade_count: int
    equity_points: int
    per_bar_metric_points: int
    status: str


class SimulationRunner:
    """
    Research-layer orchestrator for simulation runs.

    Responsibilities:
    - resolve research datasets
    - load bounded simulation windows
    - initialize simulation metadata
    - call the simulation engine/strategy execution path
    - record output artifacts
    - return a compact run result

    This should not become a scheduler cycle. The scheduler can call this runner.
    """

    def __init__(
        self,
        *,
        dataset_resolver: ResearchDatasetResolver,
        window_loader: SimulationWindowLoader,
        result_recorder: ResultRecorderService,
        execution_engine: SimulationExecutionEngine,
        context_builder: StrategyContextBuilder,
        portfolio_construction_service: PortfolioConstructionService,
        simulated_execution_service: Any,
        simulation_run_repository: Any | None = None,
        strategy_config_repository: Any | None = None,
        manifest_service: Any | None = None,
        strategy_factory: StrategyFactory,
    ) -> None:
        self.strategy_factory = strategy_factory
        self.dataset_resolver = dataset_resolver
        self.window_loader = window_loader
        self.result_recorder = result_recorder
        self.execution_engine = execution_engine
        self.context_builder = context_builder
        self.portfolio_construction_service = portfolio_construction_service
        self.simulated_execution_service = simulated_execution_service
        self.simulation_run_repository = simulation_run_repository
        self.strategy_config_repository = strategy_config_repository
        self.manifest_service = manifest_service

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        run_id = uuid4()
        experiment_id = request.experiment_id or f"experiment_{run_id}"

        resolved_dataset = self.dataset_resolver.resolve_bars_dataset(
            dataset_version=request.dataset_version,
            price_basis=request.price_basis,
        )

        self._record_run_started(
            run_id=run_id,
            request=request,
            experiment_id=experiment_id,
            resolved_dataset_metadata=resolved_dataset.metadata,
        )

        try:
            window = self.window_loader.load_window(
                dataset_version=request.dataset_version,
                bars_dataset=resolved_dataset.dataset,
                symbols=request.symbols,
                start_date=request.start_date,
                end_date=request.end_date,
                strict=request.strict_data_loading,
            )

            strategy_config = StrategyConfig(
                strategy_id=request.strategy_id,
                type=request.strategy_config["type"],
                parameters=request.strategy_config.get("parameters", {}),
            )
            strategy = self.strategy_factory.build(strategy_config)

            trade_logs, equity_curve, per_bar_metrics, positions = self._execute_simulation(
                run_id=run_id,
                request=request,
                window=window,
                strategy=strategy,
            )

            self.result_recorder.record_results(
                experiment_id=experiment_id,
                strategy_id=request.strategy_id,
                trade_logs=trade_logs,
                equity_curve=equity_curve,
                per_bar_metrics=per_bar_metrics,
                positions=positions,
            )

            self._record_run_completed(
                run_id=run_id,
                trade_count=len(trade_logs),
                equity_points=len(equity_curve),
                per_bar_metric_points=len(per_bar_metrics),
            )

            return SimulationRunResult(
                run_id=run_id,
                experiment_id=experiment_id,
                strategy_id=request.strategy_id,
                dataset_version=request.dataset_version,
                symbols=window.symbols,
                start_date=request.start_date,
                end_date=request.end_date,
                trade_count=len(trade_logs),
                equity_points=len(equity_curve),
                per_bar_metric_points=len(per_bar_metrics),
                status="completed",
            )

        except Exception as exc:
            self._record_run_failed(run_id=run_id, error_message=str(exc))
            raise

    def _execute_simulation(self, *, run_id, request, window, strategy):
        result = self.execution_engine.execute(
            run_id=run_id,
            strategy=strategy,
            window=window,
            context_builder=self.context_builder,
            portfolio_construction_service=self.portfolio_construction_service,
            simulated_execution_service=self.simulated_execution_service,
            initial_cash=request.initial_cash,
        )
        return (
            result.trade_logs,
            result.equity_curve,
            result.per_bar_metrics,
            result.positions,
        )

    def _record_run_started(
        self,
        *,
        run_id: UUID,
        request: SimulationRunRequest,
        experiment_id: str,
        resolved_dataset_metadata: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC)

        if self.strategy_config_repository is not None:
            strategy_config = RuntimeStrategyConfig(
                strategy_id=request.strategy_id,
                config_hash=request.strategy_config.get("config_hash", request.strategy_id),
                config_json=request.strategy_config,
                created_at=now,
                strategy_type=request.strategy_config["type"],
                metadata_json={
                    "source": "simulation_runner",
                },
            )

            strategy_config_row = self.strategy_config_repository.to_row(strategy_config)
            self.strategy_config_repository.upsert(strategy_config_row)

        if self.strategy_config_repository is not None:
            build_config = StrategyConfig(
                strategy_id=request.strategy_id,
                type=request.strategy_config["type"],
                parameters=request.strategy_config.get("parameters", {}),
            )

            strategy_config = RuntimeStrategyConfig(
                strategy_id=build_config.strategy_id,
                config_hash=build_config.config_hash(),
                config_json=json.loads(build_config.canonical_json()),
                created_at=now,
                strategy_type=build_config.type,
                metadata_json={
                    "source": "simulation_runner",
                },
            )

            strategy_config_row = self.strategy_config_repository.to_row(strategy_config)
            self.strategy_config_repository.upsert(strategy_config_row)

        if self.manifest_service is not None:
            # Optional later: build and save RunManifest here.
            pass

    def _record_run_completed(
        self,
        *,
        run_id: UUID,
        trade_count: int,
        equity_points: int,
        per_bar_metric_points: int,
    ) -> None:
        now = datetime.now(UTC)

        if self.simulation_run_repository is not None:
            row = self.simulation_run_repository.get_by_run_id(str(run_id))

            if row is not None:
                row.status = "COMPLETED"
                row.end_time = now
                row.execution_config = {
                    **row.execution_config,
                    "result_summary": {
                        "trade_count": trade_count,
                        "equity_points": equity_points,
                        "per_bar_metric_points": per_bar_metric_points,
                    },
                }

        if self.manifest_service is not None:
            # Optional later: mark manifest completed.
            pass

    def _record_run_failed(self, *, run_id: UUID, error_message: str) -> None:
        now = datetime.now(UTC)

        if self.simulation_run_repository is not None:
            row = self.simulation_run_repository.get_by_run_id(str(run_id))

            if row is not None:
                row.status = "FAILED"
                row.end_time = now
                row.execution_config = {
                    **row.execution_config,
                    "error": error_message,
                }

        if self.manifest_service is not None:
            # Optional later: mark manifest failed.
            pass
