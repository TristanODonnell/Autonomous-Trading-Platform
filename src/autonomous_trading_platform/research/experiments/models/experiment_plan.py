from dataclasses import dataclass
from datetime import date
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis


@dataclass(slots=True)
class ExperimentDefinition:
    experiment_id: str
    description: str | None
    strategy_set: list[dict[str, Any]]
    parameter_grid: list[dict[str, Any]]
    dataset_version: str
    universe_version: str
    price_basis: PriceBasis
    symbols: list[str]
    start_date: date
    end_date: date
    random_seed: int
    initial_cash: float = 100_000.0
