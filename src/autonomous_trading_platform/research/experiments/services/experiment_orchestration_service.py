from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from autonomous_trading_platform.contracts.runtime.experiment import Experiment
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.storage.sor.repositories.experiments_repository import (
    ExperimentsRepository,
)


@dataclass
class _Window:
    start_date: date
    end_date: date
    symbols: list[str]
    window_role: str | None = None  # "train" | "test" | "fold_N" | None


class ExperimentOrchestrationService:
    def __init__(
        self,
        *,
        experiment_repository: ExperimentsRepository,
        simulation_runner: SimulationRunner,
    ) -> None:
        self.experiment_repository = experiment_repository
        self.simulation_runner = simulation_runner

    def run_experiment(self, plan: ExperimentDefinition) -> list[SimulationRunResult]:
        self._create_experiment(plan)
        results: list[SimulationRunResult] = []

        try:
            strategy_configs = self._expand_strategy_configs(plan)
            windows = self._expand_windows(plan)

            for window in windows:
                for strategy_config in strategy_configs:
                    request = SimulationRunRequest(
                        experiment_id=plan.experiment_id,
                        strategy_id=strategy_config["strategy_id"],
                        strategy_config=strategy_config,
                        dataset_version=plan.dataset_version,
                        random_seed=plan.random_seed,
                        price_basis=plan.price_basis,
                        symbols=window.symbols,
                        start_date=window.start_date,
                        end_date=window.end_date,
                        initial_cash=plan.initial_cash,
                        window_role=window.window_role,
                    )
                    results.append(self.simulation_runner.run(request))

            self._mark_experiment_completed(plan.experiment_id)
            return results

        except Exception:
            self._mark_experiment_failed(plan.experiment_id)
            raise

    # --- strategy expansion ---------------------------------------------------

    def _expand_strategy_configs(self, plan: ExperimentDefinition) -> list[dict[str, Any]]:
        if plan.experiment_type == ExperimentType.AB:
            if len(plan.strategy_set) != 2:
                raise ValueError("AB experiment requires exactly 2 strategies in strategy_set")
            return plan.strategy_set

        if not plan.parameter_grid:
            return plan.strategy_set

        # SWEEP (and any other type that uses a param grid)
        configs: list[dict[str, Any]] = []
        for strategy in plan.strategy_set:
            for params in plan.parameter_grid:
                merged = {**strategy.get("parameters", {}), **params}
                configs.append({**strategy, "parameters": merged})
        return configs

    # --- window expansion -----------------------------------------------------

    def _expand_windows(self, plan: ExperimentDefinition) -> list[_Window]:
        match plan.experiment_type:
            case ExperimentType.TIME_SEGMENTATION:
                return self._time_segmentation_windows(plan)
            case ExperimentType.ROLLING_WINDOW:
                return self._rolling_windows(plan)
            case ExperimentType.CROSS_UNIVERSE:
                return self._cross_universe_windows(plan)
            case _:
                # AB and SWEEP: single window, plan.symbols
                return [_Window(plan.start_date, plan.end_date, plan.symbols)]

    def _time_segmentation_windows(self, plan: ExperimentDefinition) -> list[_Window]:
        if plan.train_ratio is None:
            raise ValueError("TIME_SEGMENTATION requires train_ratio")
        if not (0.0 < plan.train_ratio < 1.0):
            raise ValueError("train_ratio must be between 0 and 1 exclusive")

        total_days = (plan.end_date - plan.start_date).days
        split_day = round(total_days * plan.train_ratio)
        split_date = plan.start_date + timedelta(days=split_day)

        return [
            _Window(plan.start_date, split_date, plan.symbols, window_role="train"),
            _Window(split_date, plan.end_date, plan.symbols, window_role="test"),
        ]

    def _rolling_windows(self, plan: ExperimentDefinition) -> list[_Window]:
        if plan.window_size_days is None or plan.step_size_days is None:
            raise ValueError("ROLLING_WINDOW requires window_size_days and step_size_days")

        windows: list[_Window] = []
        window_start = plan.start_date
        fold = 0

        while True:
            window_end = window_start + timedelta(days=plan.window_size_days)
            if window_end > plan.end_date:
                break
            windows.append(
                _Window(window_start, window_end, plan.symbols, window_role=f"fold_{fold}")
            )
            window_start += timedelta(days=plan.step_size_days)
            fold += 1

        if not windows:
            raise ValueError(
                "ROLLING_WINDOW produced no windows — "
                "window_size_days may be larger than the total date range"
            )

        return windows

    def _cross_universe_windows(self, plan: ExperimentDefinition) -> list[_Window]:
        if not plan.universe_set:
            raise ValueError("CROSS_UNIVERSE requires universe_set")

        return [_Window(plan.start_date, plan.end_date, symbols) for symbols in plan.universe_set]

    # --- status helpers -------------------------------------------------------

    def _create_experiment(self, plan: ExperimentDefinition) -> None:
        experiment = Experiment(
            experiment_id=plan.experiment_id,
            experiment_name=plan.experiment_id,
            created_at=datetime.now(UTC),
            description=plan.description,
            status="RUNNING",
            metadata_json={
                "experiment_type": plan.experiment_type,
                "dataset_version": plan.dataset_version,
                "universe_version": plan.universe_version,
                "price_basis": plan.price_basis.value,
                "symbols": plan.symbols,
                "start_date": str(plan.start_date),
                "end_date": str(plan.end_date),
                "random_seed": plan.random_seed,
                "initial_cash": plan.initial_cash,
                "strategy_set": plan.strategy_set,
                "parameter_grid": plan.parameter_grid,
            },
        )
        row = self.experiment_repository.to_row(experiment)
        self.experiment_repository.upsert(row)
        self.experiment_repository.session.commit()

    def _mark_experiment_completed(self, experiment_id: str) -> None:
        row = self.experiment_repository.get_by_experiment_id(experiment_id)
        if row is None:
            return
        row.status = "COMPLETED"
        self.experiment_repository.session.commit()

    def _mark_experiment_failed(self, experiment_id: str) -> None:
        row = self.experiment_repository.get_by_experiment_id(experiment_id)
        if row is None:
            return
        row.status = "FAILED"
        self.experiment_repository.session.commit()
