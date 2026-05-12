from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class BacktestConfig:
    symbols: list[str]
    start_date: date
    end_date: date
    initial_capital: Decimal = Decimal("100_000.00")
    strategy_id: str = "baseline_strategy"
    # Simple MA crossover parameters
    short_window: int = 5
    long_window: int = 20
    # Fee rate applied to fills (percentage of notional)
    fee_rate: Decimal = Decimal("0.0005")
    # Slippage applied to fill prices (percentage)
    slippage_rate: Decimal = Decimal("0.001")
