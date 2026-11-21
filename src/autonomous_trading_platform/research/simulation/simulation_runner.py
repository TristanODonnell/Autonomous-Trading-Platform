from __future__ import annotations

import json
import platform
import random
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pandas as pd

from autonomous_trading_platform.accounting.corporate_actions import (
    CorporateActionSource,
    StaticSplitSource,
    dividend_events_from,
    is_split,
)
from autonomous_trading_platform.common.system_info import get_dependency_lock_hash, get_git_commit
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
)
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.runtime.metrics_summary import MetricsSummary
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.contracts.runtime.simulation_run import SimulationRun
from autonomous_trading_platform.contracts.runtime.strategy_config import (
    StrategyConfig as RuntimeStrategyConfig,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import UniverseTimeline
from autonomous_trading_platform.contracts.simulation.dividend_event import DividendEvent
from autonomous_trading_platform.execution.services.sleeve_sizing import VOL_LOOKBACK_BARS
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.logging import get_logger
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

logger = get_logger(__name__)

# Stable namespace for deterministic run_id generation (P-01).
_RUN_NS = uuid5(NAMESPACE_URL, "autonomous-trading-platform:run")

# Corporate actions are loaded this far before a run's window so the warmup history
# the strategy reads is split-adjusted too (the longest registry warmup is ~1 year).
CORPORATE_ACTION_HISTORY_DAYS = 400


def corporate_actions_for_request(
    source: CorporateActionSource | None, request: SimulationRunRequest
) -> list[CorporateAction]:
    """The run's corporate actions: the request's own list when given, else the
    source's stored actions for the symbols from ``CORPORATE_ACTION_HISTORY_DAYS``
    before the window start to its end."""
    if request.corporate_actions is not None:
        return list(request.corporate_actions)
    if source is None:
        return []
    return source.actions_for(
        symbols=request.symbols,
        start_date=request.start_date - timedelta(days=CORPORATE_ACTION_HISTORY_DAYS),
        end_date=request.end_date,
    )


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
    resample_to_daily: bool = False
    # Scales slippage + commission for execution-cost stress runs. 1.0 = normal.
    cost_multiplier: float = 1.0
    # Settlement delay in trading bars (0 = immediate) and the child-order policy,
    # forwarded to the engine (plan 5d, D7).
    settlement_days: int = 0
    execution_policy_config: ExecutionPolicyConfig | None = None
    # Corporate actions for the run. None = load the stored splits and cash dividends
    # for the symbols from the runner's source (the SOR in production); a list (even
    # empty) is used as given. Splits adjust the strategy's history on read and the
    # held positions on the ex-date; dividends become the engine's dividend events
    # unless ``dividend_events`` is given explicitly.
    corporate_actions: list[CorporateAction] | None = None
    dividend_events: list[DividendEvent] | None = None
    # The exported universe timeline the run trades on (a dataset version id). With
    # one, the universe on each bar is the list in force at the bar's time, and the
    # run loads bars for the names of the lists in force during its own dates;
    # ``symbols`` must cover those names. None = ``symbols`` is the universe for
    # the whole run.
    universe_timeline_version: str | None = None
    # False = the run leaves no row behind: no simulation_runs, metrics_summary or
    # run-manifest row, and no experiment or strategy-config row made for it. Its
    # Parquet artifacts are written as usual. For a run whose numbers must not be
    # read as the strategy's research record: promotion, demotion, allocation and
    # the catalog take the newest run or metrics row of a strategy (the holdout
    # report, plan R2).
    record_run: bool = True


# The universe label of a run whose symbols are its universe for the whole run.
FIXED_UNIVERSE_VERSION = "v1"


def universe_version_for(request: SimulationRunRequest) -> str:
    """The universe a run is recorded and cached under: the id of its universe
    timeline when it trades on one, the fixed label otherwise. Two runs on the same
    names and dates under different universes must not share an identity."""
    return request.universe_timeline_version or FIXED_UNIVERSE_VERSION


@dataclass(slots=True)
class SimulationRunResult:
    run_id: UUID
    experiment_id: str | None
    strategy_id: str
    dataset_version: str
    random_seed: int
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
    # Fills of the run (one row per fill); used to slice trade metrics by window.
    trade_logs: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Symbols whose history before start_date was shorter than the warmup the run
    # asked for, with the number of bars missing (plan R17). The run goes ahead;
    # those symbols give no signal until the bars have accumulated. Empty when
    # every symbol was warmed up.
    warmup_shortfall_by_symbol: dict[str, int] = field(default_factory=dict)
    # The universe timeline the run traded on; None when it had a fixed list.
    universe_timeline_version: str | None = None
    # Sales ordered because the held name was no longer in the universe. None when
    # the run had a fixed list, where no such sale exists.
    universe_exit_count: int | None = None


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
        corporate_action_source: CorporateActionSource | None = None,
        universe_timeline_source: Callable[[str], UniverseTimeline] | None = None,
    ) -> None:
        self.strategy_factory = strategy_factory
        # Reads an exported universe timeline by its version id. Each timeline is
        # read once and kept: a pipeline asks for the same one for every run.
        self.universe_timeline_source = universe_timeline_source
        self._universe_timelines: dict[str, UniverseTimeline] = {}
        self.corporate_action_source = corporate_action_source
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

    def reuse_loaded_bars(self) -> AbstractContextManager[None]:
        """Around one pipeline run: its simulations share the bars they load
        (see ``SimulationWindowLoader.reuse_bars``)."""
        return self.window_loader.reuse_bars()

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        if not isinstance(request.random_seed, int):
            raise ValueError("random_seed must be an int for deterministic execution")

        # P-01: derive a stable run_id from the simulation inputs so that identical
        # reruns produce the same run_id (and therefore the same fill / intent IDs).
        run_id = self._derive_run_id(request)
        experiment_id = request.experiment_id or f"adhoc_{run_id}"

        # P-02: create an isolated, seeded RNG instance rather than mutating global
        # random / numpy state. Inject it into the execution service so all stochastic
        # decisions within this run are reproducible and isolated from other runs.
        py_rng = self._init_rng(request.random_seed)
        if hasattr(self.simulated_execution_service, "reset_for_run"):
            self.simulated_execution_service.reset_for_run(
                rng=py_rng, cost_multiplier=request.cost_multiplier
            )

        artifact_identity = SimulationArtifactIdentity(
            run_id=str(run_id),
            experiment_id=experiment_id,
            strategy_id=request.strategy_id,
            dataset_version=request.dataset_version,
            stage_name=request.stage_name or "adhoc",
            window_role=request.window_role or "default",
            seed=request.random_seed,
            universe_version=universe_version_for(request),
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
        # The strategy sees exactly its registry warmup in bars, as in the trading cycle
        # (_instantiate_strategy): same bars in, same signals out. Path-dependent
        # indicators (EMA, Wilder RSI) need the same count, not just enough bars.
        lookback_bars = (
            get_registry().get_definition(strategy_type).context_lookback_bars(strategy_parameters)
        )

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
            warmup_bars = max(resolved_deps.warmup_bars, VOL_LOOKBACK_BARS)
        else:
            # When resampling to daily bars, skip intraday warmup entirely: the first
            # lookback_bars daily bars of the window serve as warmup. Otherwise load at
            # least the sizer's volatility closes too (the trading cycle reads them from
            # history); the strategy context still gets exactly lookback_bars.
            warmup_bars = 0 if request.resample_to_daily else max(lookback_bars, VOL_LOOKBACK_BARS)
            feature_requests = []
            resolved_feature_dataset_ids = {}

        if request.record_run:
            self._record_run_started(
                run_id=run_id,
                request=request,
                experiment_id=experiment_id,
                resolved_dataset_metadata=resolved_dataset.metadata,
                resolved_feature_dataset_ids=resolved_feature_dataset_ids,
            )
            self._commit_metadata()

        try:
            universe_timeline, load_symbols = self._resolve_universe_timeline(request)
            window = self.window_loader.load_window(
                dataset_version=request.dataset_version,
                bars_dataset=resolved_dataset.dataset,
                symbols=load_symbols,
                start_date=request.start_date,
                end_date=request.end_date,
                feature_datasets=feature_requests or None,
                strict=request.strict_data_loading,
                warmup_bars=warmup_bars,
                resample_to_daily=request.resample_to_daily,
            )

            if window.warmup_shortfall_by_symbol:
                # Recorded, not refused (the user's decision, plan R17): the run
                # is still evidence, and its result says which names began cold.
                logger.warning(
                    "simulation.warmup_short",
                    extra={
                        "run_id": str(run_id),
                        "strategy_id": request.strategy_id,
                        "start_date": request.start_date.isoformat(),
                        "warmup_bars": warmup_bars,
                        "bars_missing_by_symbol": dict(window.warmup_shortfall_by_symbol),
                    },
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

            corporate_actions = corporate_actions_for_request(self.corporate_action_source, request)

            (
                trade_logs,
                equity_curve,
                per_bar_metrics,
                positions,
                signal_log,
                universe_exit_count,
            ) = self._execute_simulation(
                run_id=run_id,
                request=request,
                window=window,
                strategy=strategy,
                lookback_bars=lookback_bars,
                corporate_actions=corporate_actions,
                universe_timeline=universe_timeline,
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
            tm = compute_trade_metrics(trade_logs, live_equity_curve)
            sm = compute_stability_metrics(live_equity_curve)

            if request.record_run:
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
                    universe_exit_count=universe_exit_count,
                )
                self._commit_metadata()

            return SimulationRunResult(
                run_id=run_id,
                experiment_id=experiment_id,
                strategy_id=request.strategy_id,
                dataset_version=request.dataset_version,
                random_seed=request.random_seed,
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
                trade_logs=trade_logs,
                warmup_shortfall_by_symbol=dict(window.warmup_shortfall_by_symbol),
                universe_timeline_version=request.universe_timeline_version,
                universe_exit_count=universe_exit_count,
            )

        except Exception as exc:
            if request.record_run:
                self._record_run_failed(run_id=run_id, error_message=str(exc))
                self._commit_metadata()
            raise

    def _resolve_universe_timeline(
        self, request: SimulationRunRequest
    ) -> tuple[UniverseTimeline | None, list[str]]:
        """The timeline a request names, and the symbols to load bars for.

        With no timeline the request's own symbols are loaded, as before. With one,
        the names loaded are those of the lists in force during the run's dates: a
        name that is a member only before or after the run is not loaded, so it
        cannot fail the run for having no bars in it, and a walk-forward fold loads
        the names of its own months.
        """
        version_id = request.universe_timeline_version
        if version_id is None:
            return None, request.symbols
        if request.shuffle_timestamp:
            raise ValueError(
                "a run on a universe timeline cannot shuffle bar timestamps: the list "
                "in force is looked up by a bar's time"
            )
        timeline = self._universe_timelines.get(version_id)
        if timeline is None:
            if self.universe_timeline_source is None:
                raise ValueError(
                    f"the request names universe timeline {version_id}, but this "
                    "runner was built without a universe timeline source"
                )
            timeline = self.universe_timeline_source(version_id)
            self._universe_timelines[version_id] = timeline

        names = timeline.symbols_on_dates(request.start_date, request.end_date)
        if not names:
            raise ValueError(
                f"universe timeline {version_id} has no list in force between "
                f"{request.start_date} and {request.end_date}"
            )
        requested = {symbol.upper() for symbol in request.symbols}
        missing = sorted(name for name in names if name.upper() not in requested)
        if missing:
            raise ValueError(
                f"universe timeline {version_id}: the lists in force between "
                f"{request.start_date} and {request.end_date} name symbols the request "
                f"does not: {missing}"
            )
        return timeline, names

    def _execute_simulation(
        self,
        *,
        run_id,
        request,
        window,
        strategy,
        lookback_bars,
        corporate_actions: list[CorporateAction] | None = None,
        universe_timeline: UniverseTimeline | None = None,
    ):
        actions = list(corporate_actions or [])
        context_builder = self.context_builder.with_lookback(lookback_bars)
        if actions:
            # History the strategy sees is split-adjusted on read (plan 5d, D5), from
            # every loaded split — including those before the window, for warmup bars.
            context_builder = context_builder.with_split_source(StaticSplitSource(actions))
        in_window = [
            a for a in actions if request.start_date <= a.effective_date <= request.end_date
        ]
        dividend_events = (
            list(request.dividend_events)
            if request.dividend_events is not None
            else dividend_events_from(in_window)
        )

        result = self.execution_engine.execute(
            run_id=run_id,
            strategy=strategy,
            window=window,
            context_builder=context_builder,
            simulated_execution_service=self.simulated_execution_service,
            initial_cash=request.initial_cash,
            execution_policy_config=request.execution_policy_config,
            settlement_days=request.settlement_days,
            dividend_events=dividend_events,
            corporate_actions=[a for a in in_window if is_split(a)],
            universe_timeline=universe_timeline,
        )

        return (
            result.trade_logs,
            result.equity_curve,
            result.per_bar_metrics,
            result.positions,
            result.signal_log,
            # Counted only on a timeline: a fixed list has no such sale to count.
            len(result.universe_exit_log) if universe_timeline is not None else None,
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
                universe_version=universe_version_for(request),
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
                    # Present only for a run on a universe timeline.
                    **(
                        {"universe_timeline_version": request.universe_timeline_version}
                        if request.universe_timeline_version is not None
                        else {}
                    ),
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
                universe_version=universe_version_for(request),
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
                        "isolated_python_rng": True,
                        "isolated_numpy_rng": False,
                        "deterministic_run_id": True,
                        "deterministic_fill_ids": True,
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
                governance_state=GovernanceState.CANDIDATE,
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
        universe_exit_count: int | None = None,
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
                        # Present only for a run on a universe timeline.
                        **(
                            {"universe_exit_count": universe_exit_count}
                            if universe_exit_count is not None
                            else {}
                        ),
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
                        **(
                            {"universe_exit_count": universe_exit_count}
                            if universe_exit_count is not None
                            else {}
                        ),
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

    def _derive_run_id(self, request: SimulationRunRequest) -> UUID:
        """Derive a stable UUID from the simulation inputs (P-01).

        Identical inputs always produce the same run_id, making fill IDs and intent IDs
        reproducible across reruns without relying on wall-clock time or random generation.
        """
        key = ":".join(
            [
                request.strategy_id,
                str(request.random_seed),
                request.dataset_version,
                request.price_basis.value,
                ":".join(sorted(request.symbols)),
                str(request.start_date),
                str(request.end_date),
                request.experiment_id or "",
                request.stage_name or "",
                request.window_role or "",
            ]
        )
        # Only non-default multipliers extend the key so existing run_ids stay stable.
        if request.cost_multiplier != 1.0:
            key += f":cost_x{request.cost_multiplier}"
        # The same for the universe: only a run on a timeline extends the key.
        if request.universe_timeline_version is not None:
            key += f":universe_timeline={request.universe_timeline_version}"
        return uuid5(_RUN_NS, key)

    def _init_rng(self, seed: int) -> random.Random:
        """Create an isolated, seeded Python RNG instance (P-02).

        Uses random.Random(seed) rather than random.seed() so the simulation's
        randomness is isolated from global module state and from other concurrent runs.
        """
        return random.Random(seed)
