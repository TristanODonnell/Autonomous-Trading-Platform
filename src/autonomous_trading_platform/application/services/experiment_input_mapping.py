from __future__ import annotations

from copy import deepcopy
from typing import Any

MAPPING_VERSION = "story-57.v1"

STRATEGY_TYPES = {"momentum", "mean_reversion", "breakout", "pairs"}
RISK_LEVELS = {"low", "medium", "high"}
TIME_HORIZONS = {"1w", "1m", "3m", "1y"}

_RISK_LEVEL_CONFIG: dict[str, dict[str, Any]] = {
    "low": {
        "parameter_grid": {
            "max_position_pct": [0.02, 0.05],
            "stop_loss_pct": [0.02],
            "volatility_target": [0.08],
        },
        "filter_thresholds": {
            "min_sharpe": 1.2,
            "max_drawdown": -0.10,
            "min_trades": 10,
            "min_consistency_score": 0.60,
        },
    },
    "medium": {
        "parameter_grid": {
            "max_position_pct": [0.05, 0.10],
            "stop_loss_pct": [0.03, 0.05],
            "volatility_target": [0.12],
        },
        "filter_thresholds": {
            "min_sharpe": 1.0,
            "max_drawdown": -0.20,
            "min_trades": 20,
            "min_consistency_score": 0.50,
        },
    },
    "high": {
        "parameter_grid": {
            "max_position_pct": [0.10, 0.15],
            "stop_loss_pct": [0.05, 0.08],
            "volatility_target": [0.18],
        },
        "filter_thresholds": {
            "min_sharpe": 0.8,
            "max_drawdown": -0.30,
            "min_trades": 30,
            "min_consistency_score": 0.40,
        },
    },
}

_TIME_HORIZON_CONFIG: dict[str, dict[str, Any]] = {
    "1w": {
        "dataset_version": "research_1w_latest",
        "universe": "liquid_large_cap_25",
        "stage_configuration": {"window_days": 7, "walk_forward_windows": 1},
    },
    "1m": {
        "dataset_version": "research_1m_latest",
        "universe": "liquid_large_cap_50",
        "stage_configuration": {"window_days": 30, "walk_forward_windows": 2},
    },
    "3m": {
        "dataset_version": "research_3m_latest",
        "universe": "liquid_large_cap_100",
        "stage_configuration": {"window_days": 90, "walk_forward_windows": 3},
    },
    "1y": {
        "dataset_version": "research_1y_latest",
        "universe": "liquid_large_cap_250",
        "stage_configuration": {"window_days": 365, "walk_forward_windows": 4},
    },
}


def build_experiment_mapping(
    *,
    strategy_type: str,
    risk_level: str,
    time_horizon: str,
) -> dict[str, Any]:
    if strategy_type not in STRATEGY_TYPES:
        raise ValueError(f"Unsupported strategy_type: {strategy_type}")
    if risk_level not in RISK_LEVELS:
        raise ValueError(f"Unsupported risk_level: {risk_level}")
    if time_horizon not in TIME_HORIZONS:
        raise ValueError(f"Unsupported time_horizon: {time_horizon}")

    risk_config = deepcopy(_RISK_LEVEL_CONFIG[risk_level])
    horizon_config = deepcopy(_TIME_HORIZON_CONFIG[time_horizon])

    return {
        "mapping_version": MAPPING_VERSION,
        "strategy_type": strategy_type,
        "risk_level": risk_level,
        "time_horizon": time_horizon,
        "dataset_version": horizon_config["dataset_version"],
        "universe": horizon_config["universe"],
        "parameter_grid": {
            "strategy_type": [strategy_type],
            **risk_config["parameter_grid"],
        },
        "filter_thresholds": risk_config["filter_thresholds"],
        "stage_configuration": {
            "pipeline": "simulation_walk_forward_filter",
            **horizon_config["stage_configuration"],
        },
    }
