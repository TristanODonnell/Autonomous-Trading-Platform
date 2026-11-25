"""Momentum's `measure` setting (research plan G3, Step 1b row 3).

Momentum's thresholds were in dollars and documented as a return, so ten cents was a
different bar for a 20 dollar name and a 500 dollar one. `measure: return` compares the
thresholds with the change as a fraction (rate_of_change); `measure: price`, the default,
keeps the old meaning, and a config that does not name it keeps the id it had: ids hash
the normalised parameters, and Box A's momentum_v1, the baselines and every saved run
are matched by them. Research draws `return` only, over thresholds sized from data.
"""

from __future__ import annotations

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.research.pipeline.gates.parameter_neighbours import (
    build_neighbours,
)
from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    ParameterSpaceResolver,
)
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory
from autonomous_trading_platform.strategy.registry import get_registry
from tests.strategy.composite.test_composite_rule_strategy import _make_bars, _make_context

# Taken before `measure` existed (2026-11-24, main 4419c04cf). `{}` is Box A's
# momentum_v1 (config_json {}); the second is the 2026-11-23 baseline's momentum
# candidate, also pinned in test_stage_gates_on_real_runs.py.
_IDS_BEFORE_MEASURE = {
    (): "momentum__07a1462e7818d286a7460bd17caef68421223aa0f4f027c20117565cfca9609c",
    (10, 0.1, -0.1): "momentum__f84afdd49e7e463aa29a211bf0425d17bdf157f2a7cde251b34d00711a34d9bc",
    (60, 0.0, 0.0): "momentum__c56a9553a9a08c1aa1bfdd7f1c451ef00bd2445fd4606bb2cae52f5338be34e7",
}


def _params(key: tuple) -> dict:
    return dict(zip(("lookback", "buy_above", "sell_below"), key, strict=True)) if key else {}


# -- ids -------------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(_IDS_BEFORE_MEASURE))
def test_a_config_without_measure_keeps_the_id_it_had_before_the_setting(key: tuple) -> None:
    assert make_config("momentum", _params(key)).strategy_id == _IDS_BEFORE_MEASURE[key]


@pytest.mark.parametrize("key", list(_IDS_BEFORE_MEASURE))
def test_naming_the_default_measure_is_the_same_config(key: tuple) -> None:
    named = make_config("momentum", {**_params(key), "measure": "price"})
    assert named.strategy_id == _IDS_BEFORE_MEASURE[key]
    assert "measure" not in named.parameters


def test_a_return_config_is_another_config_and_says_so() -> None:
    config = make_config("momentum", {"measure": "return"})
    assert config.strategy_id != _IDS_BEFORE_MEASURE[()]
    assert config.parameters["measure"] == "return"


def test_an_unknown_measure_is_refused() -> None:
    with pytest.raises(ValueError):
        get_registry().normalize_parameters("momentum", {"measure": "percent"})


# -- behaviour -------------------------------------------------------------------------

# The same 1% rise over 10 bars on a 20 dollar name and a 500 dollar one.
_CHEAP = [20.0] * 15 + [20.0 * (1 + 0.001 * (i + 1)) for i in range(10)]
_DEAR = [500.0] * 15 + [500.0 * (1 + 0.001 * (i + 1)) for i in range(10)]


def _direction(parameters: dict, closes: list[float]) -> SignalDirection | None:
    config = make_config("momentum", {"lookback": 10, **parameters})
    signal = StrategyFactory().build(config).evaluate_symbol(_make_context(_make_bars(closes)))
    return None if signal is None else signal.direction


def test_in_returns_one_threshold_is_the_same_bar_for_a_cheap_and_a_dear_name() -> None:
    rule = {"measure": "return", "buy_above": 0.005, "sell_below": -0.005}
    assert _direction(rule, _CHEAP) == SignalDirection.BUY
    assert _direction(rule, _DEAR) == SignalDirection.BUY
    # Under 1%: neither.
    high = {"measure": "return", "buy_above": 0.015, "sell_below": -0.015}
    assert _direction(high, _CHEAP) is None
    assert _direction(high, _DEAR) is None


def test_in_price_the_same_move_clears_the_bar_only_on_the_dear_name() -> None:
    # Today's meaning, unchanged: 50 cents is 2.5% of 20 dollars and 0.1% of 500.
    rule = {"buy_above": 0.5, "sell_below": -0.5}
    assert _direction(rule, _CHEAP) is None  # moved 0.20 dollars
    assert _direction(rule, _DEAR) == SignalDirection.BUY  # moved 5.00 dollars


def test_a_price_signal_carries_no_measure_so_its_id_does_not_move() -> None:
    # The signal's params feed its id; price signals keep the params they had.
    config = make_config("momentum", {"lookback": 10, "buy_above": 0.5, "sell_below": -0.5})
    signal = StrategyFactory().build(config).evaluate_symbol(_make_context(_make_bars(_DEAR)))
    assert signal is not None and signal.params is not None
    assert "measure" not in signal.params

    returns = make_config("momentum", {"lookback": 10, "measure": "return", "buy_above": 0.005})
    signal = StrategyFactory().build(returns).evaluate_symbol(_make_context(_make_bars(_DEAR)))
    assert signal is not None and signal.params is not None
    assert signal.params["measure"] == "return"


# -- research --------------------------------------------------------------------------


def test_research_draws_returns_over_the_measured_grid() -> None:
    grid = [-0.02, -0.01, 0.0, 0.01, 0.02]
    assert ParameterSpaceResolver().resolve("momentum", {}) == {
        "buy_above": grid,
        "lookback": [10, 60],
        "measure": ["return"],
        "sell_below": grid,
    }


def test_the_search_grid_is_not_a_validation_rule() -> None:
    # A stored or hand-written config outside the grid (and the declared range) loads.
    config = make_config("momentum", {"lookback": 10, "buy_above": 0.1, "sell_below": -0.1})
    assert config.strategy_id == _IDS_BEFORE_MEASURE[(10, 0.1, -0.1)]


def test_the_neighbour_check_moves_numbers_one_grid_step_and_keeps_the_measure() -> None:
    config = make_config(
        "momentum", {"lookback": 10, "measure": "return", "buy_above": 0.01, "sell_below": -0.01}
    )
    neighbours = build_neighbours(config)

    assert {n.setting for n in neighbours} == {"lookback", "buy_above", "sell_below"}
    moved = {(n.setting, n.direction): n.value for n in neighbours}
    assert moved[("buy_above", "down")] == pytest.approx(0.0)
    assert moved[("buy_above", "up")] == pytest.approx(0.02)
    for n in neighbours:
        assert n.config is not None
        assert n.config.parameters["measure"] == "return"
