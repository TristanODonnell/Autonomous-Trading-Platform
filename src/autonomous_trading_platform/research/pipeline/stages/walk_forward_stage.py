"""
WalkForwardStage — Stage 3 (Heavy).

Walk-forward analysis: for each fold, run a train simulation then a test
simulation. A strategy survives the fold only if it passes filters on BOTH
windows. A strategy survives the stage only if it passes ALL folds.

Fold generation
---------------
Given a date range [start_date, end_date] and three integers:

    train_days   length of the train window
    test_days    length of the test window
    step_days    how far to slide the window each fold

Fold N:
    train_start = start_date + N * step_days
    train_end   = train_start + train_days
    test_start  = train_end
    test_end    = test_start + test_days

Generation stops when test_end would exceed end_date.

Example (train=365, test=90, step=90, range Jan-2020→Dec-2023):
    Fold 0: train Jan20→Jan21  test Jan21→Apr21
    Fold 1: train Apr20→Apr21  test Apr21→Jul21
    Fold 2: train Jul20→Jul21  test Jul21→Oct21
    ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from autonomous_trading_platform.contracts.common.enums import PriceBasis
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
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_stage import BaseStage, StageResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardStageConfig:
    """
    Configuration for walk-forward fold generation and filtering.

    name            Human-readable stage label, e.g. "heavy_walk_forward".
    symbols         Universe of symbols to run on every fold.
    start_date      Earliest date the fold generator may use.
    end_date        Latest date the fold generator may use.
    train_days      Length of the train window in calendar days.
    test_days       Length of the test (out-of-sample) window in calendar days.
    step_days       How far to slide the window per fold. Overlapping folds
                    are fine and give more signal; non-overlapping is also valid.
    train_filter_config / train_scoring_weights
                    Filter and scoring config applied to train-window results.
                    Typically *looser* than the test filter — you want some
                    breadth coming out of train before the test narrows further.
    test_filter_config / test_scoring_weights
                    Filter and scoring config applied to test-window results.
                    Typically *stricter* — this is the out-of-sample gate.
    require_all_folds
                    If True (default), a strategy must pass every fold to
                    survive the stage. If False, a majority-vote or
                    min_folds_passed threshold can be used (see below).
    min_folds_passed
                    Only used when require_all_folds=False.
                    Minimum number of folds a strategy must pass (both train
                    and test) to be considered a survivor. Defaults to 1.
    """

    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    train_days: int
    test_days: int
    step_days: int
    train_filter_config: FilterConfig
    train_scoring_weights: ScoringWeights
    test_filter_config: FilterConfig
    test_scoring_weights: ScoringWeights
    require_all_folds: bool = True
    min_folds_passed: int = 1

    def __post_init__(self) -> None:
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _Fold:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass
class _FoldResult:
    """Per-fold outcome for a single strategy."""

    fold_index: int
    train_sim_result: SimulationRunResult
    test_sim_result: SimulationRunResult | None  # None if failed train filter
    train_filter_output: FilterScoreOutput
    test_filter_output: FilterScoreOutput | None  # None if failed train filter
    passed: bool  # True only if passed both train AND test filters


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class WalkForwardStage(BaseStage):
    """
    Stage 3 Heavy — walk-forward analysis.

    For every fold:
      1. Run simulation over the TRAIN window for all current survivors.
      2. Apply train filter — drop strategies that fail.
      3. Run simulation over the TEST window for train-passing strategies.
      4. Apply test filter — drop strategies that fail.
      5. A strategy passes the fold iff it passed both filters.

    After all folds:
      - require_all_folds=True  → must have passed every single fold.
      - require_all_folds=False → must have passed >= min_folds_passed folds.
    """

    def __init__(
        self,
        *,
        stage_config: WalkForwardStageConfig,
        simulation_runner: SimulationRunner,
    ) -> None:
        self._cfg = stage_config
        self._simulation_runner = simulation_runner
        self._train_filter_service = FilterScoreService(
            filter_config=stage_config.train_filter_config,
            scoring_weights=stage_config.train_scoring_weights,
        )
        self._test_filter_service = FilterScoreService(
            filter_config=stage_config.test_filter_config,
            scoring_weights=stage_config.test_scoring_weights,
        )

    # ------------------------------------------------------------------
    # BaseStage interface
    # ------------------------------------------------------------------

    @property
    def stage_name(self) -> str:
        return self._cfg.name

    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
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

        # fold_pass_counts[strategy_id] = number of folds where strategy passed both filters
        fold_pass_counts: dict[str, int] = {c.strategy_id: 0 for c in survivors}

        all_sim_results: list[SimulationRunResult] = []
        all_filter_outputs: list[FilterScoreOutput] = []

        for fold in folds:
            fold_results = self._run_fold(
                fold=fold,
                configs=survivors,
                experiment_id=experiment_id,
                dataset_version=dataset_version,
                random_seed=random_seed,
                price_basis=price_basis,
                initial_cash=initial_cash,
            )

            passed_fold = 0
            for fr in fold_results:
                all_sim_results.append(fr.train_sim_result)
                all_filter_outputs.append(fr.train_filter_output)

                if fr.test_sim_result is not None:
                    all_sim_results.append(fr.test_sim_result)
                if fr.test_filter_output is not None:
                    all_filter_outputs.append(fr.test_filter_output)

                if fr.passed:
                    fold_pass_counts[fr.train_sim_result.strategy_id] += 1
                    passed_fold += 1

            logger.info(
                "  Fold %d | train %s→%s | test %s→%s | %d/%d passed both",
                fold.index,
                fold.train_start,
                fold.train_end,
                fold.test_start,
                fold.test_end,
                passed_fold,
                len(fold_results),
            )

        # ------------------------------------------------------------------
        # Determine final survivors across all folds
        # ------------------------------------------------------------------
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
        )

    # ------------------------------------------------------------------
    # Fold generation
    # ------------------------------------------------------------------

    def _generate_folds(self) -> list[_Fold]:
        """
        Slide a (train_days + test_days) window across [start_date, end_date]
        advancing by step_days each iteration.
        """
        folds: list[_Fold] = []
        fold_index = 0
        train_start = self._cfg.start_date

        while True:
            train_end = train_start + timedelta(days=self._cfg.train_days)
            test_start = train_end
            test_end = test_start + timedelta(days=self._cfg.test_days)

            if test_end > self._cfg.end_date:
                break

            folds.append(
                _Fold(
                    index=fold_index,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )

            train_start += timedelta(days=self._cfg.step_days)
            fold_index += 1

        return folds

    # ------------------------------------------------------------------
    # Per-fold execution
    # ------------------------------------------------------------------

    def _run_fold(
        self,
        *,
        fold: _Fold,
        configs: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
    ) -> list[_FoldResult]:
        """
        For one fold:
          1. Run all configs on the train window.
          2. Filter — keep only train-passing strategies.
          3. Run train-passers on the test window.
          4. Filter test results.
          5. A strategy passes the fold iff it passed both.
        """
        cfg = self._cfg
        window_role_train = f"fold_{fold.index}_train"
        window_role_test = f"fold_{fold.index}_test"

        # ---- train simulations -------------------------------------------
        train_sim_results: list[SimulationRunResult] = []
        for config in configs:
            req = SimulationRunRequest(
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=dataset_version,
                random_seed=random_seed,
                price_basis=price_basis,
                symbols=cfg.symbols,
                start_date=fold.train_start,
                end_date=fold.train_end,
                initial_cash=initial_cash,
                window_role=window_role_train,
            )
            train_sim_results.append(self._simulation_runner.run(req))

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

        # map strategy_id → train filter output for assembly below
        train_filter_by_id = {o.strategy_id: o for o in train_filter_outputs}
        train_sim_by_id = {r.strategy_id: r for r in train_sim_results}

        # ---- test simulations (only train-passers) ------------------------
        test_configs = [c for c in configs if c.strategy_id in train_passed_ids]
        test_sim_results: list[SimulationRunResult] = []
        for config in test_configs:
            req = SimulationRunRequest(
                experiment_id=experiment_id,
                strategy_id=config.strategy_id,
                strategy_config=config.model_dump(),
                dataset_version=dataset_version,
                random_seed=random_seed,
                price_basis=price_basis,
                symbols=cfg.symbols,
                start_date=fold.test_start,
                end_date=fold.test_end,
                initial_cash=initial_cash,
                window_role=window_role_test,
            )
            test_sim_results.append(self._simulation_runner.run(req))

        # ---- test filter --------------------------------------------------
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

            if sid not in train_passed_ids:
                # Failed train — no test was run
                fold_results.append(
                    _FoldResult(
                        fold_index=fold.index,
                        train_sim_result=train_sim,
                        test_sim_result=None,
                        train_filter_output=train_fo,
                        test_filter_output=None,
                        passed=False,
                    )
                )
            else:
                test_sim = test_sim_by_id[sid]
                test_fo = test_filter_by_id[sid]
                passed_test = sid in test_passed_ids
                fold_results.append(
                    _FoldResult(
                        fold_index=fold.index,
                        train_sim_result=train_sim,
                        test_sim_result=test_sim,
                        train_filter_output=train_fo,
                        test_filter_output=test_fo,
                        passed=passed_test,
                    )
                )

        return fold_results
