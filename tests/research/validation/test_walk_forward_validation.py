"""Tests for WalkForwardValidationService."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
    WalkForwardValidationService,
)


def _fold(
    index: int,
    train_sharpe: float,
    test_sharpe: float,
    fold_passed: bool = True,
) -> FoldValidationInput:
    return FoldValidationInput(
        fold_index=index,
        train_sharpe=train_sharpe,
        test_sharpe=test_sharpe,
        train_drawdown=-0.05,
        test_drawdown=-0.07,
        train_return=0.10,
        test_return=0.08,
        train_passed=True,
        test_passed=fold_passed,
        fold_passed=fold_passed,
    )


def test_basic_consistency():
    service = WalkForwardValidationService()
    folds = [_fold(i, 1.5, 1.2, True) for i in range(5)]
    result = service.analyze(folds)

    assert result.n_folds == 5
    assert result.n_folds_passed == 5
    assert result.fold_consistency == 1.0
    assert result.avg_train_sharpe == pytest.approx(1.5)
    assert result.avg_test_sharpe == pytest.approx(1.2)
    assert result.is_consistent


def test_partial_consistency():
    service = WalkForwardValidationService()
    folds = [_fold(0, 2.0, 1.5, True), _fold(1, 2.0, -0.5, False), _fold(2, 2.0, 1.3, True)]
    result = service.analyze(folds)

    assert result.n_folds == 3
    assert result.n_folds_passed == 2
    assert result.fold_consistency == pytest.approx(2 / 3)


def test_zero_folds_raises():
    service = WalkForwardValidationService()
    with pytest.raises(ValueError, match="at least one fold"):
        service.analyze([])


def test_train_test_degradation_detected():
    # Train Sharpe = 3.0, test Sharpe = 0.5 → big degradation
    service = WalkForwardValidationService()
    folds = [_fold(i, 3.0, 0.5, True) for i in range(4)]
    result = service.analyze(folds)

    assert result.train_test_degradation > 0.5
    assert any("degradation" in w.lower() for w in result.warnings)


def test_expanding_test_sharpes_length():
    service = WalkForwardValidationService()
    folds = [_fold(i, 1.5, float(i + 1) * 0.5, True) for i in range(4)]
    result = service.analyze(folds)

    assert len(result.expanding_test_sharpes) == 4
    # Each entry is the mean of test sharpes seen so far
    assert result.expanding_test_sharpes[0] == pytest.approx(0.5)
    assert result.expanding_test_sharpes[-1] == pytest.approx(sum([0.5, 1.0, 1.5, 2.0]) / 4)


def test_fold_sharpe_stability_perfect():
    """When all test sharpes are identical, stability should be 1.0."""
    service = WalkForwardValidationService()
    folds = [_fold(i, 1.5, 1.2, True) for i in range(6)]
    result = service.analyze(folds)

    assert result.fold_sharpe_cv == pytest.approx(0.0, abs=1e-9)
    assert result.fold_sharpe_stability == pytest.approx(1.0)


def test_no_folds_passed_produces_warning():
    service = WalkForwardValidationService()
    folds = [_fold(i, 1.5, -0.3, False) for i in range(3)]
    result = service.analyze(folds)

    assert result.fold_consistency == 0.0
    assert not result.is_consistent
    assert any("no folds" in w.lower() for w in result.warnings)


def test_deterministic():
    """Identical inputs produce identical results."""
    folds = [_fold(i, float(i + 1), float(i) * 0.8, i % 2 == 0) for i in range(6)]
    svc = WalkForwardValidationService()
    r1 = svc.analyze(folds)
    r2 = svc.analyze(folds)

    assert r1.fold_consistency == r2.fold_consistency
    assert r1.avg_test_sharpe == r2.avg_test_sharpe
    assert r1.train_test_degradation == r2.train_test_degradation
