"""The edge, the block shuffle and Benjamini-Hochberg, with the screen's own controls.

The controls are the screen's M1: on made-up data where the answer is known, noise
must not be found and a planted edge must be. The noise is the hard kind: returns move
together within a day, and the condition holds on runs of whole days, so there are far
fewer independent pieces of evidence than there are bars.
"""

from __future__ import annotations

import numpy as np
import pytest

from autonomous_trading_platform.research.signal_screen.statistics import (
    MIN_FIRINGS,
    MIN_GROUP_FIRINGS,
    benjamini_hochberg,
    block_sessions_for,
    name_edges,
    screen_cell,
    shuffle_plan,
)

SESSIONS, SLOTS, NAMES = 120, 6, 8
BLOCK = 5


def _plan(shuffles: int = 400):
    return shuffle_plan(sessions=SESSIONS, block=BLOCK, shuffles=shuffles, seed=11)


def _event_days(rng) -> np.ndarray:
    """Runs of days: a day is an event day with chance 0.2, and tends to stay one."""
    days = np.zeros(SESSIONS, dtype=bool)
    state = False
    for index in range(SESSIONS):
        state = rng.random() < (0.75 if state else 0.0625)
        days[index] = state
    return days


def _noise(rng, planted: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """A condition that holds for every name and slot on event days, and returns that
    share a strong day move. Nothing links the two unless ``planted`` is added."""
    events = _event_days(rng)
    condition = np.broadcast_to(events[:, None, None], (SESSIONS, SLOTS, NAMES)).astype(float)
    day_move = rng.normal(0.0, 1.0, (SESSIONS, 1, 1))
    name_move = rng.normal(0.0, 0.5, (SESSIONS, 1, NAMES))
    returns = day_move + name_move + rng.normal(0.0, 0.3, (SESSIONS, SLOTS, NAMES))
    return condition, returns + planted * condition


def _bar_shuffle_p_value(condition, returns, rng, shuffles: int = 200) -> float:
    """What shuffling single bars would say: every bar its own piece of evidence."""
    fired = condition.ravel() > 0
    values = returns.ravel()
    real = values[fired].mean()
    luck = np.array([rng.permutation(values)[fired].mean() for _ in range(shuffles)])
    return float((1 + (luck >= real).sum()) / (1 + shuffles))


# --------------------------------------------------------------------- the controls


def test_noise_is_found_about_as_often_as_the_p_value_says():
    plan = _plan()
    found = 0
    runs = 300
    for seed in range(runs):
        condition, returns = _noise(np.random.default_rng(1000 + seed))
        result = screen_cell(condition, returns, direction=1, plan=plan)
        if result.tested and result.p_value <= 0.05:
            found += 1
    # 5% is the honest rate; 300 runs put it between 1% and 10% with room to spare.
    assert 0.01 <= found / runs <= 0.10, found / runs


def test_shuffling_single_bars_is_fooled_by_the_same_noise():
    found = 0
    runs = 60
    for seed in range(runs):
        rng = np.random.default_rng(1000 + seed)
        condition, returns = _noise(rng)
        assert condition.any()
        if _bar_shuffle_p_value(condition, returns, rng) <= 0.05:
            found += 1
    # One time in twenty is honest. It calls noise an edge at least three times as
    # often (measured: 16 of these 60), because it counts every bar of a day that
    # moved together as separate evidence.
    assert found / runs > 0.15, found / runs


def test_a_planted_edge_is_found_with_its_size_and_only_in_its_direction():
    plan = _plan()
    found = 0
    edges = []
    runs = 40
    for seed in range(runs):
        condition, returns = _noise(np.random.default_rng(5000 + seed), planted=1.5)
        result = screen_cell(condition, returns, direction=1, plan=plan)
        found += result.p_value <= 0.01
        firing_share = condition.mean()
        edges.append(result.edge / (1.5 * (1 - firing_share)))
        wrong_way = screen_cell(condition, returns, direction=-1, plan=plan)
        assert wrong_way.p_value > 0.5
        assert wrong_way.edge == pytest.approx(-result.edge)
    assert found / runs >= 0.9, found / runs
    # Against other days, which hold event days too, the edge is the planted size
    # times the share of days that are not event days.
    assert np.mean(edges) == pytest.approx(1.0, abs=0.15)


def test_a_condition_that_only_fires_at_a_good_time_of_day_is_not_an_edge():
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0, 0.2, (SESSIONS, SLOTS, NAMES))
    returns[:, 0, :] += 1.0  # the first slot of every day pays, whatever the indicator says
    condition = np.zeros((SESSIONS, SLOTS, NAMES))
    condition[:, 0, :] = rng.random((SESSIONS, NAMES)) < 0.5  # fires at the open only
    result = screen_cell(condition, returns, direction=1, plan=_plan())
    assert result.after_firing - result.all_bars > 0.7  # looks like an edge against all bars
    assert abs(result.edge) < 0.05  # and is none against the same slot on other days
    assert result.p_value > 0.05


# --------------------------------------------------------------------- the cell


def test_the_cell_reports_counts_means_and_hit_rates():
    condition = np.zeros((SESSIONS, SLOTS, NAMES))
    returns = np.full((SESSIONS, SLOTS, NAMES), -0.01)
    condition[::4] = 1.0  # every fourth day
    returns[::4] = 0.03
    returns[1, 0, 0] = np.nan  # not measured
    condition[2, 0, 0] = np.nan  # no indicator value
    result = screen_cell(condition, returns, direction=1, plan=_plan())
    assert result.tested
    assert result.observations == SESSIONS * SLOTS * NAMES - 2
    assert result.firings == 30 * SLOTS * NAMES
    assert result.after_firing == pytest.approx(0.03)
    assert result.all_bars == pytest.approx((30 * 0.03 - 90 * 0.01) / 120, abs=1e-4)
    assert result.hit_rate == 1.0
    assert 0.0 < result.hit_rate_on_other_days < 0.5
    assert result.edge > 0.02 and result.luck_low < 0 < result.luck_high
    assert result.p_value == pytest.approx(1 / (1 + result.shuffles))
    assert result.block_sessions == BLOCK
    # Predicted to fall: the same returns are all misses after a firing, and hits on
    # the other days.
    falls = screen_cell(condition, returns, direction=-1, plan=_plan())
    assert falls.hit_rate == 0.0
    assert falls.hit_rate_on_other_days > 0.5
    assert falls.edge == pytest.approx(-result.edge)
    assert falls.p_value > 0.99


def test_the_edge_is_also_reported_on_named_groups_of_sessions():
    rng = np.random.default_rng(8)
    condition = (rng.random((SESSIONS, SLOTS, NAMES)) < 0.3).astype(float)
    returns = rng.normal(0.0, 0.1, (SESSIONS, SLOTS, NAMES))
    first_half = np.arange(SESSIONS) < SESSIONS // 2
    returns[first_half] += 1.0 * condition[first_half]  # an edge in the first half only
    groups = {"first": first_half, "second": ~first_half, "one_day": np.arange(SESSIONS) == 0}
    quiet = condition.copy()
    quiet[0] = 0.0
    quiet[0, 0, : MIN_GROUP_FIRINGS - 1] = 1.0  # one day with too few firings
    result = screen_cell(quiet, returns, direction=1, plan=_plan(), groups=groups)
    # The condition holds on 3 bars in 10, so the other days its firings are laid on
    # hold the planted return 3 times in 10 too: the edge reads 1.0 x (1 - 0.3).
    assert result.group_edges["first"] == pytest.approx(0.7, abs=0.05)
    assert result.group_edges["second"] == pytest.approx(0.0, abs=0.05)
    assert result.group_edges["one_day"] is None
    # The whole is between its parts, and a fall is the same numbers with the sign turned.
    assert result.group_edges["second"] < result.edge < result.group_edges["first"]
    falls = screen_cell(quiet, returns, direction=-1, plan=_plan(), groups=groups)
    assert falls.group_edges["first"] == pytest.approx(-result.group_edges["first"])
    assert screen_cell(quiet, returns, direction=1, plan=_plan()).group_edges == {}
    with pytest.raises(ValueError, match="one flag per session"):
        screen_cell(quiet, returns, direction=1, plan=_plan(), groups={"bad": first_half[:5]})


def _name_edge_by_hand(condition, returns, name, direction, block):
    """Every pair of sessions at least a block apart, one at a time."""
    sessions = condition.shape[0]
    after, other = [], []
    for a in range(sessions):
        fired = condition[a, :, name] > 0
        after += list(returns[a, fired, name][np.isfinite(returns[a, fired, name])])
        for b in range(sessions):
            apart = abs(a - b)
            if min(apart, sessions - apart) < block:
                continue
            found = returns[b, fired, name]
            other += list(found[np.isfinite(found)])
    return direction * (np.mean(after) - np.mean(other))


def test_each_names_own_edge_matches_the_pairs_counted_by_hand():
    rng = np.random.default_rng(21)
    shape = (40, 4, 5)
    condition = (rng.random(shape) < 0.3).astype(float)
    returns = rng.normal(0.0, 1.0, shape)
    returns[:, :, 1] += 2.0 * condition[:, :, 1]  # an edge in one name
    returns[rng.random(shape) < 0.1] = np.nan
    condition[rng.random(shape) < 0.05] = np.nan
    condition[:, :, 4] = 0.0
    condition[0, 0, 4] = 1.0  # a name that hardly fires
    for block in (1, 3, 7):
        for direction in (1, -1):
            edges = name_edges(condition, returns, direction=direction, block=block)
            assert edges.shape == (5,)
            for name in range(4):
                by_hand = _name_edge_by_hand(condition, returns, name, direction, block)
                assert edges[name] == pytest.approx(by_hand, abs=1e-12)
            assert np.isnan(edges[4])
    edges = name_edges(condition, returns, direction=1, block=3)
    assert edges[1] == pytest.approx(2.0 * (1 - 0.3), abs=0.25)
    assert np.abs(edges[[0, 2, 3]]).max() < 0.5
    # A block as wide as the sessions leaves no other days to compare with.
    assert np.isnan(name_edges(condition, returns, direction=1, block=21)).all()


def test_too_few_firings_are_not_tested():
    condition = np.zeros((SESSIONS, SLOTS, NAMES))
    condition[0, 0, : MIN_FIRINGS - 25] = 1.0
    returns = np.ones((SESSIONS, SLOTS, NAMES))
    result = screen_cell(condition, returns, direction=1, plan=_plan())
    assert not result.tested
    assert result.reason == f"fewer than {MIN_FIRINGS} firings"
    assert result.p_value is None and result.firings == MIN_FIRINGS - 25


def test_firings_on_too_few_sessions_are_not_tested():
    # Every firing on one day: most shuffles land it where little or nothing is measured.
    condition = np.zeros((SESSIONS, SLOTS, NAMES))
    condition[10] = 1.0
    returns = np.full((SESSIONS, SLOTS, NAMES), np.nan)
    returns[10] = 0.01
    returns[11:20] = 0.01
    result = screen_cell(condition, returns, direction=1, plan=_plan())
    assert not result.tested and "too few sessions" in result.reason


def test_the_cell_refuses_mismatched_inputs():
    grid = np.zeros((SESSIONS, SLOTS, NAMES))
    with pytest.raises(ValueError, match="same shape"):
        screen_cell(grid, grid[:, :1], direction=1, plan=_plan())
    with pytest.raises(ValueError, match="direction"):
        screen_cell(grid, grid, direction=0, plan=_plan())
    with pytest.raises(ValueError, match="the plan shuffles"):
        screen_cell(grid[:60], grid[:60], direction=1, plan=_plan())


# --------------------------------------------------------------------- the shuffle


def test_a_plan_moves_whole_blocks_and_keeps_every_session_a_block_away():
    plan = shuffle_plan(sessions=100, block=10, shuffles=200, seed=3)
    base = np.arange(100)
    for pairing in plan.pairings:
        assert sorted(pairing) == list(base)  # every session used once
        apart = np.abs(pairing - base)
        assert np.minimum(apart, 100 - apart).min() >= 10
        # Consecutive sessions stay consecutive except where a block ends.
        steps = np.diff(pairing) % 100
        assert (steps == 1).sum() >= 100 - 10 - 1
    assert plan.shuffles == 200
    assert len({tuple(p) for p in plan.pairings}) == 200  # no two the same


def test_a_plan_is_the_same_for_the_same_seed_and_differs_for_another():
    one = shuffle_plan(sessions=90, block=5, shuffles=20, seed=1)
    again = shuffle_plan(sessions=90, block=5, shuffles=20, seed=1)
    other = shuffle_plan(sessions=90, block=5, shuffles=20, seed=2)
    np.testing.assert_array_equal(one.pairings, again.pairings)
    assert not np.array_equal(one.pairings, other.pairings)


def test_a_plan_holds_each_way_to_shuffle_once_however_many_are_asked_for():
    # Three blocks of 20 in 60 sessions: every session must move 20 to 40 away, which
    # leaves a few dozen ways. Asking for 10,000 does not make more.
    plan = shuffle_plan(sessions=60, block=20, shuffles=10_000, seed=1)
    assert 20 <= plan.shuffles < 200
    assert len({tuple(p) for p in plan.pairings}) == plan.shuffles
    again = shuffle_plan(sessions=60, block=20, shuffles=10_000, seed=2)
    assert {tuple(p) for p in again.pairings} == {tuple(p) for p in plan.pairings}

    # So the smallest p-value such a cell can have is 1 in (ways + 1), not 1 in 10,001.
    condition = np.zeros((60, 4, 5))
    condition[np.random.default_rng(9).random(60) < 0.3] = 1.0  # no pattern a shift repeats
    returns = np.where(condition > 0, 1.0, -1.0)
    result = screen_cell(condition, returns, direction=1, plan=plan)
    assert result.shuffles == plan.shuffles
    assert result.p_value == pytest.approx(1 / (plan.shuffles + 1))
    assert result.p_value > 1 / 200


def test_too_few_sessions_for_the_block_are_refused():
    with pytest.raises(ValueError, match="cannot be shuffled in blocks of 20"):
        shuffle_plan(sessions=59, block=20, shuffles=10, seed=1)
    assert shuffle_plan(sessions=60, block=20, shuffles=10, seed=1).shuffles == 10


def test_the_block_covers_the_horizon_twice_and_the_indicators_memory():
    assert block_sessions_for(horizon_sessions=0, memory_sessions=1) == 5
    assert block_sessions_for(horizon_sessions=5, memory_sessions=2) == 10
    assert block_sessions_for(horizon_sessions=1, memory_sessions=20) == 20
    assert block_sessions_for(horizon_sessions=10, memory_sessions=50) == 50


# --------------------------------------------------------------------- many tests


def test_benjamini_hochberg_on_the_papers_kind_of_example():
    p_values = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    # Thresholds at 5%: 0.005, 0.010, 0.015, ... Only the first two are under theirs.
    assert benjamini_hochberg(p_values, q=0.05) == [True, True] + [False] * 8


def test_benjamini_hochberg_steps_up():
    # 0.02 is above its own threshold (0.0125) and passes because the fourth value is
    # under the fourth threshold (0.05).
    assert benjamini_hochberg([0.04, 0.02, 0.03, 0.021], q=0.05) == [True, True, True, True]
    # With 0.06 last, the third value (0.03, threshold 0.0375) is the largest that
    # passes, and it carries the two below it.
    assert benjamini_hochberg([0.06, 0.02, 0.03, 0.021], q=0.05) == [False, True, True, True]
    # Each above its own threshold (0.0125, 0.025, 0.0375, 0.05): none passes.
    assert benjamini_hochberg([0.06, 0.03, 0.04, 0.026], q=0.05) == [False] * 4


def test_untested_cells_do_not_pass_and_are_not_counted():
    with_gaps = benjamini_hochberg([None, 0.02, float("nan"), 0.04], q=0.05)
    assert with_gaps == [False, True, False, True]  # two tested: thresholds 0.025, 0.05
    # Counted as four tested, 0.02 would have missed its threshold of 0.0125.
    assert benjamini_hochberg([0.9, 0.02, 0.9, 0.04], q=0.05) == [False] * 4
    assert benjamini_hochberg([], q=0.05) == []
    assert benjamini_hochberg([None], q=0.05) == [False]
    with pytest.raises(ValueError, match="between 0 and 1"):
        benjamini_hochberg([0.01], q=1.0)
