from __future__ import annotations

import json
import platform
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pandas as pd

from autonomous_trading_platform.common.system_info import get_dependency_lock_hash, get_git_commit
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.metrics_summary import MetricsSummary
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.runtime.simulation_run import SimulationRun
from autonomous_trading_platform.contracts.runtime.strategy_config import (
    StrategyConfig as RuntimeStrategyConfig,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    ReturnMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    return_metrics as compute_return_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    RiskMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    risk_metrics as compute_risk_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.stability_metrics import (
    StabilityMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.stability_metrics import (
    stability_metrics as compute_stability_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    TradeMetrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    trade_metrics as compute_trade_metrics,
)
from autonomous_trading_platform.research.services.research_dataset_resolver_service import (
    ResearchDatasetResolver,
)
from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.research.simulation.services.feature_dependency_resolver_service import (
    FeatureDependencyResolverService,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.research.simulation.services.simulation_execution_engine import (
    SimulationExecutionEngine,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowLoader,
    shuffle_window_bar_timestamps,
)
from autonomous_trading_platform.storage.sor.repositories.core.experiments_repository import (
    ExperimentsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.metrics_summary_repository import (
    MetricsSummaryRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.simulation_runs_repository import (
    SimulationRunsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_configs_repository import (
    StrategyConfigsRepository,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory
from autonomous_trading_platform.strategy.registry.strategy_registry import get_registry


@dataclass(slots=True)
class SimulationRunRequest:
    strategy_id: str
    strategy_config: dict[str, Any]
    dataset_version: str
    random_seed: int
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date
    initial_cash: float = 100_000.0
    experiment_id: str | None = None
    strict_data_loading: bool = True
    shuffle_timestamp: bool = False
    window_role: str | None = None
    stage_name: str | None = None


@dataclass(slots=True)
class SimulationRunResult:
    run_id: UUID
    experiment_id: str | None
    strategy_id: str
    dataset_version: str
    symbols: list[str]
    start_date: date
    end_date: date
    trade_count: int
    equity_points: int
    per_bar_metric_points: int
    status: str
    return_metrics: ReturnMetrics
    risk_metrics: RiskMetrics
    trade_metrics: TradeMetrics
    stability_metrics: StabilityMetrics
    equity_curve: pd.DataFrame


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
        simulated_execution_service: Any,
        simulation_run_repository: SimulationRunsRepository,
        strategy_config_repository: StrategyConfigsRepository,
        experiment_repository: ExperimentsRepository | None = None,
        metrics_summary_repository: MetricsSummaryRepository,
        manifest_service: Any | None = None,
        strategy_factory: StrategyFactory,
        feature_dependency_resolver: FeatureDependencyResolverService | None = None,
    ) -> None:
        self.strategy_factory = strategy_factory
        self.dataset_resolver = dataset_resolver
        self.window_loader = window_loader
        self.result_recorder = result_recorder
        self.execution_engine = execution_engine
        self.context_builder = context_builder
        self.simulated_execution_service = simulated_execution_service
        self.simulation_run_repository = simulation_run_repository
        self.strategy_config_repository = strategy_config_repository
        self.manifest_service = manifest_service
        self.experiment_repository = experiment_repository
        self.metrics_summary_repository = metrics_summary_repository
        self.feature_dependency_resolver = feature_dependency_resolver

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        if not isinstance(request.random_seed, int):
            raise ValueError("random_seed must be an int for deterministic execution")

        self._set_seed(request.random_seed)

        run_id = uuid4()
        experiment_id = request.experiment_id or f"adhoc_{run_id}"

        artifact_identity = SimulationArtifactIdentity(
            run_id=str(run_id),
            experiment_id=experiment_id,
            strategy_id=request.strategy_id,
            dataset_version=request.dataset_version,
            stage_name=request.stage_name or "adhoc",
            window_role=request.window_role or "default",
            seed=request.random_seed,
            universe_version="v1",
            price_basis=request.price_basis.value,
            start_date=request.start_date,
            end_date=request.end_date,
        )

        resolved_dataset = self.dataset_resolver.resolve_bars_dataset(
            dataset_version=request.dataset_version,
            price_basis=request.price_basis,
        )

        # Resolve feature dependencies and registry-derived warmup before
        # persisting run metadata so resolved_feature_dataset_ids can be
        # recorded in execution_config from the start.
        strategy_type = request.strategy_config["type"]
        strategy_parameters = request.strategy_config.get("parameters", {})

        if self.feature_dependency_resolver is not None:
            resolved_deps = self.feature_dependency_resolver.resolve(
                strategy_type=strategy_type,
                strategy_parameters=strategy_parameters,
                simulation_dataset_version=request.dataset_version,
                price_basis=request.price_basis,
                symbols=request.symbols,
                start_date=request.start_date,
                end_date=request.end_date,
            )
            feature_requests = resolved_deps.feature_requests
            resolved_feature_dataset_ids = resolved_deps.resolved_feature_dataset_ids
            warmup_bars = resolved_deps.warmup_bars
        else:
            # Registry warmup replaces the former parameter-name heuristic
            # (_long_window * 78).  The registry warmup functions already return
            # bars directly; no day-to-bar conversion is needed here.
            registry = get_registry()
            defn = registry.get_definition(strategy_type)
            warmup_bars = defn.compute_warmup_bars(strategy_parameters)
            feature_requests = []
            resolved_feature_dataset_ids = {}

        self._record_run_started(
            run_id=run_id,
            request=request,
            experiment_id=experiment_id,
            resolved_dataset_metadata=resolved_dataset.metadata,
            resolved_feature_dataset_ids=resolved_feature_dataset_ids,
        )
        self._commit_metadata()

        try:
            window = self.window_loader.load_window(
                dataset_version=request.dataset_version,
                bars_dataset=resolved_dataset.dataset,
                symbols=request.symbols,
                start_date=request.start_date,
                end_date=request.end_date,
                feature_datasets=feature_requests or None,
                strict=request.strict_data_loading,
                warmup_bars=warmup_bars,
            )

            if request.shuffle_timestamp:
                window = shuffle_window_bar_timestamps(
                    window,
                    random_seed=request.random_seed,
                )

            strategy_config = StrategyConfig(
                strategy_id=request.strategy_id,
                type=request.strategy_config["type"],
                parameters=request.strategy_config.get("parameters", {}),
            )
            strategy = self.strategy_factory.build(strategy_config)

            trade_logs, equity_curve, per_bar_metrics, positions, signal_log = (
                self._execute_simulation(
                    run_id=run_id,
                    request=request,
                    window=window,
                    strategy=strategy,
                )
            )

            self.result_recorder.record_results(
                identity=artifact_identity,
                trade_logs=trade_logs,
                equity_curve=equity_curve,
                per_bar_metrics=per_bar_metrics,
                positions=positions,
                signal_log=signal_log,
            )

            # Strip warmup rows before computing metrics so indicators don't
            # skew duration, CAGR, Sharpe or drawdown calculations.
            live_equity_curve = equity_curve
            if window.warmup_timestamps and not equity_curve.empty:
                live_equity_curve = equity_curve[
                    ~equity_curve["timestamp"].isin(window.warmup_timestamps)
                ].reset_index(drop=True)

            rm = compute_return_metrics(live_equity_curve)
            risk = compute_risk_metrics(live_equity_curve)
            tm = compute_trade_metrics(trade_logs)
            sm = compute_stability_metrics(live_equity_curve)

            self._record_run_completed(
                run_id=run_id,
                artifact_identity=artifact_identity,
                trade_count=tm.total_trades,
                equity_points=len(live_equity_curve),
                per_bar_metric_points=len(per_bar_metrics),
                total_return=rm.total_return,
                sharpe_ratio=risk.sharpe_ratio,
                max_drawdown=risk.max_drawdown,
                volatility=risk.volatility,
                win_rate=tm.win_rate,
                consistency_score=sm.consistency_score,
            )
            self._commit_metadata()

            return SimulationRunResult(
                run_id=run_id,
                experiment_id=experiment_id,
                strategy_id=request.strategy_id,
                dataset_version=request.dataset_version,
                symbols=window.symbols,
                start_date=request.start_date,
                end_date=request.end_date,
                trade_count=len(trade_logs),
                equity_points=len(live_equity_curve),
                per_bar_metric_points=len(per_bar_metrics),
                status="completed",
                return_metrics=rm,
                risk_metrics=risk,
                trade_metrics=tm,
                stability_metrics=sm,
                equity_curve=live_equity_curve,
            )

        except Exception as exc:
            self._record_run_failed(run_id=run_id, error_message=str(exc))
            self._commit_metadata()
            raise

    def _execute_simulation(self, *, run_id, request, window, strategy):

        result = self.execution_engine.execute(
            run_id=run_id,
            strategy=strategy,
            window=window,
            context_builder=self.context_builder,
            simulated_execution_service=self.simulated_execution_service,
            initial_cash=request.initial_cash,
        )

        return (
            result.trade_logs,
            result.equity_curve,
            result.per_bar_metrics,
            result.positions,
            result.signal_log,
        )

    def _record_run_started(
        self,
        *,
        run_id: UUID,
        request: SimulationRunRequest,
        experiment_id: str,
        resolved_dataset_metadata: dict[str, Any],
        resolved_feature_dataset_ids: dict[str, str],
    ) -> None:
        now = datetime.now(UTC)
        if self.experiment_repository is not None:
            existing = self.experiment_repository.get_by_experiment_id(experiment_id)
            if existing is None:
                from autonomous_trading_platform.contracts.runtime.experiment import Experiment

                adhoc_experiment = Experiment(
                    experiment_id=experiment_id,
                    experiment_name=experiment_id,
                    created_at=now,
                    description="Auto-created adhoc experiment",
                    status="RUNNING",
                    metadata_json={
                        "dataset_version": request.dataset_version,
                        "price_basis": request.price_basis.value,
                        "symbols": request.symbols,
                        "start_date": str(request.start_date),
                        "end_date": str(request.end_date),
                        "random_seed": request.random_seed,
                        "initial_cash": request.initial_cash,
                    },
                )
                row = self.experiment_repository.to_row(adhoc_experiment)
                self.experiment_repository.upsert(row)
                self.experiment_repository.session.flush()

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
                metadata_json={"source": "simulation_runner"},
            )

            strategy_config_row = self.strategy_config_repository.to_row(strategy_config)
            self.strategy_config_repository.upsert(strategy_config_row)
            self.strategy_config_repository.session.flush()

        if self.simulation_run_repository is not None:
            simulation_run = SimulationRun(
                run_id=str(run_id),
                experiment_id=experiment_id,
                strategy_id=request.strategy_id,
                dataset_version=request.dataset_version,
                universe_version="v1",
                price_basis=request.price_basis,
                symbols=request.symbols,
                start_date=request.start_date,
                end_date=request.end_date,
                window_role=request.window_role,
                start_time=now,
                end_time=None,
                execution_config={
                    "random_seed": request.random_seed,
                    "price_basis": request.price_basis.value,
                    "symbols": request.symbols,
                    "start_date": str(request.start_date),
                    "end_date": str(request.end_date),
                    "initial_cash": request.initial_cash,
                    "strict_data_loading": request.strict_data_loading,
                    "stage_name": request.stage_name,
                    "resolved_dataset_metadata": resolved_dataset_metadata,
                    "resolved_feature_dataset_ids": resolved_feature_dataset_ids,
                },
                status="RUNNING",
                metrics_snapshot_id=None,
            )

            simulation_run_row = self.simulation_run_repository.to_row(simulation_run)
            self.simulation_run_repository.upsert(simulation_run_row)

        if self.manifest_service is not None:
            cost_model = (
                self.simulated_execution_service.cost_model_summary
                if self.simulated_execution_service is not None
                else None
            )
            fill_model = (
                self.simulated_execution_service.slippage_model_summary
                if self.simulated_execution_service is not None
                else None
            )

            manifest = RunManifest(
                run_id=run_id,
                run_type=RunType.SIMULATION,
                created_at=now,
                environment="local",
                broker="alpaca",
                broker_account_id="paper",
                strategy_id=request.strategy_id,
                strategy_version="v1",
                strategy_config=request.strategy_config,
                capital_bucket=Decimal(str(request.initial_cash)),
                interval=BarInterval.FIVE_MIN,
                start_date=request.start_date,
                end_date=request.end_date,
                dataset_version=request.dataset_version,
                price_basis=request.price_basis,
                universe_version="v1",
                cost_model=cost_model,
                fill_model=fill_model,
                random_seed=request.random_seed,
                git_commit=get_git_commit(),
                docker_image=None,
                python_version=platform.python_version(),
                dependency_lock_hash=get_dependency_lock_hash(),
                notes="Created by SimulationRunner",
                bar_timestamp=None,
                status="RUNNING",
                current_step="simulation_started",
                last_successful_step=None,
                error_message=None,
                artifact_manifest=None,
                schema_definition={
                    "determinism": {
                        "random_seed": request.random_seed,
                        "python_random_seeded": True,
                        "numpy_random_seeded": True,
                        "seed_scope": "simulation_run",
                    },
                    "simulation_request": {
                        "price_basis": request.price_basis.value,
                        "symbols": request.symbols,
                        "strict_data_loading": request.strict_data_loading,
                    },
                    "feature_dependencies": {
                        "resolved_feature_dataset_ids": resolved_feature_dataset_ids,
                    },
                },
                governance_state=GovernanceState.APPROVED_RESEARCH,
            )

            self.manifest_service.upsert(manifest)

    def _record_run_completed(
        self,
        *,
        run_id: UUID,
        artifact_identity: SimulationArtifactIdentity,
        trade_count: int,
        equity_points: int,
        per_bar_metric_points: int,
        total_return: float = 0.0,
        sharpe_ratio: float = 0.0,
        max_drawdown: float = 0.0,
        volatility: float = 0.0,
        win_rate: float = 0.0,
        consistency_score: float = 0.0,
    ) -> None:
        now = datetime.now(UTC)

        if self.simulation_run_repository is not None:
            row = self.simulation_run_repository.get_by_run_id(str(run_id))

            if row is not None:
                snapshot_id = str(uuid4())
                snapshot = MetricsSummary(
                    metrics_snapshot_id=snapshot_id,
                    run_id=str(run_id),
                    created_at=now,
                    trade_count=trade_count,
                    total_return=total_return,
                    sharpe_ratio=sharpe_ratio,
                    max_drawdown=max_drawdown,
                    volatility=volatility,
                    metrics_json={
                        "equity_points": equity_points,
                        "per_bar_metric_points": per_bar_metric_points,
                        "win_rate": win_rate,
                        "consistency_score": consistency_score,
                    },
                )
                snapshot_row = self.metrics_summary_repository.to_row(snapshot)
                self.metrics_summary_repository.upsert(snapshot_row)
                self.metrics_summary_repository.session.flush()

                # link FK back onto run
                row.metrics_snapshot_id = snapshot_id
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
            row = self.manifest_service.get_by_run_id(run_id)

            if row is not None:
                manifest = self.manifest_service.to_contract(row)
                manifest.status = "COMPLETED"
                manifest.current_step = "simulation_completed"
                manifest.last_successful_step = "record_results"
                manifest.error_message = None
                manifest.artifact_manifest = {
                    "identity": artifact_identity.to_manifest_dict(),
                    "partition_path": artifact_identity.partition_path(),
                    "summary": {
                        "trade_count": trade_count,
                        "equity_points": equity_points,
                        "per_bar_metric_points": per_bar_metric_points,
                    },
                }

                self.manifest_service.upsert(manifest)

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
            row = self.manifest_service.get_by_run_id(run_id)

            if row is not None:
                manifest = self.manifest_service.to_contract(row)
                manifest.status = "FAILED"
                manifest.current_step = "simulation_failed"
                manifest.error_message = error_message

                self.manifest_service.upsert(manifest)

    def _commit_metadata(self) -> None:
        repos = [
            self.simulation_run_repository,
            self.strategy_config_repository,
            self.experiment_repository,
            self.manifest_service,
        ]

        for repo in repos:
            if repo is not None:
                repo.session.commit()

    def _set_seed(self, seed: int) -> None:
        random.seed(seed)
        np.random.seed(seed)
