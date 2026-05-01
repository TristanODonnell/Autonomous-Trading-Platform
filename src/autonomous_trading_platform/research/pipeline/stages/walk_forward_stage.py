"""
WalkForwardStage — Stage 3 (Heavy).

Walk-forward analysis: for each fold, run a train simulation then a test
simulation. A strategy survives the fold only if it passes filters on BOTH
windows. A strategy survives the stage only if it passes ALL folds
(or >= min_folds_passed when require_all_folds=False).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

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


@dataclass
class WalkForwardStageConfig:
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


class WalkForwardStage(BaseStage):
    """
    Stage 3 Heavy — walk-forward analysis.

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
    # Loader — owns its own deserialization from a YAML dict block
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        simulation_runner: SimulationRunner,
    ) -> WalkForwardStage:
        stage_cfg = WalkForwardStageConfig(
            name=raw["name"],
            symbols=raw["symbols"],
            start_date=date.fromisoformat(raw["start_date"]),
            end_date=date.fromisoformat(raw["end_date"]),
            train_days=raw["train_days"],
            test_days=raw["test_days"],
            step_days=raw["step_days"],
            require_all_folds=raw.get("require_all_folds", True),
            min_folds_passed=raw.get("min_folds_passed", 1),
            train_filter_config=_parse_filter_config(raw["train_filter_config"]),
            train_scoring_weights=_parse_scoring_weights(raw["train_scoring_weights"]),
            test_filter_config=_parse_filter_config(raw["test_filter_config"]),
            test_scoring_weights=_parse_scoring_weights(raw["test_scoring_weights"]),
        )
        return cls(stage_config=stage_cfg, simulation_runner=simulation_runner)

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
                "  Fold %d | train %s->%s | test %s->%s | %d/%d passed both",
                fold.index,
                fold.train_start,
                fold.train_end,
                fold.test_start,
                fold.test_end,
                passed_fold,
                len(fold_results),
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
        )

    def _generate_folds(self) -> list[_Fold]:
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
        train_filter_by_id = {o.strategy_id: o for o in train_filter_outputs}
        train_sim_by_id = {r.strategy_id: r for r in train_sim_results}

        # ---- test simulations (only train-passers) -----------------------
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

        # ---- test filter -------------------------------------------------
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
                fold_results.append(
                    _FoldResult(
                        fold_index=fold.index,
                        train_sim_result=train_sim,
                        test_sim_result=test_sim,
                        train_filter_output=train_fo,
                        test_filter_output=test_fo,
                        passed=sid in test_passed_ids,
                    )
                )

        return fold_results
