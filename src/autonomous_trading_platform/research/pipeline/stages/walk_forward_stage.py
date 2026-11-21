"""
WalkForwardStage — rolling sub-period evaluation of a fixed strategy config.

For each fold, run a train simulation then a test simulation. A strategy
survives the fold only if it passes filters on BOTH windows. A strategy
survives the stage only if it passes ALL folds (or >= min_folds_passed when
require_all_folds=False).

What it is and is not
---------------------
Nothing is fitted on the train window: the same config runs unchanged on
train and test, and the train window is a pass/fail filter. So this is a
consistency check across sub-periods, not walk-forward optimisation.

A test fold is out of sample only if no earlier stage picked the survivors
on dates that fold covers. The stage cannot check that; the pipeline that
builds it has to arrange it (research_hooks.py ends stage 1's window the day
before the first test fold starts).

Two ways to say where the folds are
-----------------------------------
Rolling (train_days, test_days, step_days): the stage lays the folds out
itself. Fold boundaries are calendar days, and train_end and test_start are
the same date.

By date (folds): the pipeline hands the stage its folds, each a train window
and a test window given by first and last day, both read. This is how a
layout counted in trading sessions reaches the stage, and how every fold can
share one train window. A train window that several folds share is simulated
once.

Rules the config enforces (plan items R2 and R6)
------------------------------------------------
- Rolling: step_days >= test_days, so no day is in two test folds. A config
  that cannot meet this yet says so with allow_overlapping_test_folds=True.
- By date: a fold's train window ends before its test window starts; test
  windows are in order and share no day; no train window covers a day of any
  fold's test window. A fold passes only when its train run and its test run
  both pass, so a train window lying on another fold's test days would make
  one test period decide two folds. There is no exception to these.
- min_folds_passed <= the number of folds the window produces (when
  require_all_folds is False), or no strategy could ever pass.
The rule that test folds start after earlier selection stages end involves
other stages and lives in pipeline_validation.py.

Place in the pipeline
---------------------
Second of the six stages in the current pipeline (research_hooks.py):

  initial_quality_filter → walk_forward → monte_carlo → regime → stress → overfitting

The older four-stage layout (experiments/staged/ma_sweep_staged.yaml) has it
third.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.checkpoints.research_checkpoint import ResearchTaskType
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
    ResumeMode,
)
from autonomous_trading_platform.research.execution import (
    DeterministicSeedInputs,
    DeterministicSeedService,
    ExecutionMode,
    ExecutionUnit,
    ParallelExecutionConfig,
    ParallelExecutionService,
)
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreInput,
    FilterScoreOutput,
    FilterScoreService,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ..pipeline_validation import StageWindowFacts
from .base_stage import BaseStage, StageDiagnostics, StageResult

logger = logging.getLogger(__name__)


def _parse_filter_config(fc: dict[str, Any]) -> FilterConfig:
    return FilterConfig(
        min_sharpe=fc.get("min_sharpe", 1.0),
        max_drawdown=fc.get("max_drawdown", -0.20),
        min_trades=fc.get("min_trades", 30),
        min_consistency_score=fc.get("min_consistency_score", 0.5),
        min_profit_factor=fc.get("min_profit_factor", 1.0),
        min_win_rate=fc.get("min_win_rate", 0.0),
        min_total_return=fc.get("min_total_return", 0.0),
    )


def _parse_scoring_weights(sw: dict[str, Any]) -> ScoringWeights:
    return ScoringWeights(
        w_sharpe=sw.get("w_sharpe", 0.4),
        w_return=sw.get("w_return", 0.3),
        w_drawdown=sw.get("w_drawdown", 0.2),
        w_consistency=sw.get("w_consistency", 0.1),
    )


@dataclass(frozen=True)
class FoldWindow:
    """One fold given by date. All four days are read."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass(kw_only=True)
class WalkForwardStageConfig:
    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    # Rolling folds: all three given, and ``folds`` left out.
    train_days: int = 0
    test_days: int = 0
    step_days: int = 0
    # Folds by date: given, and the three counts above left out.
    folds: tuple[FoldWindow, ...] | None = None
    train_filter_config: FilterConfig
    train_scoring_weights: ScoringWeights
    test_filter_config: FilterConfig
    test_scoring_weights: ScoringWeights
    require_all_folds: bool = True
    min_folds_passed: int = 1
    execution_mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False
    # A named exception to the step_days >= test_days rule, for a window too
    # short to hold two separate test folds. Overlapping folds share days, so
    # two passes are less than two independent pieces of evidence.
    allow_overlapping_test_folds: bool = False
    # The exported universe timeline the folds trade on (a dataset version id).
    # Each fold then loads the names of the lists in force during its own dates.
    # None = the symbols above are the universe for every fold.
    universe_timeline_version: str | None = None

    def __post_init__(self) -> None:
        if self.folds is not None:
            self.folds = tuple(self.folds)
            self._check_folds_by_date(self.folds)
        else:
            self._check_rolling_folds()
        n_folds = len(self.fold_windows())
        if not self.require_all_folds and self.min_folds_passed > n_folds:
            raise ValueError(
                f"min_folds_passed ({self.min_folds_passed}) > the {n_folds} fold(s) this window "
                "produces: no strategy could pass."
            )

    def _check_folds_by_date(self, folds: tuple[FoldWindow, ...]) -> None:
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if not folds:
            raise ValueError("folds is empty: give at least one fold, or leave folds out")
        if self.train_days or self.test_days or self.step_days:
            raise ValueError(
                "folds are given by date: train_days, test_days and step_days do not apply "
                "and must be left out"
            )
        if self.allow_overlapping_test_folds:
            raise ValueError(
                "allow_overlapping_test_folds has no meaning for folds given by date: their "
                "test windows never share a day"
            )
        for number, fold in enumerate(folds, 1):
            if fold.train_end < fold.train_start or fold.test_end < fold.test_start:
                raise ValueError(f"fold {number}: a window ends before it starts")
            if fold.train_end >= fold.test_start:
                raise ValueError(
                    f"fold {number}: the train window ends on {fold.train_end}, on or after "
                    f"the test window's first day {fold.test_start}"
                )
            if fold.train_start < self.start_date or fold.test_end > self.end_date:
                raise ValueError(
                    f"fold {number} ({fold.train_start} to {fold.test_end}) lies outside the "
                    f"stage's window {self.start_date} to {self.end_date}"
                )
        for number, (earlier, later) in enumerate(zip(folds, folds[1:], strict=False), 1):
            if earlier.test_end >= later.test_start:
                raise ValueError(
                    f"fold {number} tests up to {earlier.test_end} and fold {number + 1} "
                    f"tests from {later.test_start}: test windows must be in order and "
                    "share no day"
                )
        for train_number, trained in enumerate(folds, 1):
            for test_number, tested in enumerate(folds, 1):
                if (
                    trained.train_start <= tested.test_end
                    and tested.test_start <= trained.train_end
                ):
                    raise ValueError(
                        f"fold {train_number}'s train window ({trained.train_start} to "
                        f"{trained.train_end}) covers days of fold {test_number}'s test "
                        f"window ({tested.test_start} to {tested.test_end}): one test period "
                        "would decide two folds"
                    )

    def _check_rolling_folds(self) -> None:
        if self.train_days <= 0:
            raise ValueError("train_days must be positive")
        if self.test_days <= 0:
            raise ValueError("test_days must be positive")
        if self.step_days <= 0:
            raise ValueError("step_days must be positive")
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        min_span = self.train_days + self.test_days
        total_days = (self.end_date - self.start_date).days
        if total_days < min_span:
            raise ValueError(
                f"Date range ({total_days} days) is shorter than "
                f"train_days + test_days ({min_span} days). No folds can be generated."
            )
        if self.step_days < self.test_days and not self.allow_overlapping_test_folds:
            raise ValueError(
                f"step_days ({self.step_days}) < test_days ({self.test_days}): consecutive test "
                f"folds would share {self.test_days - self.step_days} days. Use step_days >= "
                "test_days, or set allow_overlapping_test_folds=True to accept it knowingly."
            )

    @property
    def first_test_start(self) -> date:
        if self.folds is not None:
            return self.folds[0].test_start
        return self.start_date + timedelta(days=self.train_days)

    def fold_windows(self) -> list[tuple[date, date, date, date]]:
        """(train_start, train_end, test_start, test_end) for every fold: the folds
        given by date, or every rolling fold that fits."""
        if self.folds is not None:
            return [
                (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
                for fold in self.folds
            ]
        windows: list[tuple[date, date, date, date]] = []
        train_start = self.start_date
        while True:
            train_end = train_start + timedelta(days=self.train_days)
            test_end = train_end + timedelta(days=self.test_days)
            if test_end > self.end_date:
                return windows
            windows.append((train_start, train_end, train_end, test_end))
            train_start += timedelta(days=self.step_days)


@dataclass
class _Fold:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass
class _FoldResult:
    fold_index: int
    train_sim_result: SimulationRunResult
    test_sim_result: SimulationRunResult | None
    train_filter_output: FilterScoreOutput
    test_filter_output: FilterScoreOutput | None
    passed: bool


def _to_fold_input(fr: _FoldResult) -> FoldValidationInput | None:
    """Convert a fold into validation input.

    Every fold with a test run is evidence, including one whose train run
    failed the filter (train_passed=False, fold_passed=False). Only a fold
    with no test run at all (a dry run) is skipped.
    """
    test = fr.test_sim_result
    if test is None or fr.test_filter_output is None:
        return None
    train = fr.train_sim_result
    return FoldValidationInput(
        fold_index=fr.fold_index,
        train_sharpe=float(train.risk_metrics.sharpe_ratio),
        test_sharpe=float(test.risk_metrics.sharpe_ratio),
        train_drawdown=float(train.risk_metrics.max_drawdown),
        test_drawdown=float(test.risk_metrics.max_drawdown),
        train_return=float(train.return_metrics.total_return),
        test_return=float(test.return_metrics.total_return),
        train_passed=bool(fr.train_filter_output.filter_result.passed),
        test_passed=bool(fr.test_filter_output.filter_result.passed),
        fold_passed=fr.passed,
    )


class WalkForwardStage(BaseStage):
    """
    Walk-forward analysis of a fixed config over rolling train/test sub-periods.

    For every fold:
      1. Run simulation over the TRAIN window for all current survivors.
      2. Apply train filter — drop strategies that fail.
      3. Run simulation over the TEST window for train-passing strategies only.
      4. Apply test filter — drop strategies that fail.
      5. A strategy passes the fold iff it passed both filters.

    After all folds:
      - require_all_folds=True  -> must have passed every single fold.
      - require_all_folds=False -> must have passed >= min_folds_passed folds.
    """

    def __init__(
        self,
        *,
        stage_config: WalkForwardStageConfig,
        simulation_runner: SimulationRunner,
        checkpoint_service: ResearchCheckpointService | None = None,
        resume_mode: ResumeMode = ResumeMode.ALL,
        force_rerun: bool = False,
        dry_run: bool = False,
    ) -> None:
        self._cfg = stage_config
        self._simulation_runner = simulation_runner
        self._checkpoint_service = checkpoint_service
        self._resume_mode = resume_mode
        self._force_rerun = force_rerun
        self._dry_run = dry_run
        self._seed_service = DeterministicSeedService()
        self._execution_service = ParallelExecutionService(
            ParallelExecutionConfig(
                mode=stage_config.execution_mode,
                max_workers=stage_config.max_workers,
                fail_fast=stage_config.fail_fast,
            )
        )
        self._train_filter_service = FilterScoreService(
            filter_config=stage_config.train_filter_config,
            scoring_weights=stage_config.train_scoring_weights,
        )
        self._test_filter_service = FilterScoreService(
            filter_config=stage_config.test_filter_config,
            scoring_weights=stage_config.test_scoring_weights,
        )

    # ------------------------------------------------------------------
    # Loader — owns its own deserialization from a YAML dict block
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        simulation_runner: SimulationRunner,
    ) -> WalkForwardStage:
        from autonomous_trading_platform.research.config.stage_configs import (
            WalkForwardStageConfigModel,
        )

        validated = WalkForwardStageConfigModel.model_validate(raw)
        stage_cfg = WalkForwardStageConfig(
            name=validated.name,
            symbols=list(validated.symbols),
            start_date=validated.start_date,
            end_date=validated.end_date,
            train_days=validated.train_days,
            test_days=validated.test_days,
            step_days=validated.step_days,
            require_all_folds=validated.require_all_folds,
            min_folds_passed=validated.min_folds_passed,
            allow_overlapping_test_folds=validated.allow_overlapping_test_folds,
            execution_mode=ExecutionMode(validated.execution_mode),
            max_workers=validated.max_workers,
            fail_fast=validated.fail_fast,
            train_filter_config=validated.train_filter_config.to_dataclass(),
            train_scoring_weights=validated.train_scoring_weights.to_dataclass(),
            test_filter_config=validated.test_filter_config.to_dataclass(),
            test_scoring_weights=validated.test_scoring_weights.to_dataclass(),
        )
        return cls(stage_config=stage_cfg, simulation_runner=simulation_runner)

    @property
    def stage_name(self) -> str:
        return self._cfg.name

    def validation_facts(self) -> StageWindowFacts:
        cfg = self._cfg
        return StageWindowFacts(
            stage_name=cfg.name,
            kind="walk_forward",
            first_test_start=cfg.first_test_start,
            # A fold's test window is the last thing it reads.
            last_day_read=max((fold[3] for fold in cfg.fold_windows()), default=None),
            filters=(
                ("train_filter_config", cfg.train_filter_config),
                ("test_filter_config", cfg.test_filter_config),
            ),
        )

    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> StageResult:
        if not survivors:
            logger.warning("Stage %s received empty survivor list — skipping.", self.stage_name)
            return StageResult(stage_name=self.stage_name)

        folds = self._generate_folds()
        if not folds:
            raise RuntimeError(
                f"WalkForwardStage '{self.stage_name}' generated zero folds. "
                "Check start_date, end_date, train_days, test_days, step_days."
            )

        logger.info(
            "Stage %-20s | %d folds | %d strategies entering",
            self.stage_name,
            len(folds),
            len(survivors),
        )

        fold_pass_counts: dict[str, int] = {c.strategy_id: 0 for c in survivors}
        # Every fold per strategy (train and test both simulated, whether or not
        # train passed) — evidence for OverfittingStage and the validation layer.
        fold_inputs: dict[str, list[FoldValidationInput]] = {c.strategy_id: [] for c in survivors}
        all_sim_results: list[SimulationRunResult] = []
        all_filter_outputs: list[FilterScoreOutput] = []
        # A train window is simulated once: folds that share one share its runs.
        train_runs: dict[tuple[date, date], list[SimulationRunResult]] = {}
        train_runs_recorded: set[int] = set()

        for fold in folds:
            train_is_shared = (fold.train_start, fold.train_end) in train_runs
            fold_results = self._run_fold(
                fold=fold,
                train_runs=train_runs,
                configs=survivors,
                experiment_id=experiment_id,
                dataset_version=dataset_version,
                random_seed=random_seed,
                price_basis=price_basis,
                initial_cash=initial_cash,
                resample_to_daily=resample_to_daily,
            )

            passed_fold = 0
            failed_train = 0
            for fr in fold_results:
                if not fr.train_filter_output.filter_result.passed:
                    failed_train += 1
                # A shared train run is one simulation and one verdict in the results.
                if id(fr.train_sim_result) not in train_runs_recorded:
                    train_runs_recorded.add(id(fr.train_sim_result))
                    all_sim_results.append(fr.train_sim_result)
                    all_filter_outputs.append(fr.train_filter_output)
                if fr.test_sim_result is not None:
                    all_sim_results.append(fr.test_sim_result)
                if fr.test_filter_output is not None:
                    all_filter_outputs.append(fr.test_filter_output)
                fold_input = _to_fold_input(fr)
                if fold_input is not None:
                    fold_inputs[fr.train_sim_result.strategy_id].append(fold_input)
                if fr.passed:
                    fold_pass_counts[fr.train_sim_result.strategy_id] += 1
                    passed_fold += 1

            logger.info(
                "  Fold %d | train %s->%s%s | test %s->%s | %d/%d passed both"
                " | %d failed train (test run kept as evidence only)",
                fold.index,
                fold.train_start,
                fold.train_end,
                " (runs shared with an earlier fold)" if train_is_shared else "",
                fold.test_start,
                fold.test_end,
                passed_fold,
                len(fold_results),
                failed_train,
            )

        if not all_sim_results:
            return StageResult(
                stage_name=self.stage_name,
                simulation_results=[],
                filter_outputs=[],
                survivors=survivors,
            )

        n_folds = len(folds)
        final_survivors: list[StrategyConfig] = []

        for config in survivors:
            sid = config.strategy_id
            passed = fold_pass_counts[sid]
            threshold = n_folds if self._cfg.require_all_folds else self._cfg.min_folds_passed
            if passed >= threshold:
                final_survivors.append(config)
            else:
                logger.debug(
                    "  ELIMINATED %s | passed %d/%d folds (need %d)",
                    sid,
                    passed,
                    n_folds,
                    threshold,
                )

        logger.info(
            "Stage %-20s | %d folds | entered %d | survived %d | eliminated %d",
            self.stage_name,
            n_folds,
            len(survivors),
            len(final_survivors),
            len(survivors) - len(final_survivors),
        )

        return StageResult(
            stage_name=self.stage_name,
            simulation_results=all_sim_results,
            filter_outputs=all_filter_outputs,
            survivors=final_survivors,
            diagnostics={
                sid: StageDiagnostics(fold_inputs=inputs)
                for sid, inputs in fold_inputs.items()
                if inputs
            },
        )

    def _generate_folds(self) -> list[_Fold]:
        return [
            _Fold(
                index=index,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
            for index, (train_start, train_end, test_start, test_end) in enumerate(
                self._cfg.fold_windows()
            )
        ]

    def _run_fold(
        self,
        *,
        fold: _Fold,
        train_runs: dict[tuple[date, date], list[SimulationRunResult]],
        configs: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> list[_FoldResult]:
        cfg = self._cfg
        window_role_train = f"fold_{fold.index}_train"
        window_role_test = f"fold_{fold.index}_test"

        # ---- train simulations -------------------------------------------
        # Simulated by the first fold that has this train window (and so under
        # that fold's name and seed); later folds with the same window reuse it.
        train_window = (fold.train_start, fold.train_end)
        train_units: list[ExecutionUnit[SimulationRunResult | None]] = []
        for index, config in enumerate(configs if train_window not in train_runs else []):
            seed = self._seed_service.derive_seed(
                DeterministicSeedInputs(
                    base_seed=random_seed,
                    experiment_id=experiment_id,
                    strategy_id=config.strategy_id,
                    config_hash=config.config_hash(),
                    stage_name=self.stage_name,
                    window_role=window_role_train,
                    fold_id=fold.index,
                )
            )
            req = SimulationRunRequest(
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=dataset_version,
                random_seed=seed,
                price_basis=price_basis,
                symbols=cfg.symbols,
                universe_timeline_version=cfg.universe_timeline_version,
                start_date=fold.train_start,
                end_date=fold.train_end,
                initial_cash=initial_cash,
                window_role=window_role_train,
                stage_name=self.stage_name,
                resample_to_daily=resample_to_daily,
            )
            train_units.append(
                ExecutionUnit(
                    unit_id=f"{self.stage_name}:{window_role_train}:{config.strategy_id}",
                    sort_key=(fold.index, 0, index, config.strategy_id),
                    run=self._fold_simulation_runner_for(req),
                    metadata={
                        "fold_id": fold.index,
                        "strategy_id": config.strategy_id,
                        "stage_name": self.stage_name,
                        "window_role": window_role_train,
                        "seed": seed,
                    },
                )
            )
        if train_window not in train_runs:
            maybe_train_sim_results = self._execution_service.values(train_units)
            train_runs[train_window] = [
                result for result in maybe_train_sim_results if result is not None
            ]
        train_sim_results = train_runs[train_window]

        if not train_sim_results:
            return []

        # ---- train filter -------------------------------------------------
        train_filter_inputs = [
            FilterScoreInput(
                strategy_id=r.strategy_id,
                rm=r.return_metrics,
                risk=r.risk_metrics,
                tm=r.trade_metrics,
                sm=r.stability_metrics,
                equity_curve=r.equity_curve,
            )
            for r in train_sim_results
        ]
        train_filter_outputs, _ = self._train_filter_service.filter_and_rank(train_filter_inputs)
        train_passed_ids = {o.strategy_id for o in train_filter_outputs if o.filter_result.passed}
        train_filter_by_id = {o.strategy_id: o for o in train_filter_outputs}
        train_sim_by_id = {r.strategy_id: r for r in train_sim_results}

        # ---- test simulations (every strategy with a train run) ----------
        # A fold counts for a strategy only if train AND test pass. Strategies
        # that failed train are still simulated on the test window so the fold
        # is recorded as evidence; dropping it would leave only the folds the
        # strategy did well on (plan R2). Train-passers keep their order.
        train_failed_ids = set(train_sim_by_id) - train_passed_ids
        test_configs = [c for c in configs if c.strategy_id in train_passed_ids] + [
            c for c in configs if c.strategy_id in train_failed_ids
        ]
        test_units: list[ExecutionUnit[SimulationRunResult | None]] = []
        for index, config in enumerate(test_configs):
            seed = self._seed_service.derive_seed(
                DeterministicSeedInputs(
                    base_seed=random_seed,
                    experiment_id=experiment_id,
                    strategy_id=config.strategy_id,
                    config_hash=config.config_hash(),
                    stage_name=self.stage_name,
                    window_role=window_role_test,
                    fold_id=fold.index,
                )
            )
            req = SimulationRunRequest(
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=dataset_version,
                random_seed=seed,
                price_basis=price_basis,
                symbols=cfg.symbols,
                universe_timeline_version=cfg.universe_timeline_version,
                start_date=fold.test_start,
                end_date=fold.test_end,
                initial_cash=initial_cash,
                window_role=window_role_test,
                stage_name=self.stage_name,
                resample_to_daily=resample_to_daily,
            )
            test_units.append(
                ExecutionUnit(
                    unit_id=f"{self.stage_name}:{window_role_test}:{config.strategy_id}",
                    sort_key=(fold.index, 1, index, config.strategy_id),
                    run=self._fold_simulation_runner_for(req),
                    metadata={
                        "fold_id": fold.index,
                        "strategy_id": config.strategy_id,
                        "stage_name": self.stage_name,
                        "window_role": window_role_test,
                        "seed": seed,
                    },
                )
            )
        maybe_test_sim_results = self._execution_service.values(test_units)
        test_sim_results = [result for result in maybe_test_sim_results if result is not None]

        # ---- test filter -------------------------------------------------
        # Pass and score are computed per strategy, so the evidence-only runs
        # sharing this call cannot change a train-passer's verdict.
        test_filter_inputs = [
            FilterScoreInput(
                strategy_id=r.strategy_id,
                rm=r.return_metrics,
                risk=r.risk_metrics,
                tm=r.trade_metrics,
                sm=r.stability_metrics,
                equity_curve=r.equity_curve,
            )
            for r in test_sim_results
        ]
        test_filter_outputs, _ = self._test_filter_service.filter_and_rank(test_filter_inputs)
        test_passed_ids = {o.strategy_id for o in test_filter_outputs if o.filter_result.passed}
        test_filter_by_id = {o.strategy_id: o for o in test_filter_outputs}
        test_sim_by_id = {r.strategy_id: r for r in test_sim_results}

        # ---- assemble FoldResult per strategy ----------------------------
        fold_results: list[_FoldResult] = []
        for config in configs:
            sid = config.strategy_id
            train_sim = train_sim_by_id[sid]
            train_fo = train_filter_by_id[sid]

            fold_results.append(
                _FoldResult(
                    fold_index=fold.index,
                    train_sim_result=train_sim,
                    test_sim_result=test_sim_by_id.get(sid),
                    train_filter_output=train_fo,
                    test_filter_output=test_filter_by_id.get(sid),
                    passed=sid in train_passed_ids and sid in test_passed_ids,
                )
            )

        return fold_results

    def _fold_simulation_runner_for(
        self,
        request: SimulationRunRequest,
    ) -> Callable[[], SimulationRunResult | None]:
        def run() -> SimulationRunResult | None:
            return self._run_resumable_fold_simulation(request)

        return run

    def _run_resumable_fold_simulation(
        self,
        request: SimulationRunRequest,
    ) -> SimulationRunResult | None:
        if self._checkpoint_service is None:
            return self._simulation_runner.run(request)
        execution = self._checkpoint_service.run_simulation_unit(
            request=request,
            runner=self._simulation_runner,
            task_type=ResearchTaskType.WALK_FORWARD_FOLD,
            resume_mode=self._resume_mode,
            force_rerun=self._force_rerun,
            dry_run=self._dry_run,
        )
        return execution.result
