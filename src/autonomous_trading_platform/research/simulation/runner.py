from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pandas as pd

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
    ResearchDatasetResolver,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowData,
    SimulationWindowLoader,
)


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
        simulation_run_repository: Any | None = None,
        strategy_config_repository: Any | None = None,
        manifest_service: Any | None = None,
    ) -> None:
        self.dataset_resolver = dataset_resolver
        self.window_loader = window_loader
        self.result_recorder = result_recorder
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

            trade_logs, equity_curve, per_bar_metrics = self._execute_simulation(
                run_id=run_id,
                request=request,
                window=window,
            )

            self.result_recorder.record_results(
                experiment_id=experiment_id,
                strategy_id=request.strategy_id,
                trade_logs=trade_logs,
                equity_curve=equity_curve,
                per_bar_metrics=per_bar_metrics,
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

    def _execute_simulation(
        self,
        *,
        run_id: UUID,
        request: SimulationRunRequest,
        window: SimulationWindowData,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Temporary placeholder.

        Replace this with your actual simulation engine once the portfolio,
        execution simulator, strategy evaluator, and metrics calculator exist.
        """

        trade_logs = pd.DataFrame(
            columns=[
                "run_id",
                "experiment_id",
                "strategy_id",
                "symbol",
                "timestamp",
                "side",
                "quantity",
                "price",
                "notional",
                "fees",
                "slippage",
            ]
        )

        equity_rows: list[dict[str, Any]] = []
        metric_rows: list[dict[str, Any]] = []

        cash = request.initial_cash

        for symbol, bars in window.bars_by_symbol.items():
            for bar in bars:
                equity_rows.append(
                    {
                        "run_id": str(run_id),
                        "strategy_id": request.strategy_id,
                        "timestamp": bar.timestamp,
                        "symbol": symbol,
                        "cash": cash,
                        "equity": cash,
                    }
                )

                metric_rows.append(
                    {
                        "run_id": str(run_id),
                        "strategy_id": request.strategy_id,
                        "timestamp": bar.timestamp,
                        "symbol": symbol,
                        "close": bar.close,
                    }
                )

        equity_curve = pd.DataFrame(equity_rows)
        per_bar_metrics = pd.DataFrame(metric_rows)

        return trade_logs, equity_curve, per_bar_metrics

    def _record_run_started(
        self,
        *,
        run_id: UUID,
        request: SimulationRunRequest,
        experiment_id: str,
        resolved_dataset_metadata: dict[str, Any],
    ) -> None:

        if self.strategy_config_repository is not None:
            # Later: upsert strategy config/config_hash here.
            pass

        if self.simulation_run_repository is not None:
            # Later: create simulation_runs row here.
            pass

        if self.manifest_service is not None:
            # Later: create/save research run_manifest here.
            pass

    def _record_run_completed(
        self,
        *,
        run_id: UUID,
        trade_count: int,
        equity_points: int,
        per_bar_metric_points: int,
    ) -> None:
        if self.simulation_run_repository is not None:
            # Later: mark simulation_run completed.
            pass

        if self.manifest_service is not None:
            # Later: mark manifest completed.
            pass

    def _record_run_failed(self, *, run_id: UUID, error_message: str) -> None:
        if self.simulation_run_repository is not None:
            # Later: mark simulation_run failed.
            pass

        if self.manifest_service is not None:
            # Later: mark manifest failed.
            pass
