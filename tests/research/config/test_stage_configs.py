"""Tests for pipeline stage config validation models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.research.config.stage_configs import (
    FilterConfigModel,
    MonteCarloStageConfigModel,
    ScoringWeightsModel,
    SimulationStageConfigModel,
    WalkForwardStageConfigModel,
)

# ===========================================================================
# FilterConfigModel
# ===========================================================================


def test_filter_config_defaults():
    fc = FilterConfigModel()
    assert fc.min_sharpe == 1.0
    assert fc.max_drawdown == -0.20
    assert fc.min_trades == 30


def test_filter_config_valid_custom():
    fc = FilterConfigModel(min_sharpe=0.5, max_drawdown=-0.10, min_trades=5)
    assert fc.min_sharpe == 0.5
    assert fc.max_drawdown == -0.10


def test_filter_config_to_dataclass():
    from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig

    fc = FilterConfigModel(min_sharpe=1.5, max_drawdown=-0.15, min_trades=20)
    dc = fc.to_dataclass()
    assert isinstance(dc, FilterConfig)
    assert dc.min_sharpe == 1.5
    assert dc.max_drawdown == -0.15
    assert dc.min_trades == 20


def test_positive_max_drawdown_raises():
    with pytest.raises(ValidationError, match="max_drawdown"):
        FilterConfigModel(max_drawdown=0.20)


def test_zero_max_drawdown_is_valid():
    # 0.0 means "no drawdown allowed" — extreme but semantically valid
    fc = FilterConfigModel(max_drawdown=0.0)
    assert fc.max_drawdown == 0.0


def test_consistency_score_above_one_raises():
    with pytest.raises(ValidationError, match="min_consistency_score"):
        FilterConfigModel(min_consistency_score=1.5)


def test_consistency_score_below_zero_raises():
    with pytest.raises(ValidationError, match="min_consistency_score"):
        FilterConfigModel(min_consistency_score=-0.1)


def test_win_rate_above_one_raises():
    with pytest.raises(ValidationError, match="min_win_rate"):
        FilterConfigModel(min_win_rate=1.1)


def test_negative_profit_factor_raises():
    with pytest.raises(ValidationError, match="min_profit_factor"):
        FilterConfigModel(min_profit_factor=-0.5)


def test_negative_min_trades_raises():
    with pytest.raises(ValidationError, match="min_trades"):
        FilterConfigModel(min_trades=-1)


def test_zero_max_return_variance_raises():
    with pytest.raises(ValidationError, match="max_return_variance"):
        FilterConfigModel(max_return_variance=0.0)


def test_negative_max_return_variance_raises():
    with pytest.raises(ValidationError, match="max_return_variance"):
        FilterConfigModel(max_return_variance=-0.01)


# ===========================================================================
# ScoringWeightsModel
# ===========================================================================


def test_scoring_weights_defaults():
    sw = ScoringWeightsModel()
    assert sw.w_sharpe == 0.4
    assert sw.w_return == 0.3


def test_scoring_weights_to_dataclass():
    from autonomous_trading_platform.research.experiments.filtering.config import ScoringWeights

    sw = ScoringWeightsModel(w_sharpe=0.5, w_return=0.25, w_drawdown=0.15, w_consistency=0.1)
    dc = sw.to_dataclass()
    assert isinstance(dc, ScoringWeights)
    assert dc.w_sharpe == 0.5


def test_negative_weight_raises():
    with pytest.raises(ValidationError, match="weight"):
        ScoringWeightsModel(w_sharpe=-0.1)


def test_zero_weights_allowed():
    sw = ScoringWeightsModel(w_sharpe=0.0, w_return=0.0, w_drawdown=0.0, w_consistency=0.0)
    assert sw.w_sharpe == 0.0


# ===========================================================================
# SimulationStageConfigModel
# ===========================================================================


def _valid_sim_stage() -> dict:
    return {
        "name": "cheap_filter",
        "start_date": "2022-01-01",
        "end_date": "2022-06-30",
        "symbols": ["SPY", "AAPL"],
    }


def test_sim_stage_valid():
    cfg = SimulationStageConfigModel.model_validate(_valid_sim_stage())
    assert cfg.name == "cheap_filter"
    assert len(cfg.symbols) == 2


def test_sim_stage_symbols_uppercase():
    raw = _valid_sim_stage()
    raw["symbols"] = ["spy", "aapl"]
    cfg = SimulationStageConfigModel.model_validate(raw)
    assert cfg.symbols == ["SPY", "AAPL"]


def test_sim_stage_empty_name_raises():
    raw = _valid_sim_stage()
    raw["name"] = ""
    with pytest.raises(ValidationError, match="name"):
        SimulationStageConfigModel.model_validate(raw)


def test_sim_stage_empty_symbols_raises():
    raw = _valid_sim_stage()
    raw["symbols"] = []
    with pytest.raises(ValidationError, match="symbols"):
        SimulationStageConfigModel.model_validate(raw)


def test_sim_stage_start_after_end_raises():
    raw = _valid_sim_stage()
    raw["start_date"] = "2022-12-01"
    raw["end_date"] = "2022-01-01"
    with pytest.raises(ValidationError, match="start_date"):
        SimulationStageConfigModel.model_validate(raw)


def test_sim_stage_start_equals_end_raises():
    raw = _valid_sim_stage()
    raw["start_date"] = "2022-06-01"
    raw["end_date"] = "2022-06-01"
    with pytest.raises(ValidationError, match="start_date"):
        SimulationStageConfigModel.model_validate(raw)


def test_sim_stage_invalid_filter_config_propagates():
    raw = _valid_sim_stage()
    raw["filter_config"] = {"max_drawdown": 0.5}  # positive drawdown — invalid
    with pytest.raises(ValidationError, match="max_drawdown"):
        SimulationStageConfigModel.model_validate(raw)


def test_sim_stage_default_filter_and_weights():
    cfg = SimulationStageConfigModel.model_validate(_valid_sim_stage())
    assert cfg.filter_config.min_sharpe == 1.0
    assert cfg.scoring_weights.w_sharpe == 0.4


# ===========================================================================
# WalkForwardStageConfigModel
# ===========================================================================


def _valid_wf_stage() -> dict:
    return {
        "name": "walk_forward",
        "symbols": ["SPY"],
        "start_date": "2021-01-01",
        "end_date": "2023-01-01",
        "train_days": 180,
        "test_days": 60,
        "step_days": 60,
        "train_filter_config": {},
        "train_scoring_weights": {},
        "test_filter_config": {},
        "test_scoring_weights": {},
    }


def test_wf_stage_valid():
    cfg = WalkForwardStageConfigModel.model_validate(_valid_wf_stage())
    assert cfg.train_days == 180
    assert cfg.test_days == 60
    assert cfg.require_all_folds is True


def test_wf_stage_zero_train_days_raises():
    raw = _valid_wf_stage()
    raw["train_days"] = 0
    with pytest.raises(ValidationError, match="train_days"):
        WalkForwardStageConfigModel.model_validate(raw)


def test_wf_stage_zero_test_days_raises():
    raw = _valid_wf_stage()
    raw["test_days"] = 0
    with pytest.raises(ValidationError, match="test_days"):
        WalkForwardStageConfigModel.model_validate(raw)


def test_wf_stage_zero_step_days_raises():
    raw = _valid_wf_stage()
    raw["step_days"] = 0
    with pytest.raises(ValidationError, match="step_days"):
        WalkForwardStageConfigModel.model_validate(raw)


def test_wf_stage_date_range_too_short_raises():
    raw = _valid_wf_stage()
    raw["start_date"] = "2022-01-01"
    raw["end_date"] = "2022-03-01"  # ~59 days
    raw["train_days"] = 120
    raw["test_days"] = 60
    with pytest.raises(ValidationError, match="train_days"):
        WalkForwardStageConfigModel.model_validate(raw)


def test_wf_stage_start_after_end_raises():
    raw = _valid_wf_stage()
    raw["start_date"] = "2023-01-01"
    raw["end_date"] = "2021-01-01"
    with pytest.raises(ValidationError, match="start_date"):
        WalkForwardStageConfigModel.model_validate(raw)


def test_wf_stage_min_folds_zero_raises():
    raw = _valid_wf_stage()
    raw["min_folds_passed"] = 0
    with pytest.raises(ValidationError, match="min_folds_passed"):
        WalkForwardStageConfigModel.model_validate(raw)


def test_wf_stage_invalid_train_filter_propagates():
    raw = _valid_wf_stage()
    raw["train_filter_config"] = {"max_drawdown": 0.10}  # positive — invalid
    with pytest.raises(ValidationError, match="max_drawdown"):
        WalkForwardStageConfigModel.model_validate(raw)


# ===========================================================================
# MonteCarloStageConfigModel
# ===========================================================================


def _valid_mc_stage() -> dict:
    return {
        "name": "monte_carlo",
        "symbols": ["SPY", "MSFT"],
        "start_date": "2022-01-01",
        "end_date": "2023-01-01",
        "n_runs": 50,
        "min_pass_rate": 0.70,
    }


def test_mc_stage_valid():
    cfg = MonteCarloStageConfigModel.model_validate(_valid_mc_stage())
    assert cfg.n_runs == 50
    assert cfg.min_pass_rate == 0.70


def test_mc_stage_n_runs_one_raises():
    raw = _valid_mc_stage()
    raw["n_runs"] = 1
    with pytest.raises(ValidationError, match="n_runs"):
        MonteCarloStageConfigModel.model_validate(raw)


def test_mc_stage_n_runs_zero_raises():
    raw = _valid_mc_stage()
    raw["n_runs"] = 0
    with pytest.raises(ValidationError, match="n_runs"):
        MonteCarloStageConfigModel.model_validate(raw)


def test_mc_stage_pass_rate_zero_raises():
    raw = _valid_mc_stage()
    raw["min_pass_rate"] = 0.0
    with pytest.raises(ValidationError, match="min_pass_rate"):
        MonteCarloStageConfigModel.model_validate(raw)


def test_mc_stage_pass_rate_above_one_raises():
    raw = _valid_mc_stage()
    raw["min_pass_rate"] = 1.1
    with pytest.raises(ValidationError, match="min_pass_rate"):
        MonteCarloStageConfigModel.model_validate(raw)


def test_mc_stage_pass_rate_exactly_one_is_valid():
    raw = _valid_mc_stage()
    raw["min_pass_rate"] = 1.0
    cfg = MonteCarloStageConfigModel.model_validate(raw)
    assert cfg.min_pass_rate == 1.0


def test_mc_stage_start_after_end_raises():
    raw = _valid_mc_stage()
    raw["start_date"] = "2023-06-01"
    raw["end_date"] = "2022-01-01"
    with pytest.raises(ValidationError, match="start_date"):
        MonteCarloStageConfigModel.model_validate(raw)


def test_mc_stage_empty_symbols_raises():
    raw = _valid_mc_stage()
    raw["symbols"] = []
    with pytest.raises(ValidationError, match="symbols"):
        MonteCarloStageConfigModel.model_validate(raw)


def test_mc_stage_invalid_filter_config_propagates():
    raw = _valid_mc_stage()
    raw["filter_config"] = {"min_win_rate": 2.0}  # > 1.0 — invalid
    with pytest.raises(ValidationError, match="min_win_rate"):
        MonteCarloStageConfigModel.model_validate(raw)


def test_mc_stage_default_n_runs_and_pass_rate():
    raw = _valid_mc_stage()
    del raw["n_runs"]
    del raw["min_pass_rate"]
    cfg = MonteCarloStageConfigModel.model_validate(raw)
    assert cfg.n_runs == 30
    assert cfg.min_pass_rate == 0.70
