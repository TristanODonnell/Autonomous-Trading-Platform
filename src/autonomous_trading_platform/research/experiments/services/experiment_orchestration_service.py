from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from autonomous_trading_platform.contracts.runtime.experiment import Experiment
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreInput,
    FilterScoreOutput,
    FilterScoreService,
)
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.research.pipeline.pipeline_runner import (
    PipelineRunner,
    PipelineRunResult,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.research.strategy_generation.strategy_generation_engine import (
    StrategyGenerationEngine,
)
from autonomous_trading_platform.storage.sor.repositories.core.experiments_repository import (
    ExperimentsRepository,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)


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
        strategy_generation_engine: StrategyGenerationEngine,
        filter_score_service: FilterScoreService,
    ) -> None:
        self.experiment_repository = experiment_repository
        self.simulation_runner = simulation_runner
        self.strategy_generation_engine = strategy_generation_engine
        self.filter_score_service = filter_score_service

    def run_experiment(
        self, plan: ExperimentDefinition
    ) -> tuple[list[SimulationRunResult], list[FilterScoreOutput]]:
        self._create_experiment(plan)

        try:
            # MULTI STAGE PIPELINE CHECK
            if plan.staged_pipeline_config is not None:
                initial_configs = self._expand_strategy_configs(plan)
                pipeline = PipelineRunner(stages=plan.staged_pipeline_config.stages)
                result = pipeline.run(
                    initial_configs=initial_configs,
                    experiment_id=plan.experiment_id,
                    dataset_version=plan.dataset_version,
                    random_seed=plan.random_seed,
                    price_basis=plan.price_basis,
                    initial_cash=plan.initial_cash,
                )
                self._mark_experiment_completed(plan.experiment_id)
                return result.all_simulation_results, result.all_filter_outputs

            # SINGLE EXPERIMENT PASS BELOW
            strategy_configs = self._expand_strategy_configs(plan)
            windows = self._expand_windows(plan)
            all_results: list[SimulationRunResult] = []
            all_filter_outputs: list[FilterScoreOutput] = []

            for window in windows:
                window_results: list[SimulationRunResult] = []

                for config in strategy_configs:
                    request = SimulationRunRequest(
                        experiment_id=plan.experiment_id,
                        strategy_id=config.strategy_id,
                        strategy_config=config.model_dump(),
                        dataset_version=plan.dataset_version,
                        random_seed=plan.random_seed,
                        price_basis=plan.price_basis,
                        symbols=window.symbols,
                        start_date=window.start_date,
                        end_date=window.end_date,
                        initial_cash=plan.initial_cash,
                        window_role=window.window_role,
                    )
                    window_results.append(self.simulation_runner.run(request))

                inputs = [
                    FilterScoreInput(
                        strategy_id=result.strategy_id,
                        rm=result.return_metrics,
                        risk=result.risk_metrics,
                        tm=result.trade_metrics,
                        sm=result.stability_metrics,
                        equity_curve=result.equity_curve,
                    )
                    for result in window_results
                ]

                outputs, ranked = self.filter_score_service.filter_and_rank(inputs)
                all_results.extend(window_results)
                all_filter_outputs.extend(outputs)

                passed = [o for o in outputs if o.filter_result.passed]
                failed = [o for o in outputs if not o.filter_result.passed]

                logger.info(
                    "Window %s→%s | ran %d strategies | %d passed | %d failed",
                    window.start_date,
                    window.end_date,
                    len(window_results),
                    len(passed),
                    len(failed),
                )
                for o in failed:
                    logger.debug(
                        "  FAILED %s: %s", o.strategy_id, "; ".join(o.filter_result.failures)
                    )
                for o in passed:
                    if o.score is not None:
                        logger.debug(
                            "  PASSED %s | score=%.4f | sharpe=%.3f | return=%.3f | drawdown=%.3f",
                            o.strategy_id,
                            o.score.score,
                            o.score.sharpe_contrib,
                            o.score.return_contrib,
                            o.score.drawdown_contrib,
                        )

            self._mark_experiment_completed(plan.experiment_id)
            logger.info(
                "Experiment %s complete | total results=%d | total passed=%d",
                plan.experiment_id,
                len(all_results),
                len([o for o in all_filter_outputs if o.filter_result.passed]),
            )
            return all_results, all_filter_outputs

        except Exception:
            self._mark_experiment_failed(plan.experiment_id)
            raise

    # --- strategy expansion ---------------------------------------------------

    def _expand_strategy_configs(self, plan: ExperimentDefinition) -> list[StrategyConfig]:
        if plan.experiment_type == ExperimentType.AB:
            if len(plan.strategy_set) != 2:
                raise ValueError("AB experiment requires exactly 2 strategies in strategy_set")
            return [StrategyConfig(**s) for s in plan.strategy_set]

        if not plan.parameter_space:
            # No generation needed — strategy_set is already fully specified
            return [StrategyConfig(**s) for s in plan.strategy_set]

        # SWEEP or any type using the generation engine
        configs: list[StrategyConfig] = []
        for strategy in plan.strategy_set:
            generated = self.strategy_generation_engine.generate(
                strategy_type=strategy["type"],
                parameter_space=plan.parameter_space,
            )
            configs.extend(generated)
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
                "parameter_space": plan.parameter_space,
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

    def run_staged_experiment(self, plan: ExperimentDefinition) -> PipelineRunResult:
        self._create_experiment(plan)
        try:
            if plan.staged_pipeline_config is None:
                raise ValueError("run_staged_experiment requires staged_pipeline_config.")
            initial_configs = self._expand_strategy_configs(plan)
            pipeline = PipelineRunner(stages=plan.staged_pipeline_config.stages)
            result = pipeline.run(
                initial_configs=initial_configs,
                experiment_id=plan.experiment_id,
                dataset_version=plan.dataset_version,
                random_seed=plan.random_seed,
                price_basis=plan.price_basis,
                initial_cash=plan.initial_cash,
            )
            self._mark_experiment_completed(plan.experiment_id)
            return result
        except Exception:
            self._mark_experiment_failed(plan.experiment_id)
            raise
