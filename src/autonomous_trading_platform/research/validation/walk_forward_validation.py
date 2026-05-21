"""
Walk-forward validation service.

Analyses per-fold train/test metric pairs to measure:
  - fold consistency: fraction of folds where strategy passes
  - train/test degradation: systematic shrinkage from train to test Sharpe
  - fold Sharpe stability: coefficient of variation of test Sharpe values
  - expanding performance curve: cumulative test Sharpe over time

This service is PURE — it receives already-computed fold data and performs
no simulation.  The ValidationOrchestrator is responsible for supplying
fold inputs from WalkForwardStage results or independent simulation runs.

Window types supported via configuration:
  - rolling   (default): fixed-width train window slides forward by step_days
  - expanding : train window grows from a fixed anchor start
  - anchored  : test window advances; train always anchors from experiment start
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum


class WindowType(StrEnum):
    ROLLING = "rolling"
    EXPANDING = "expanding"
    ANCHORED = "anchored"


@dataclass(frozen=True)
class FoldValidationInput:
    """Per-fold train/test metric pair consumed by WalkForwardValidationService."""

    fold_index: int
    train_sharpe: float
    test_sharpe: float
    train_drawdown: float  # negative decimal
    test_drawdown: float  # negative decimal
    train_return: float
    test_return: float
    train_passed: bool
    test_passed: bool
    fold_passed: bool  # True iff both train and test passed filters


@dataclass(frozen=True)
class WalkForwardValidationResult:
    """Aggregated walk-forward robustness output."""

    n_folds: int
    n_folds_passed: int
    fold_consistency: float  # n_folds_passed / n_folds
    avg_train_sharpe: float
    avg_test_sharpe: float
    median_test_sharpe: float
    train_test_degradation: float  # (train − test) / |train + ε|, positive = degradation
    fold_sharpe_cv: float  # CoV of test Sharpe across folds
    fold_sharpe_stability: float  # 1 − clamp(CoV)
    min_test_sharpe: float
    max_test_sharpe: float
    expanding_test_sharpes: list[float]  # cumulative mean of test Sharpe over folds
    folds: list[FoldValidationInput]
    warnings: list[str] = field(default_factory=list)

    # Robustness helpers
    @property
    def is_consistent(self) -> bool:
        return self.fold_consistency >= 0.6

    @property
    def is_stable(self) -> bool:
        return self.fold_sharpe_cv <= 0.5


_EPSILON = 1e-9


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


class WalkForwardValidationService:
    """
    Analyses fold-level train/test metrics to produce a WalkForwardValidationResult.

    Parameters
    ----------
    min_acceptable_test_sharpe
        Minimum Sharpe that a test fold must exceed to be considered "acceptable".
        Used only for warning generation, not fold pass/fail (that is set by caller).
    """

    def __init__(self, *, min_acceptable_test_sharpe: float = 0.0) -> None:
        self._min_sharpe = min_acceptable_test_sharpe

    def analyze(self, folds: list[FoldValidationInput]) -> WalkForwardValidationResult:
        if not folds:
            raise ValueError("WalkForwardValidationService.analyze requires at least one fold")

        n = len(folds)
        n_passed = sum(1 for f in folds if f.fold_passed)

        train_sharpes = [f.train_sharpe for f in folds]
        test_sharpes = [f.test_sharpe for f in folds]

        avg_train = statistics.mean(train_sharpes)
        avg_test = statistics.mean(test_sharpes)
        median_test = statistics.median(test_sharpes)

        # Train/test degradation: positive = train >> test (suspicious)
        degradation = (avg_train - avg_test) / (abs(avg_train) + _EPSILON)

        # CoV of test Sharpe
        if n >= 2:
            std_test = statistics.stdev(test_sharpes)
            mean_abs = abs(avg_test)
            cv = std_test / (mean_abs + _EPSILON)
        else:
            std_test = 0.0
            cv = 0.0

        stability = _clamp(1.0 / (1.0 + cv))

        # Expanding cumulative mean of test Sharpe (rolling window, fold by fold)
        expanding: list[float] = []
        for i in range(1, n + 1):
            expanding.append(statistics.mean(test_sharpes[:i]))

        warnings: list[str] = []
        if n_passed == 0:
            warnings.append("No folds passed — strategy shows no time-robustness.")
        elif degradation > 0.5:
            warnings.append(
                f"High train/test degradation ({degradation:.2f}): "
                "train Sharpe may not generalise to new data."
            )
        if cv > 1.0:
            warnings.append(
                f"Very high test Sharpe CoV ({cv:.2f}): "
                "fold-to-fold instability suggests fragile performance."
            )
        negative_test_folds = sum(1 for s in test_sharpes if s < self._min_sharpe)
        if negative_test_folds > 0:
            warnings.append(
                f"{negative_test_folds}/{n} test folds below min acceptable Sharpe "
                f"({self._min_sharpe})."
            )

        return WalkForwardValidationResult(
            n_folds=n,
            n_folds_passed=n_passed,
            fold_consistency=n_passed / n,
            avg_train_sharpe=avg_train,
            avg_test_sharpe=avg_test,
            median_test_sharpe=median_test,
            train_test_degradation=degradation,
            fold_sharpe_cv=cv,
            fold_sharpe_stability=stability,
            min_test_sharpe=min(test_sharpes),
            max_test_sharpe=max(test_sharpes),
            expanding_test_sharpes=expanding,
            folds=list(folds),
            warnings=warnings,
        )
