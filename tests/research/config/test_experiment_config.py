"""Tests for ExperimentConfig validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.research.config.experiment_config import ExperimentConfig
from autonomous_trading_platform.research.experiments.models.experiment_plan import ExperimentType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_base() -> dict:
    return {
        "experiment_id": "exp_001",
        "experiment_type": "sweep",
        "dataset_version": "v1",
        "price_basis": "adjusted",
        "symbols": ["SPY", "AAPL"],
        "start_date": "2022-01-01",
        "end_date": "2023-01-01",
        "random_seed": 42,
        "initial_cash": 100_000.0,
    }


# ---------------------------------------------------------------------------
# Valid configs
# ---------------------------------------------------------------------------


def test_valid_base_config():
    cfg = ExperimentConfig.model_validate(_valid_base())
    assert cfg.experiment_id == "exp_001"
    assert cfg.experiment_type == ExperimentType.SWEEP
    assert cfg.symbols == ["SPY", "AAPL"]
    assert cfg.random_seed == 42
    assert cfg.initial_cash == 100_000.0


def test_symbols_normalized_to_uppercase():
    raw = _valid_base()
    raw["symbols"] = ["spy", " aapl ", "MSFT"]
    cfg = ExperimentConfig.model_validate(raw)
    assert cfg.symbols == ["SPY", "AAPL", "MSFT"]


def test_valid_time_segmentation():
    raw = _valid_base()
    raw["experiment_type"] = "time_segmentation"
    raw["train_ratio"] = 0.7
    cfg = ExperimentConfig.model_validate(raw)
    assert cfg.train_ratio == 0.7


def test_valid_rolling_window():
    raw = _valid_base()
    raw["experiment_type"] = "rolling_window"
    raw["window_size_days"] = 90
    raw["step_size_days"] = 30
    cfg = ExperimentConfig.model_validate(raw)
    assert cfg.window_size_days == 90
    assert cfg.step_size_days == 30


def test_valid_cross_universe():
    raw = _valid_base()
    raw["experiment_type"] = "cross_universe"
    raw["universe_set"] = [["SPY", "AAPL"], ["QQQ", "MSFT"]]
    cfg = ExperimentConfig.model_validate(raw)
    assert len(cfg.universe_set) == 2


def test_valid_parameter_space():
    raw = _valid_base()
    raw["parameter_space"] = {"short_window": [5, 10, 20], "long_window": [50, 100]}
    cfg = ExperimentConfig.model_validate(raw)
    assert cfg.parameter_space["short_window"] == [5, 10, 20]


def test_to_experiment_definition_roundtrip():
    cfg = ExperimentConfig.model_validate(_valid_base())
    defn = cfg.to_experiment_definition()
    assert defn.experiment_id == cfg.experiment_id
    assert defn.symbols == cfg.symbols
    assert defn.start_date == cfg.start_date
    assert defn.end_date == cfg.end_date
    assert defn.random_seed == cfg.random_seed


# ---------------------------------------------------------------------------
# Required field failures
# ---------------------------------------------------------------------------


def test_missing_experiment_id_raises():
    raw = _valid_base()
    del raw["experiment_id"]
    with pytest.raises(ValidationError, match="experiment_id"):
        ExperimentConfig.model_validate(raw)


def test_missing_dataset_version_raises():
    raw = _valid_base()
    del raw["dataset_version"]
    with pytest.raises(ValidationError, match="dataset_version"):
        ExperimentConfig.model_validate(raw)


def test_missing_symbols_raises():
    raw = _valid_base()
    del raw["symbols"]
    with pytest.raises(ValidationError, match="symbols"):
        ExperimentConfig.model_validate(raw)


def test_missing_start_date_raises():
    raw = _valid_base()
    del raw["start_date"]
    with pytest.raises(ValidationError, match="start_date"):
        ExperimentConfig.model_validate(raw)


def test_missing_end_date_raises():
    raw = _valid_base()
    del raw["end_date"]
    with pytest.raises(ValidationError, match="end_date"):
        ExperimentConfig.model_validate(raw)


def test_missing_price_basis_raises():
    raw = _valid_base()
    del raw["price_basis"]
    with pytest.raises(ValidationError, match="price_basis"):
        ExperimentConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Date validation
# ---------------------------------------------------------------------------


def test_start_equals_end_raises():
    raw = _valid_base()
    raw["start_date"] = "2022-06-01"
    raw["end_date"] = "2022-06-01"
    with pytest.raises(ValidationError, match="start_date"):
        ExperimentConfig.model_validate(raw)


def test_start_after_end_raises():
    raw = _valid_base()
    raw["start_date"] = "2023-01-01"
    raw["end_date"] = "2022-01-01"
    with pytest.raises(ValidationError, match="start_date"):
        ExperimentConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Symbols validation
# ---------------------------------------------------------------------------


def test_empty_symbols_list_raises():
    raw = _valid_base()
    raw["symbols"] = []
    with pytest.raises(ValidationError, match="symbols"):
        ExperimentConfig.model_validate(raw)


def test_whitespace_only_symbols_raises():
    raw = _valid_base()
    raw["symbols"] = ["   ", "  "]
    with pytest.raises(ValidationError, match="symbols"):
        ExperimentConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Numeric range validation
# ---------------------------------------------------------------------------


def test_negative_random_seed_raises():
    raw = _valid_base()
    raw["random_seed"] = -1
    with pytest.raises(ValidationError, match="random_seed"):
        ExperimentConfig.model_validate(raw)


def test_zero_seed_is_valid():
    raw = _valid_base()
    raw["random_seed"] = 0
    cfg = ExperimentConfig.model_validate(raw)
    assert cfg.random_seed == 0


def test_zero_initial_cash_raises():
    raw = _valid_base()
    raw["initial_cash"] = 0.0
    with pytest.raises(ValidationError, match="initial_cash"):
        ExperimentConfig.model_validate(raw)


def test_negative_initial_cash_raises():
    raw = _valid_base()
    raw["initial_cash"] = -500.0
    with pytest.raises(ValidationError, match="initial_cash"):
        ExperimentConfig.model_validate(raw)


def test_empty_experiment_id_raises():
    raw = _valid_base()
    raw["experiment_id"] = "   "
    with pytest.raises(ValidationError, match="experiment_id"):
        ExperimentConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Experiment-type-specific field validation
# ---------------------------------------------------------------------------


def test_time_segmentation_missing_train_ratio_raises():
    raw = _valid_base()
    raw["experiment_type"] = "time_segmentation"
    with pytest.raises(ValidationError, match="train_ratio"):
        ExperimentConfig.model_validate(raw)


def test_time_segmentation_train_ratio_boundary_zero_raises():
    raw = _valid_base()
    raw["experiment_type"] = "time_segmentation"
    raw["train_ratio"] = 0.0
    with pytest.raises(ValidationError, match="train_ratio"):
        ExperimentConfig.model_validate(raw)


def test_time_segmentation_train_ratio_boundary_one_raises():
    raw = _valid_base()
    raw["experiment_type"] = "time_segmentation"
    raw["train_ratio"] = 1.0
    with pytest.raises(ValidationError, match="train_ratio"):
        ExperimentConfig.model_validate(raw)


def test_rolling_window_missing_window_size_raises():
    raw = _valid_base()
    raw["experiment_type"] = "rolling_window"
    raw["step_size_days"] = 30
    with pytest.raises(ValidationError, match="window_size_days"):
        ExperimentConfig.model_validate(raw)


def test_rolling_window_missing_step_size_raises():
    raw = _valid_base()
    raw["experiment_type"] = "rolling_window"
    raw["window_size_days"] = 90
    with pytest.raises(ValidationError, match="step_size_days"):
        ExperimentConfig.model_validate(raw)


def test_rolling_window_zero_window_size_raises():
    raw = _valid_base()
    raw["experiment_type"] = "rolling_window"
    raw["window_size_days"] = 0
    raw["step_size_days"] = 30
    with pytest.raises(ValidationError, match="window_size_days"):
        ExperimentConfig.model_validate(raw)


def test_cross_universe_missing_universe_set_raises():
    raw = _valid_base()
    raw["experiment_type"] = "cross_universe"
    with pytest.raises(ValidationError, match="universe_set"):
        ExperimentConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Parameter space validation
# ---------------------------------------------------------------------------


def test_parameter_space_non_list_value_raises():
    raw = _valid_base()
    raw["parameter_space"] = {"short_window": 10}  # single value, not a list
    with pytest.raises(ValidationError, match="parameter_space"):
        ExperimentConfig.model_validate(raw)


def test_parameter_space_empty_list_raises():
    raw = _valid_base()
    raw["parameter_space"] = {"short_window": []}
    with pytest.raises(ValidationError, match="parameter_space"):
        ExperimentConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Invalid enum values
# ---------------------------------------------------------------------------


def test_invalid_price_basis_raises():
    raw = _valid_base()
    raw["price_basis"] = "invalid_basis"
    with pytest.raises(ValidationError, match="price_basis"):
        ExperimentConfig.model_validate(raw)


def test_invalid_experiment_type_raises():
    raw = _valid_base()
    raw["experiment_type"] = "not_a_real_type"
    with pytest.raises(ValidationError, match="experiment_type"):
        ExperimentConfig.model_validate(raw)
