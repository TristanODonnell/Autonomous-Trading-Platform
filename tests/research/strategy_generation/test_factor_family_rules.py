"""The factor family's rule and canonical form in the research draw (plan G4, G8; Step 1b
row 4). The baseline's configs named here are read from Box B's run manifests.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from autonomous_trading_platform.research.strategy_generation import family_rules
from autonomous_trading_platform.research.strategy_generation.family_rules import (
    factor_reachable_scores,
    research_form,
)
from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    ParameterSpaceResolver,
)
from autonomous_trading_platform.research.strategy_generation.strategy_generation_engine import (
    StrategyGenerationEngine,
)
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory
from autonomous_trading_platform.strategy.implementations.factor_based_strategy import (
    FactorBasedStrategy,
)
from tests.utilities.factories import make_five_minute_bar


def _factor(**p: float) -> dict:
    return {k: (int(v) if k.endswith(("lookback", "window")) else float(v)) for k, v in p.items()}


# The 2026-11-23 baseline's factor configs (Box B, run_manifests.strategy_config).
ZERO_TRADES_0A2D = _factor(
    momentum_weight=0, mean_reversion_weight=0, volume_weight=0, volatility_weight=0,
    momentum_lookback=60, mean_reversion_window=10, volume_window=100, volatility_window=100,
    buy_score_threshold=1.0, sell_score_threshold=-1.0,
)  # fmt: skip
ZERO_TRADES_8C07 = _factor(
    momentum_weight=1.0, mean_reversion_weight=0, volume_weight=0.2, volatility_weight=1.0,
    momentum_lookback=10, mean_reversion_window=100, volume_window=10, volatility_window=100,
    buy_score_threshold=1.0, sell_score_threshold=-0.1,
)  # fmt: skip
SAME_4FA1 = _factor(
    momentum_weight=0, mean_reversion_weight=1.0, volume_weight=0, volatility_weight=0,
    momentum_lookback=60, mean_reversion_window=10, volume_window=100, volatility_window=10,
    buy_score_threshold=1.0, sell_score_threshold=-1.0,
)  # fmt: skip
SAME_8919 = _factor(
    momentum_weight=0, mean_reversion_weight=1.0, volume_weight=0, volatility_weight=0,
    momentum_lookback=10, mean_reversion_window=10, volume_window=10, volatility_window=20,
    buy_score_threshold=0.5, sell_score_threshold=-0.1,
)  # fmt: skip
SURVIVOR_E7D4 = _factor(
    momentum_weight=0.4, mean_reversion_weight=0.3, volume_weight=1.0, volatility_weight=0.1,
    momentum_lookback=10, mean_reversion_window=10, volume_window=20, volatility_window=20,
    buy_score_threshold=0.1, sell_score_threshold=-1.0,
)  # fmt: skip


# -- the rule (G4) ---------------------------------------------------------------------


@pytest.mark.parametrize("params", [ZERO_TRADES_0A2D, ZERO_TRADES_8C07], ids=["0a2d", "8c07"])
def test_the_baselines_zero_trade_configs_are_refused(params: dict) -> None:
    with pytest.raises(ValueError, match="can never buy"):
        research_form("factor_based", params)


def test_8c07_is_caught_because_volatility_cannot_score_zero_there() -> None:
    # Momentum 1.0 + volume 0.2 = 1.2 would reach the threshold of 1.0; volatility
    # subtracts 0.25 whenever price moves, and its window is not shorter than mean
    # reversion's, so the flat case gives no signal at all: the top is 0.95.
    assert max(factor_reachable_scores(ZERO_TRADES_8C07)) == pytest.approx(0.95)
    shorter = {**ZERO_TRADES_8C07, "volatility_window": 20}  # 20 < 100: flat 20 bars count
    assert max(factor_reachable_scores(shorter)) == pytest.approx(1.2)
    # But the draw reads the config as it will run: mean reversion has weight 0, so its
    # window goes to the floor (10), 20 is no longer shorter, and the top is 0.95 again.
    with pytest.raises(ValueError, match="can never buy"):
        research_form("factor_based", shorter)


def test_a_refused_config_is_drawn_again_and_counted() -> None:
    for month in (202611, 202612):
        result = StrategyGenerationEngine().generate_result(
            "factor_based",
            method="random",
            parameter_space={},
            options={"n_samples": 8, "seed": month},
        )
        assert len(result.configs) == 8
        assert result.summary.rejected_count > 0
        for config in result.configs:
            research_form("factor_based", config.parameters)  # every one can buy


def test_stored_and_hand_written_configs_are_not_touched() -> None:
    # The rule is the draw's: the schema still takes the config, under the id it had.
    config = make_config("factor_based", ZERO_TRADES_0A2D)
    assert config.strategy_id.startswith("factor_based__0a2d5aed3879")


# -- the canonical form (G8) -----------------------------------------------------------


def test_4fa1_and_8919_are_one_config() -> None:
    a = make_config("factor_based", research_form("factor_based", SAME_4FA1))
    b = make_config("factor_based", research_form("factor_based", SAME_8919))
    assert a.strategy_id == b.strategy_id
    assert a.parameters["buy_score_threshold"] == 1.0
    assert a.parameters["sell_score_threshold"] == -1.0


def test_zero_weight_windows_go_to_the_floor_and_weighted_ones_stay() -> None:
    out = research_form("factor_based", SAME_4FA1)
    assert (out["momentum_lookback"], out["volume_window"], out["volatility_window"]) == (
        10,
        10,
        10,
    )
    assert out["mean_reversion_window"] == 10  # weighted: as drawn
    kept = research_form("factor_based", SURVIVOR_E7D4)
    for window in (
        "momentum_lookback",
        "mean_reversion_window",
        "volume_window",
        "volatility_window",
    ):
        assert kept[window] == SURVIVOR_E7D4[window]


def test_moving_a_threshold_to_its_reachable_score_picks_the_same_scores() -> None:
    """The exactness the threshold step rests on, over random weights and thresholds:
    for every score the config can give, buy and sell decide alike before and after."""
    rng = random.Random(20261124)
    space = ParameterSpaceResolver().resolve("factor_based", {})
    checked = 0
    for _ in range(2000):
        params = {k: rng.choice(v) for k, v in space.items()}
        params["buy_score_threshold"] = round(rng.uniform(-0.5, 2.5), 3)
        params["sell_score_threshold"] = round(
            rng.uniform(-2.5, params["buy_score_threshold"] - 0.01), 3
        )
        try:
            out = research_form("factor_based", params)
        except ValueError:
            continue
        for s in factor_reachable_scores(out):
            assert (s >= params["buy_score_threshold"]) == (s >= out["buy_score_threshold"])
            assert (s <= params["sell_score_threshold"]) == (s <= out["sell_score_threshold"])
        checked += 1
    assert checked > 1000


def test_configs_that_never_sell_share_one_sell_threshold() -> None:
    a = research_form("factor_based", {**SURVIVOR_E7D4, "sell_score_threshold": -9.0})
    b = research_form("factor_based", {**SURVIVOR_E7D4, "sell_score_threshold": -5.0})
    assert a == b
    assert a["sell_score_threshold"] < min(factor_reachable_scores(a))


def test_two_draws_of_one_behaviour_are_one_candidate_and_a_repeat() -> None:
    # The random generator's repeat check (plan G2), seen for the first time: two buy
    # thresholds that pick the same scores of a mean-reversion-only config.
    fixed = {k: [v] for k, v in SAME_4FA1.items() if k != "buy_score_threshold"}
    result = StrategyGenerationEngine().generate_result(
        "factor_based",
        method="random",
        parameter_space={**fixed, "buy_score_threshold": [0.5, 1.0]},
        options={"n_samples": 2, "seed": 1},
    )
    assert len(result.configs) == 1
    assert result.summary.duplicate_count == 1
    assert result.summary.exhausted is True


def test_the_grid_is_12336_configs() -> None:
    # The size of task 4's factor grid: 39,366 combinations, 8,208 that can never buy,
    # the rest in 12,336 canonical forms.
    result = StrategyGenerationEngine().generate_result(
        "factor_based", method="grid", parameter_space={}
    )
    assert len(result.configs) == 12336
    assert result.summary.rejected_count == 8208


def test_other_families_pass_through() -> None:
    params = {"lookback": 10, "buy_above": 0.01, "sell_below": -0.01, "measure": "return"}
    assert research_form("momentum", params) is params


# -- the strategy itself ---------------------------------------------------------------


def test_the_part_scores_are_all_the_strategy_gives() -> None:
    rng = random.Random(7)
    xs = [rng.uniform(-5, 5) for _ in range(5000)] + [-2.0, 2.0, 0.0, 0.5, 1.5]
    assert {FactorBasedStrategy._normalize_signed(x) for x in xs} <= set(
        family_rules._MOMENTUM_SCORES
    )
    assert {FactorBasedStrategy._score_mean_reversion(x) for x in xs} <= set(
        family_rules._MEAN_REVERSION_SCORES
    )
    assert {FactorBasedStrategy._score_volume(abs(x)) for x in xs} <= set(
        family_rules._VOLUME_SCORES
    )
    assert {FactorBasedStrategy._score_volatility(abs(x)) for x in xs} <= {
        family_rules._VOLATILITY_SCORE_MOVING,
        family_rules._VOLATILITY_SCORE_FLAT,
    }


def _walk(seed: int, n: int = 400) -> list:
    rng = random.Random(seed)
    price, bars, t0 = 100.0, [], datetime(2026, 4, 1, 13, 30, tzinfo=UTC)
    for i in range(n):
        price = max(1.0, price * (1 + rng.gauss(0, 0.004)))
        bars.append(
            make_five_minute_bar(
                timestamp=t0 + timedelta(minutes=5 * i),
                close_price=f"{price:.2f}", open_price=f"{price:.2f}",
                high_price=f"{price:.2f}", low_price=f"{price:.2f}",
                volume=int(rng.lognormvariate(8, 0.8)),
            )
        )  # fmt: skip
    return bars


def _directions(params: dict, bars: list) -> list:
    strategy = StrategyFactory().build(make_config("factor_based", params))
    out = []
    for i in range(150, len(bars)):
        window = bars[i - 149 : i + 1]
        ts = window[-1].timestamp + timedelta(minutes=5)
        context = StrategyContext(
            run_id=UUID(int=1), strategy_id="g8", symbol="AAPL",
            evaluation_timestamp=ts, bar_timestamp=window[-1].timestamp, bars=window,
        )  # fmt: skip
        signal = strategy.evaluate_symbol(context)
        out.append(None if signal is None else signal.direction)
    return out


@pytest.mark.parametrize(
    "params", [SURVIVOR_E7D4, SAME_4FA1, SAME_8919], ids=["e7d4", "4fa1", "8919"]
)
def test_the_canonical_config_trades_as_the_drawn_one(params: dict) -> None:
    for seed in (1, 2, 3):
        bars = _walk(seed)
        drawn = _directions(params, bars)
        assert any(d is not None for d in drawn)
        assert _directions(research_form("factor_based", params), bars) == drawn
