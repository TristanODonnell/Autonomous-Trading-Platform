"""Rotation step 5c-G: research measures turnover, rejects noise traders and stops
generating candidates with windows under the search floor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig
from autonomous_trading_platform.research.experiments.filtering.filters import apply_filters
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    return_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    risk_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.stability_metrics import (
    stability_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    daily_turnover,
    trade_metrics,
)
from autonomous_trading_platform.research.strategy_generation import composite_generation
from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    MIN_RESEARCH_WINDOW_BARS,
    ParameterSpaceResolver,
)
from autonomous_trading_platform.strategy.registry import get_registry

T0 = datetime(2024, 3, 4, 15, tzinfo=UTC)


def _equity(days: int, equity: float = 100_000.0) -> pd.DataFrame:
    stamps = [T0 + timedelta(days=d, minutes=5 * i) for d in range(days) for i in range(3)]
    return pd.DataFrame(
        {"timestamp": stamps, "equity": [equity * (1 + 0.0001 * i) for i in range(len(stamps))]}
    )


def _round_trips(count: int, notional: float) -> pd.DataFrame:
    rows = []
    for i in range(count):
        ts = T0 + timedelta(minutes=10 * i)
        for side, price in (("buy", 100.0), ("sell", 100.5)):
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": "AAA",
                    "side": side,
                    "quantity": notional / price,
                    "price": price,
                    "fees": 0.0,
                }
            )
    return pd.DataFrame(rows)


def test_daily_turnover_is_notional_per_day_over_mean_equity() -> None:
    equity = _equity(days=2)
    trades = _round_trips(count=10, notional=10_000.0)  # $200k traded over 2 days

    assert daily_turnover(trades, equity) == pytest.approx(
        200_000 / 2 / equity["equity"].mean(), rel=1e-9
    )
    assert trade_metrics(trades, equity).daily_turnover == pytest.approx(
        daily_turnover(trades, equity)
    )
    assert trade_metrics(trades).daily_turnover == 0.0  # no equity curve, no turnover
    assert daily_turnover(trades.iloc[0:0], equity) == 0.0


def test_filter_rejects_turnover_above_the_cap() -> None:
    equity = _equity(days=1)
    churner = _round_trips(count=60, notional=10_000.0)  # ~12x the capital in a day
    holder = _round_trips(count=5, notional=10_000.0)  # ~1x
    config = FilterConfig(
        min_sharpe=-99,
        max_drawdown=-1,
        min_trades=0,
        min_consistency_score=0,
        min_profit_factor=0,
        min_total_return=-1,
        max_daily_turnover=10.0,
    )

    def failures(trades: pd.DataFrame) -> list[str]:
        return apply_filters(
            rm=return_metrics(equity),
            risk=risk_metrics(equity),
            tm=trade_metrics(trades, equity),
            sm=stability_metrics(equity),
            config=config,
            equity_curve=equity,
        ).failures

    assert any("daily_turnover" in f for f in failures(churner))
    assert not any("daily_turnover" in f for f in failures(holder))
    off = FilterConfig(
        min_sharpe=-99,
        max_drawdown=-1,
        min_trades=0,
        min_consistency_score=0,
        min_profit_factor=0,
        min_total_return=-1,
    )
    assert off.max_daily_turnover is None


@pytest.mark.parametrize(
    "strategy_type", ["momentum", "factor_based", "moving_average_crossover", "mean_reversion"]
)
def test_research_never_generates_windows_under_the_floor(strategy_type: str) -> None:
    resolved = ParameterSpaceResolver().resolve(strategy_type)
    specs = {s.name: s for s in get_registry().get_definition(strategy_type).parameter_specs}
    windows = {name: values for name, values in resolved.items() if specs[name].is_window}

    assert windows, "every family has window parameters"
    for name, values in windows.items():
        assert min(values) >= MIN_RESEARCH_WINDOW_BARS, (name, values)


def test_existing_short_window_strategies_stay_valid() -> None:
    """The floor is a research search bound, not validation: approved 1- and 5-bar
    strategies keep running."""
    get_registry().get_definition("momentum").validate_parameters({"lookback": 1})
    get_registry().get_definition("factor_based").validate_parameters({"momentum_lookback": 1})


def test_composite_catalog_has_no_instance_under_the_floor() -> None:
    instances = [
        *composite_generation._ZERO_CENTERED,
        *composite_generation._RATIO,
        *(i for family in composite_generation._CROSSOVER_FAMILIES for i in family),
        *(i for pair in composite_generation._PRICE_COMPARISON_PAIRS for i in pair),
    ]
    assert instances
    for _, _, params in instances:
        for key in ("window", "lookback"):
            if key in params:
                assert params[key] >= MIN_RESEARCH_WINDOW_BARS
