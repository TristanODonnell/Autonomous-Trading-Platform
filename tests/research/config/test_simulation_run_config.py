"""Tests for SimulationRunConfig validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.research.config.simulation_run_config import SimulationRunConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_base() -> dict:
    return {
        "strategy_id": "mac__s10_l30",
        "strategy_type": "moving_average_crossover",
        "parameters": {"short_window": 10, "long_window": 30},
        "dataset_version": "v1",
        "random_seed": 42,
        "price_basis": "adjusted",
        "symbols": ["SPY"],
        "start_date": "2022-01-01",
        "end_date": "2023-01-01",
        "initial_cash": 100_000.0,
    }


# ---------------------------------------------------------------------------
# Valid configs
# ---------------------------------------------------------------------------


def test_valid_base_config():
    cfg = SimulationRunConfig.model_validate(_valid_base())
    assert cfg.strategy_id == "mac__s10_l30"
    assert cfg.strategy_type == "moving_average_crossover"
    assert cfg.symbols == ["SPY"]
    assert cfg.initial_cash == 100_000.0


def test_symbols_normalized_to_uppercase():
    raw = _valid_base()
    raw["symbols"] = ["spy", " aapl "]
    cfg = SimulationRunConfig.model_validate(raw)
    assert cfg.symbols == ["SPY", "AAPL"]


def test_zero_seed_is_valid():
    raw = _valid_base()
    raw["random_seed"] = 0
    cfg = SimulationRunConfig.model_validate(raw)
    assert cfg.random_seed == 0


def test_optional_fields_default():
    cfg = SimulationRunConfig.model_validate(_valid_base())
    assert cfg.experiment_id is None
    assert cfg.strict_data_loading is True
    assert cfg.shuffle_timestamp is False
    assert cfg.window_role is None
    assert cfg.stage_name is None


# ---------------------------------------------------------------------------
# Strategy type validation
# ---------------------------------------------------------------------------


def test_unknown_strategy_type_raises():
    raw = _valid_base()
    raw["strategy_type"] = "not_a_real_strategy"
    with pytest.raises(ValidationError, match="strategy_type"):
        SimulationRunConfig.model_validate(raw)


def test_all_cataloged_strategy_types_accepted():
    from autonomous_trading_platform.strategy.catalog import list_strategy_types

    for strategy_type in list_strategy_types():
        raw = _valid_base()
        raw["strategy_type"] = strategy_type
        raw["parameters"] = {}
        cfg = SimulationRunConfig.model_validate(raw)
        assert cfg.strategy_type == strategy_type


# ---------------------------------------------------------------------------
# Required field failures
# ---------------------------------------------------------------------------


def test_missing_strategy_id_raises():
    raw = _valid_base()
    del raw["strategy_id"]
    with pytest.raises(ValidationError, match="strategy_id"):
        SimulationRunConfig.model_validate(raw)


def test_empty_strategy_id_raises():
    raw = _valid_base()
    raw["strategy_id"] = "   "
    with pytest.raises(ValidationError, match="strategy_id"):
        SimulationRunConfig.model_validate(raw)


def test_missing_dataset_version_raises():
    raw = _valid_base()
    del raw["dataset_version"]
    with pytest.raises(ValidationError, match="dataset_version"):
        SimulationRunConfig.model_validate(raw)


def test_empty_dataset_version_raises():
    raw = _valid_base()
    raw["dataset_version"] = ""
    with pytest.raises(ValidationError, match="dataset_version"):
        SimulationRunConfig.model_validate(raw)


def test_missing_symbols_raises():
    raw = _valid_base()
    del raw["symbols"]
    with pytest.raises(ValidationError, match="symbols"):
        SimulationRunConfig.model_validate(raw)


def test_empty_symbols_raises():
    raw = _valid_base()
    raw["symbols"] = []
    with pytest.raises(ValidationError, match="symbols"):
        SimulationRunConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Date validation
# ---------------------------------------------------------------------------


def test_start_equals_end_raises():
    raw = _valid_base()
    raw["start_date"] = "2022-06-01"
    raw["end_date"] = "2022-06-01"
    with pytest.raises(ValidationError, match="start_date"):
        SimulationRunConfig.model_validate(raw)


def test_start_after_end_raises():
    raw = _valid_base()
    raw["start_date"] = "2023-01-01"
    raw["end_date"] = "2022-01-01"
    with pytest.raises(ValidationError, match="start_date"):
        SimulationRunConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Numeric range validation
# ---------------------------------------------------------------------------


def test_negative_seed_raises():
    raw = _valid_base()
    raw["random_seed"] = -1
    with pytest.raises(ValidationError, match="random_seed"):
        SimulationRunConfig.model_validate(raw)


def test_zero_cash_raises():
    raw = _valid_base()
    raw["initial_cash"] = 0.0
    with pytest.raises(ValidationError, match="initial_cash"):
        SimulationRunConfig.model_validate(raw)


def test_negative_cash_raises():
    raw = _valid_base()
    raw["initial_cash"] = -1000.0
    with pytest.raises(ValidationError, match="initial_cash"):
        SimulationRunConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Invalid enum values
# ---------------------------------------------------------------------------


def test_invalid_price_basis_raises():
    raw = _valid_base()
    raw["price_basis"] = "unadjusted"
    with pytest.raises(ValidationError, match="price_basis"):
        SimulationRunConfig.model_validate(raw)
