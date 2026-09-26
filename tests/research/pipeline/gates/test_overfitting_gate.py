"""Unit tests for OverfittingGate evidence collection and elimination rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
    MetricDistribution,
    MonteCarloAggregation,
)
from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
    OverfittingEvidence,
    OverfittingGateConfig,
    collect_overfitting_evidence,
    evaluate_overfitting_gate,
)
from autonomous_trading_platform.research.pipeline.stages.base_stage import (
    StageDiagnostics,
    StageResult,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
)


def _fold(i: int, train: float, test: float) -> FoldValidationInput:
    return FoldValidationInput(
        fold_index=i,
        train_sharpe=train,
        test_sharpe=test,
        train_drawdown=-0.05,
        test_drawdown=-0.05,
        train_return=0.02,
        test_return=0.01,
        train_passed=True,
        test_passed=test > 0,
        fold_passed=test > 0,
    )


def _mc(mean: float, std: float) -> MonteCarloAggregation:
    dist = MetricDistribution(mean=mean, median=mean, std=std, min=mean - std, max=mean + std)
    return MonteCarloAggregation(
        strategy_id="s1",
        n_runs=5,
        n_passed=5,
        pass_rate=1.0,
        passed=True,
        sharpe_dist=dist,
        return_dist=dist,
        drawdown_dist=dist,
    )


def _ref(trade_count: int = 100):
    return SimpleNamespace(trade_count=trade_count, equity_curve=None)


CFG = OverfittingGateConfig()


class TestCollectEvidence:
    def test_merges_across_stages_and_reindexes_folds(self) -> None:
        mc = _mc(1.0, 0.1)
        ref = _ref()
        prior = [
            StageResult(stage_name="sim"),
            StageResult(
                stage_name="wf_a",
                diagnostics={"s1": StageDiagnostics(fold_inputs=[_fold(0, 1, 1), _fold(1, 1, 1)])},
            ),
            StageResult(
                stage_name="wf_b",
                diagnostics={"s1": StageDiagnostics(fold_inputs=[_fold(0, 1, 1)])},
            ),
            StageResult(
                stage_name="mc",
                diagnostics={"s1": StageDiagnostics(mc_aggregation=mc, reference_result=ref)},  # type: ignore[arg-type]
            ),
            StageResult(
                stage_name="other", diagnostics={"s2": StageDiagnostics(mc_aggregation=_mc(0, 1))}
            ),
        ]
        evidence = collect_overfitting_evidence("s1", prior)
        assert [f.fold_index for f in evidence.fold_inputs] == [0, 1, 2]
        assert evidence.mc_aggregation is mc
        assert evidence.reference_result is ref
        assert evidence.regime_profile is None

    def test_no_evidence_for_unknown_strategy(self) -> None:
        evidence = collect_overfitting_evidence("nope", [StageResult(stage_name="x")])
        assert evidence.fold_inputs == []
        assert evidence.mc_aggregation is None


class TestOverfittingGate:
    def test_consistent_strategy_passes(self) -> None:
        evidence = OverfittingEvidence(
            fold_inputs=[_fold(0, 1.2, 1.1), _fold(1, 1.1, 1.0), _fold(2, 1.3, 1.2)],
            mc_aggregation=_mc(1.1, 0.1),
            reference_result=_ref(),  # type: ignore[arg-type]
        )
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=CFG)
        assert verdict.passed
        assert verdict.n_core_indicators == 3
        assert verdict.analysis.overfitting_probability < 0.3

    def test_train_test_collapse_and_mc_dispersion_eliminate(self) -> None:
        evidence = OverfittingEvidence(
            fold_inputs=[_fold(0, 3.0, -0.5), _fold(1, 2.8, 0.2), _fold(2, 3.2, -1.0)],
            mc_aggregation=_mc(0.3, 1.5),
            reference_result=_ref(trade_count=5),  # type: ignore[arg-type]
        )
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=CFG)
        assert not verdict.passed
        assert "overfitting probability" in verdict.failure_reasons[0]
        assert verdict.analysis.indicators.train_test_degradation == pytest.approx(0.5, abs=0.1)
        assert verdict.analysis.indicators.mc_instability == 1.0

    def test_weak_indicators_alone_are_insufficient_evidence(self) -> None:
        # Only low_trade_count is available → 0 core indicators → policy decides.
        evidence = OverfittingEvidence(reference_result=_ref(trade_count=2))  # type: ignore[arg-type]
        verdict = evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=CFG)
        assert verdict.passed
        assert verdict.n_core_indicators == 0
        assert "insufficient overfitting evidence" in verdict.warnings[0]

        strict = OverfittingGateConfig(on_insufficient_evidence="fail")
        assert not evaluate_overfitting_gate(
            strategy_id="s1", evidence=evidence, config=strict
        ).passed

    def test_threshold_is_configurable(self) -> None:
        evidence = OverfittingEvidence(
            fold_inputs=[_fold(0, 2.0, 1.0), _fold(1, 2.0, 0.8)],
            mc_aggregation=_mc(1.0, 0.6),
        )
        prob = evaluate_overfitting_gate(
            strategy_id="s1", evidence=evidence, config=CFG
        ).analysis.overfitting_probability
        tight = OverfittingGateConfig(max_overfitting_probability=max(prob - 0.01, 0.01))
        loose = OverfittingGateConfig(max_overfitting_probability=min(prob + 0.01, 1.0))
        assert not evaluate_overfitting_gate(
            strategy_id="s1", evidence=evidence, config=tight
        ).passed
        assert evaluate_overfitting_gate(strategy_id="s1", evidence=evidence, config=loose).passed

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"max_overfitting_probability": 0.0}, "max_overfitting_probability"),
            ({"min_core_indicators": 0}, "min_core_indicators"),
            ({"min_core_indicators": 5}, "min_core_indicators"),
            ({"on_insufficient_evidence": "maybe"}, "on_insufficient_evidence"),
        ],
    )
    def test_config_validation(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            OverfittingGateConfig(**kwargs)
