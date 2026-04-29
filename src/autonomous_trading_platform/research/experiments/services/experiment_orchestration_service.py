from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from autonomous_trading_platform.contracts.runtime.experiment import Experiment
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentRunPlan,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.storage.sor.repositories.experiments_repository import (
    ExperimentsRepository,
)


class ExperimentOrchestrationService:
    def __init__(
        self,
        *,
        experiment_repository: ExperimentsRepository,
        simulation_runner: SimulationRunner,
    ) -> None:
        self.experiment_repository = experiment_repository
        self.simulation_runner = simulation_runner

    def run_experiment(self, plan: ExperimentRunPlan) -> list[SimulationRunResult]:
        self._create_experiment(plan)

        results: list[SimulationRunResult] = []

        try:
            for strategy_config in self._expand_strategy_configs(plan):
                request = SimulationRunRequest(
                    experiment_id=plan.experiment_id,
                    strategy_id=strategy_config["strategy_id"],
                    strategy_config=strategy_config,
                    dataset_version=plan.dataset_version,
                    random_seed=plan.random_seed,
                    price_basis=plan.price_basis,
                    symbols=plan.symbols,
                    start_date=plan.start_date,
                    end_date=plan.end_date,
                    initial_cash=plan.initial_cash,
                )

                results.append(self.simulation_runner.run(request))

            self._mark_experiment_completed(plan.experiment_id)
            return results

        except Exception:
            self._mark_experiment_failed(plan.experiment_id)
            raise

    def _create_experiment(self, plan: ExperimentRunPlan) -> None:
        experiment = Experiment(
            experiment_id=plan.experiment_id,
            experiment_name=plan.experiment_id,
            created_at=datetime.now(UTC),
            description=plan.description,
            status="RUNNING",
            metadata_json={
                "dataset_version": plan.dataset_version,
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

    def _expand_strategy_configs(self, plan: ExperimentRunPlan) -> list[dict[str, Any]]:
        configs: list[dict[str, Any]] = []

        if not plan.parameter_grid:
            return plan.strategy_set

        for strategy in plan.strategy_set:
            for params in plan.parameter_grid:
                merged_params = {
                    **strategy.get("parameters", {}),
                    **params,
                }

                configs.append(
                    {
                        **strategy,
                        "parameters": merged_params,
                    }
                )

        return configs

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
