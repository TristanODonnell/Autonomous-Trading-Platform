# autonomous_trading_platform/contracts/governance/rotation_dataset.py
"""
Rotation dataset (portfolio rotation step 5).

Everything the offline rotation simulator needs, exported once after a recording
backtest: one full-period re-simulation per strategy that was ever in the pool
(daily equity + fills), when each strategy was available (seeded, admitted to the
bench, retired), whether governance would promote it, the review dates, the market
proxy's daily returns, and the recorded path for validating the simulator.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class RotationFill(BaseModel):
    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    fees: float = 0.0


class RotationStrategySeries(BaseModel):
    strategy_id: str
    strategy_type: str | None = None
    # Approved for paper at the start of the run (seeded), so never needs promotion.
    seeded_approved: bool = False
    # A candidate whose research run passes the paper promotion rule.
    promotable: bool = False
    approval_score: float | None = None
    available_from: date
    retired_on: date | None = None
    # The recording run promoted it (so governance let it through).
    promoted_in_run: bool = False
    # Bar-level equity of the full-period re-simulation (as the re-sims score it).
    equity: list[tuple[datetime, float]] = []
    fills: list[RotationFill] = []
    # Forward record at the platform's own cadence: daily return on allocated capital
    # from the real sleeve while active and the shadow sleeve while on-deck.
    forward_returns: dict[date, float] = {}
    forward_book: dict[date, str] = {}
    # Per day: (closed sells, winning closed sells) across both books.
    forward_closed: dict[date, tuple[int, int]] = {}


class RotationDataset(BaseModel):
    fixture_name: str | None = None
    dataset_version: str | None = None
    start_date: date
    end_date: date
    starting_cash: float
    re_sim_initial_cash: float
    review_dates: list[datetime]
    market_symbol: str | None = None
    market_returns: dict[date, float] = {}
    # Operator settings of the recording run (limits, caps, review guardrails).
    settings: dict[str, Any] = {}
    initial_active: list[str] = []
    strategies: list[RotationStrategySeries] = []
    # Recorded path, for validating the simulator against the real run.
    recorded_rotation: dict[str, Any] | None = None
    recorded_decisions: list[dict[str, Any]] = []
