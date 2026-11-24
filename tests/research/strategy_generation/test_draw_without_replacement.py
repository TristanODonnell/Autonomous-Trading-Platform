"""The random draw takes distinct valid configs until it has the number asked for or the
space has none left (plan G2, Step 1b).

Before G2 the random generator made `n_samples` independent draws: a draw that broke its
family's rule was thrown away and not replaced, and two draws could be the same config,
so a family returned fewer than asked (momentum 4 of 6 and crossover 2 of 6 on the
2026-11-23 baseline).
"""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
)
from autonomous_trading_platform.research.strategy_generation.strategy_generation_engine import (
    StrategyGenerationEngine,
)


def _draw(strategy_type: str, n: int, seed: int = 42):
    return StrategyGenerationEngine().generate_result(
        strategy_type,
        method="random",
        parameter_space={},
        options=GenerationOptions(seed=seed, n_samples=n),
    )


def _hashes(result) -> list[str]:
    return [config.config_hash() for config in result.configs]


def test_momentum_gets_the_number_asked_for_although_some_draws_break_its_rule() -> None:
    # Default space: lookback {10, 60}, buy_above and sell_below {-0.1, 0, 0.1}; a draw
    # with sell_below above buy_above breaks the rule. 12 of the 18 combinations pass.
    result = _draw("momentum", 6)

    assert len(result.configs) == 6
    assert len(set(_hashes(result))) == 6
    assert result.summary.requested_count == 6
    assert result.summary.exhausted is False
    for config in result.configs:
        assert config.parameters["sell_below"] <= config.parameters["buy_above"]


def test_momentum_asked_for_more_than_its_valid_settings_returns_them_all_and_says_so() -> None:
    result = _draw("momentum", 13)

    assert len(result.configs) == 12
    assert len(set(_hashes(result))) == 12
    assert result.summary.rejected_count == 6
    assert result.summary.exhausted is True


def test_crossover_returns_its_three_valid_settings_when_six_are_asked_for() -> None:
    # Default space: short_window {10, 50}, long_window {10, 30, 200}; the short window
    # must be below the long one, which leaves (10, 30), (10, 200), (50, 200).
    result = _draw("moving_average_crossover", 6)

    pairs = {(c.parameters["short_window"], c.parameters["long_window"]) for c in result.configs}
    assert pairs == {(10, 30), (10, 200), (50, 200)}
    assert result.summary.rejected_count == 3
    assert result.summary.duplicate_count == 0
    assert result.summary.exhausted is True


@pytest.mark.parametrize(
    ("strategy_type", "n"),
    [("momentum", 6), ("mean_reversion", 6), ("moving_average_crossover", 6), ("factor_based", 8)],
)
def test_a_draw_is_reproducible_by_seed_and_never_repeats(strategy_type: str, n: int) -> None:
    first = _draw(strategy_type, n, seed=202611)
    again = _draw(strategy_type, n, seed=202611)

    assert _hashes(first) == _hashes(again)
    assert len(set(_hashes(first))) == len(first.configs)
    assert first.summary.duplicate_count == 0


@pytest.mark.parametrize("strategy_type", ["momentum", "mean_reversion", "factor_based"])
def test_another_seed_draws_another_set(strategy_type: str) -> None:
    november = set(_hashes(_draw(strategy_type, 6, seed=202611)))
    december = set(_hashes(_draw(strategy_type, 6, seed=202612)))

    assert november != december


def test_composite_draw_gets_the_number_asked_for_and_reports_it() -> None:
    result = _draw("composite_rule", 10)

    assert len(result.configs) == 10
    assert len(set(_hashes(result))) == 10
    assert result.summary.requested_count == 10
    assert result.summary.exhausted is False


def test_composite_draw_larger_than_the_pool_returns_the_pool_and_says_so() -> None:
    pool = StrategyGenerationEngine().generate_result(
        "composite_rule", method="grid", parameter_space={}
    )
    result = _draw("composite_rule", len(pool.configs) + 5)

    assert set(_hashes(result)) == set(_hashes(pool))
    assert result.summary.exhausted is True


def test_composite_draw_is_the_first_n_of_the_shuffled_pool_when_nothing_is_rejected() -> None:
    # Taking the whole shuffled pool and stopping at n gives the same skeletons the old
    # slice of the first n gave, as long as none fails validation (all 437 pass today).
    ten = _draw("composite_rule", 10, seed=7)
    twenty = _draw("composite_rule", 20, seed=7)

    assert _hashes(twenty)[:10] == _hashes(ten)
