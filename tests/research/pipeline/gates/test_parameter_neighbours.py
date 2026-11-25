"""Parameter neighbours: a strategy with one setting moved a little (plan R13).

What a neighbour is, how far a setting moves, and what is left out and said to be.
"""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.pipeline.gates.parameter_neighbours import (
    COMPOSITE_STRUCTURE,
    DOWN,
    UP,
    Neighbour,
    NudgeRule,
    build_neighbours,
    composite_numbers,
)
from autonomous_trading_platform.research.strategy_generation.composite_generation import (
    generate_composite_rule_configs,
)
from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def _momentum(**parameters: float) -> StrategyConfig:
    return make_config(
        "momentum", {"lookback": 10, "buy_above": 0.02, "sell_below": -0.02} | parameters
    )


def _by(neighbours: list[Neighbour]) -> dict[tuple[str, str], Neighbour]:
    return {(n.setting, n.direction): n for n in neighbours}


def _moved_to(neighbours: list[Neighbour], setting: str) -> tuple[float | None, float | None]:
    by = _by(neighbours)
    return by[(setting, DOWN)].value, by[(setting, UP)].value


def test_every_setting_gets_a_neighbour_each_way_in_the_settings_own_order() -> None:
    neighbours = build_neighbours(_momentum())

    assert [(n.setting, n.direction) for n in neighbours] == [
        ("lookback", DOWN),
        ("lookback", UP),
        ("buy_above", DOWN),
        ("buy_above", UP),
        ("sell_below", DOWN),
        ("sell_below", UP),
    ]
    assert all(n.config is not None and n.skipped is None for n in neighbours)


def test_a_window_moves_by_a_fifth_of_itself_and_a_bar_at_least() -> None:
    assert _moved_to(build_neighbours(_momentum(lookback=10)), "lookback") == (8, 12)
    assert _moved_to(build_neighbours(_momentum(lookback=40)), "lookback") == (32, 48)
    # A fifth of 3 bars rounds to one bar, and a fifth of 2 bars to none: one bar.
    assert _moved_to(build_neighbours(_momentum(lookback=3)), "lookback") == (2, 4)
    by = _by(build_neighbours(_momentum(lookback=2)))
    assert (by[("lookback", DOWN)].value, by[("lookback", UP)].value) == (1, 3)
    # It stays a whole number in the strategy that is run.
    moved = by[("lookback", UP)].config
    assert moved is not None and moved.parameters["lookback"] == 3
    assert isinstance(moved.parameters["lookback"], int)


def test_a_number_with_a_declared_range_moves_by_a_tenth_of_the_range() -> None:
    config = make_config("mean_reversion", {"window": 20, "buy_below_z": -2.0, "sell_above_z": 2.0})
    neighbours = build_neighbours(config)

    # buy_below_z is declared from -4.0 to -0.5: a tenth of 3.5.
    assert _moved_to(neighbours, "buy_below_z") == (-2.35, -1.65)
    assert _moved_to(neighbours, "sell_above_z") == (1.65, 2.35)


def test_a_threshold_of_zero_with_a_declared_range_still_moves() -> None:
    """Why the range is the scale and not the value: a share of 0.0 is nothing."""
    config = make_config("momentum", {"lookback": 10, "buy_above": 0.0, "sell_below": -0.02})

    # Declared -0.05 to 0.05 (plan G3): a tenth of 0.1, one step of the research grid.
    assert _moved_to(build_neighbours(config), "buy_above") == (-0.01, 0.01)


def test_the_rule_says_how_far() -> None:
    rule = NudgeRule(window_fraction=0.5, range_fraction=0.25)
    neighbours = build_neighbours(_momentum(lookback=10, buy_above=0.0), rule)

    assert _moved_to(neighbours, "lookback") == (5, 15)
    # buy_above is declared from -0.05 to 0.05: a quarter of 0.1.
    assert _moved_to(neighbours, "buy_above") == (-0.025, 0.025)


@pytest.mark.parametrize("name", ["window_fraction", "range_fraction", "unranged_fraction"])
@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
def test_a_share_is_between_nothing_and_everything(name: str, bad: float) -> None:
    with pytest.raises(ValueError, match=name):
        NudgeRule(**{name: bad})


def test_only_the_one_setting_differs_and_the_strategy_it_came_from_is_untouched() -> None:
    config = _momentum()
    before = config.model_dump()

    neighbours = build_neighbours(config)

    assert config.model_dump() == before
    for neighbour in neighbours:
        assert neighbour.config is not None
        changed = {
            name
            for name, value in neighbour.config.parameters.items()
            if value != config.parameters[name]
        }
        assert changed == {neighbour.setting}
        assert neighbour.config.parameters[neighbour.setting] == neighbour.value
        assert neighbour.base_value == config.parameters[neighbour.setting]


def test_a_neighbour_is_a_strategy_of_its_own_with_its_own_id() -> None:
    """The simulation runner stores a run's config under the run's strategy id: a
    neighbour under the id of the strategy it came from would overwrite that strategy."""
    config = _momentum()
    ids = [n.config.strategy_id for n in build_neighbours(config) if n.config is not None]

    assert config.strategy_id not in ids
    assert len(set(ids)) == len(ids) == 6
    # The id every strategy gets: its type and the hash of its parameters.
    for neighbour in build_neighbours(config):
        assert neighbour.config is not None
        same = make_config("momentum", neighbour.config.parameters)
        assert neighbour.config.strategy_id == same.strategy_id
    # The same strategy has the same neighbours whenever they are built.
    assert [n.config.strategy_id for n in build_neighbours(config) if n.config] == ids


def test_a_strategy_whose_id_is_not_its_hash_still_gets_neighbours_with_ids_of_their_own() -> None:
    named = StrategyConfig(
        strategy_id="steady",
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )

    neighbours = build_neighbours(named)

    assert [n.value for n in neighbours] == [4, 6, 16, 24]
    assert all(
        n.config is not None and n.config.strategy_id.startswith("moving_average_crossover__")
        for n in neighbours
    )


def test_a_move_outside_the_declared_range_is_not_built_and_says_so() -> None:
    by = _by(build_neighbours(_momentum(lookback=60, buy_above=0.05, sell_below=-0.05)))

    assert by[("lookback", DOWN)].config is not None
    over = by[("lookback", UP)]
    assert over.config is None and over.value == 72
    assert over.skipped == "above the declared maximum 60"
    assert by[("buy_above", UP)].skipped == "above the declared maximum 0.05"
    assert by[("sell_below", DOWN)].skipped == "below the declared minimum -0.05"


def test_the_research_floor_on_windows_is_not_a_limit_here() -> None:
    """Research searches no window under 10 bars. The neighbour of a 10-bar lookback is
    8 bars all the same: the floor says what to search, the registry what can run."""
    by = _by(build_neighbours(_momentum(lookback=10)))

    assert by[("lookback", DOWN)].value == 8 and by[("lookback", DOWN)].config is not None


def test_a_move_the_strategy_itself_refuses_is_not_built_and_gives_its_reason() -> None:
    config = make_config("moving_average_crossover", {"short_window": 10, "long_window": 12})
    by = _by(build_neighbours(config))

    assert by[("short_window", DOWN)].config is not None
    assert by[("long_window", UP)].config is not None
    refused = by[("short_window", UP)]
    assert refused.config is None and refused.value == 12
    assert refused.skipped == (
        "not a valid strategy: short_window (12) must be strictly less than long_window (12)"
    )
    assert by[("long_window", DOWN)].skipped is not None


def test_the_factor_strategy_has_ten_settings() -> None:
    neighbours = build_neighbours(make_config("factor_based", {}))

    assert len({n.setting for n in neighbours}) == 10
    assert len(neighbours) == 20


# --------------------------------------------------------------------------------------
# Composites: one nested template, no declared settings
# --------------------------------------------------------------------------------------


def _composite(template: str) -> StrategyConfig:
    configs, _summary = generate_composite_rule_configs(method="grid")
    (config,) = (c for c in configs if c.parameters["metadata"]["generation_template"] == template)
    return config


def test_a_composites_settings_are_its_indicators_windows_and_its_rules_lines() -> None:
    config = _composite("logic_or_dist_50_v2_vol_r_30_hi")
    neighbours = build_neighbours(config)

    assert _moved_to(neighbours, "indicators.dist_50.window") == (40, 60)
    assert _moved_to(neighbours, "indicators.vol_r_30.window") == (24, 36)
    # No declared range: a fifth of the value.
    assert _moved_to(neighbours, "entry_rules.primary_rule.buy_below") == (-2.4, -1.6)
    assert _moved_to(neighbours, "entry_rules.primary_rule.sell_above") == (1.6, 2.4)
    assert _moved_to(neighbours, "entry_rules.vol_rule.buy_above") == (0.96, 1.44)
    assert len(neighbours) == 10 and all(n.config is not None for n in neighbours)


def test_a_composite_neighbour_differs_in_the_one_number_and_keeps_its_wiring() -> None:
    config = _composite("logic_or_dist_50_v2_vol_r_30_hi")
    moved = _by(build_neighbours(config))[("indicators.dist_50.window", UP)].config
    assert moved is not None

    assert moved.parameters["indicators"][0]["parameters"]["window"] == 60
    assert config.parameters["indicators"][0]["parameters"]["window"] == 50
    # The rules still read the indicator by the id it had.
    assert moved.parameters["indicators"][0]["id"] == "dist_50"
    for section in ("entry_rules", "filters", "confirmations", "aggregator"):
        assert moved.parameters[section] == config.parameters[section]
    assert moved.strategy_id != config.strategy_id
    assert moved.strategy_id.startswith("composite_rule__")


def test_a_composites_line_at_zero_has_no_scale_and_is_not_moved() -> None:
    by = _by(build_neighbours(_composite("thresh_roc_10_v0_voti")))

    assert by[("indicators.roc_10.lookback", DOWN)].value == 8
    for direction in (DOWN, UP):
        at_zero = by[("entry_rules.entry.buy_above", direction)]
        assert at_zero.config is None and at_zero.value is None
        assert at_zero.skipped == ("a value of zero with no declared range: no scale to move it by")


def test_the_aggregators_thresholds_are_wiring_and_are_not_moved() -> None:
    """They sit just under the rules' fixed confidences: moved up by a fifth the
    aggregator can no longer be reached, and the strategy never trades.

    The generated pool has no weighted_score composite since plan G5 (one aggregator
    for one-rule skeletons), but stored and hand-written ones can, so one is made here
    from a pool template with the aggregator the pool used to give it."""
    template = dict(_composite("thresh_z_20_v1_voti").parameters)
    template["aggregator"] = {
        "component": "weighted_score",
        "parameters": {"buy_threshold": 0.55, "sell_threshold": -0.55},
    }
    weighted = make_config("composite_rule", template)

    assert not any(n.setting.startswith("aggregator") for n in build_neighbours(weighted))
    for neighbour in build_neighbours(weighted):
        if neighbour.config is not None:
            assert neighbour.config.parameters["aggregator"] == weighted.parameters["aggregator"]


def test_every_number_in_every_template_is_a_setting_or_is_named_as_wiring() -> None:
    """A number added to a template later must be put on one list or the other: it is
    not left unchecked without anybody having decided so."""
    configs, _summary = generate_composite_rule_configs(method="grid")
    assert len(configs) > 350  # 390 since plan G5

    unaccounted: set[str] = set()
    settings: set[str] = set()
    for config in configs:
        for path, is_setting in composite_numbers(config.parameters):
            if is_setting:
                settings.add(path)
            elif path not in COMPOSITE_STRUCTURE:
                unaccounted.add(path)

    assert unaccounted == set()
    assert settings == {
        "indicators.parameters.window",
        "indicators.parameters.lookback",
        "entry_rules.parameters.buy_above",
        "entry_rules.parameters.sell_below",
        "entry_rules.parameters.buy_below",
        "entry_rules.parameters.sell_above",
        "confirmations.parameters.buy_above",
        "confirmations.parameters.sell_below",
        "filters.threshold",
    }
    # No path is on both lists.
    assert settings.isdisjoint(COMPOSITE_STRUCTURE)


def test_every_composite_in_the_pool_has_a_neighbour_to_run() -> None:
    configs, _summary = generate_composite_rule_configs(method="grid")

    for config in configs:
        built = [n for n in build_neighbours(config) if n.config is not None]
        assert built, config.parameters["metadata"]["generation_template"]
        assert len({n.config.strategy_id for n in built if n.config}) == len(built)
