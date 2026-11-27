"""Is the return after a condition different from what luck gives?

**The edge.** For one condition and one horizon: the mean return after the bars where
the condition held, against the mean the same firing pattern earns on *other days*.

**The shuffle.** Luck is measured by pairing each session's firings with another
session's returns and taking the mean again, many times. Whole sessions are moved, in
blocks of consecutive sessions, the same way for every name, so everything except the
link between the indicator and what followed stays as it was:

- the time of day a condition tends to fire at (a condition that fires at the open is
  compared with other days' opens, not with all bars);
- names firing together on the same day (one market move is one piece of evidence, not
  twenty);
- runs of days (a slow indicator holds for days, and multi-day returns overlap).

Shuffling single bars instead treats every bar as independent evidence and finds
"edges" in noise: ``tests/research/signal_screen/test_statistics.py`` measures it.

A session is never paired with itself or a session nearer than one block, so a real
effect does not leak into the measure of luck.

**The p-value** is the share of shuffles whose mean is at least as far in the predicted
direction as the real one (one-sided: the direction is declared before the run). With
10,000 shuffles the smallest is 1 in 10,001.

**Only distinct shuffles count.** A block as long as a quarter of the history leaves a
few hundred ways to reorder it, not 10,000. A plan holds each way once, so such a cell's
smallest p-value is 1 in a few hundred: drawing the same ways again would claim a
precision the history does not have. Each cell reports how many it had.

**Many tests.** Benjamini-Hochberg over every cell tested: of the cells it passes, about
the share ``q`` are expected to be luck.

How the shuffle is computed: with firings ``F`` and returns ``Y`` as (sessions x
everything else) matrices, ``F @ Y.T`` holds, for every pair of sessions (a, b), the
sum of session b's returns at the places session a fired. Any pairing of sessions is
then a sum of one entry per column, so 10,000 shuffles cost one matrix product.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

# Fewer firings than this (in the data, or in a shuffle): not tested.
MIN_FIRINGS = 30
# A group of sessions (the selection piece, say) with fewer firings has no edge of its own.
MIN_GROUP_FIRINGS = 10
# Blocks are never shorter than a week of sessions.
MIN_BLOCK_SESSIONS = 5
# A plan needs room to move every block at least one block away.
MIN_BLOCKS = 3
# A plan stops looking for another distinct shuffle after this many tries in a row
# find none: the ways to reorder the blocks are used up.
IDLE_TRIES = 20_000

Direction = Literal[1, -1]


def block_sessions_for(*, horizon_sessions: int, memory_sessions: int) -> int:
    """Sessions per block: twice the horizon (returns that overlap stay together) and
    at least the indicator's own memory (the sessions its window reaches back)."""
    return max(MIN_BLOCK_SESSIONS, 2 * horizon_sessions, memory_sessions)


@dataclass(frozen=True)
class ShufflePlan:
    """``pairings[k, b]``: in shuffle k, the session whose firings are paired with
    session b's returns. No two shuffles are the same, so there can be fewer than were
    asked for."""

    sessions: int
    block: int
    seed: int
    pairings: np.ndarray

    @property
    def shuffles(self) -> int:
        return len(self.pairings)


def shuffle_plan(*, sessions: int, block: int, shuffles: int, seed: int) -> ShufflePlan:
    if block < 1 or shuffles < 1:
        raise ValueError("block and shuffles must be positive")
    if sessions < MIN_BLOCKS * block:
        raise ValueError(
            f"{sessions} sessions cannot be shuffled in blocks of {block}: at least "
            f"{MIN_BLOCKS * block} are needed"
        )
    rng = np.random.default_rng([seed, sessions, block])
    base = np.arange(sessions)
    found: dict[bytes, np.ndarray] = {}
    idle = 0
    while len(found) < shuffles and idle < IDLE_TRIES:
        idle += 1
        # Consecutive sessions, starting anywhere, cut into blocks; the blocks reordered.
        rotated = np.roll(base, -int(rng.integers(sessions)))
        blocks = [rotated[start : start + block] for start in range(0, sessions, block)]
        candidate = np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
        apart = np.abs(candidate - base)
        if np.minimum(apart, sessions - apart).min() < block:
            continue
        key = candidate.tobytes()
        if key in found:
            continue
        found[key] = candidate
        idle = 0
    if not found:
        raise RuntimeError(f"no shuffle keeps {sessions} sessions {block} apart")
    pairings = np.array(list(found.values()), dtype=np.int64)
    return ShufflePlan(sessions=sessions, block=block, seed=seed, pairings=pairings)


@dataclass(frozen=True)
class CellResult:
    tested: bool
    # Counted decisions where the indicator has a value and the return was measured.
    observations: int
    firings: int
    # Why it was not tested, when it was not.
    reason: str | None = None
    # Means of the return, as fractions (0.0007 is 7 bp).
    after_firing: float | None = None
    all_bars: float | None = None
    on_other_days: float | None = None
    # In the predicted direction: (after_firing - on_other_days) x direction.
    edge: float | None = None
    # What luck gives, same sign convention: the 2.5th and 97.5th percentile.
    luck_low: float | None = None
    luck_high: float | None = None
    # The largest edge any shuffle gave: an edge above it has the smallest p-value
    # the cell can have.
    luck_top: float | None = None
    p_value: float | None = None
    # Share of returns in the predicted direction: after a firing, and for the same
    # firing pattern on other days.
    hit_rate: float | None = None
    hit_rate_on_other_days: float | None = None
    shuffles: int = 0
    block_sessions: int = 0
    # The edge on named groups of sessions only (same convention); None where a group
    # has too few firings.
    group_edges: dict[str, float | None] = field(default_factory=dict)


def screen_cell(
    condition: np.ndarray,
    returns: np.ndarray,
    *,
    direction: Direction,
    plan: ShufflePlan,
    groups: Mapping[str, np.ndarray] | None = None,
) -> CellResult:
    """``condition``: 1 where it held, 0 where it did not, NaN where the indicator has
    no value. ``returns``: NaN where not measured. Both sessions-first, same shape.
    ``groups``: named sets of sessions (a True per session) to report the edge on."""
    if condition.shape != returns.shape:
        raise ValueError("condition and returns must be the same shape")
    if direction not in (1, -1):
        raise ValueError("direction is 1 (rises) or -1 (falls)")
    sessions = condition.shape[0]
    if sessions != plan.sessions:
        raise ValueError(f"the plan shuffles {plan.sessions} sessions, the data has {sessions}")

    flat_condition = condition.reshape(sessions, -1)
    flat_returns = returns.reshape(sessions, -1)
    defined = np.isfinite(flat_condition)
    measured = np.isfinite(flat_returns)
    fired = (defined & (np.where(defined, flat_condition, 0.0) > 0)).astype(float)
    value = np.where(measured, flat_returns, 0.0)
    counted = measured.astype(float)
    hit = (measured & (direction * value > 0)).astype(float)

    seen = defined & measured
    observations = int(seen.sum())
    firings = int((fired * counted).sum())
    base: dict[str, Any] = {"observations": observations, "firings": firings}
    if firings < MIN_FIRINGS:
        return CellResult(tested=False, reason=f"fewer than {MIN_FIRINGS} firings", **base)

    # [a, b]: session a's firings laid on session b's returns.
    sums = fired @ value.T
    counts = fired @ counted.T
    hits = fired @ hit.T
    columns = np.arange(sessions)

    after_firing = float(sums[columns, columns].sum() / firings)
    # [k, b]: in shuffle k, what the firings paired with session b find there.
    paired_sums = sums[plan.pairings, columns]
    paired_counts = counts[plan.pairings, columns]
    shuffled_counts = paired_counts.sum(axis=1)
    usable = shuffled_counts >= MIN_FIRINGS
    if usable.sum() < plan.shuffles / 2:
        return CellResult(
            tested=False, reason="the firings sit on too few sessions to shuffle", **base
        )
    shuffled = paired_sums.sum(axis=1)[usable] / shuffled_counts[usable]
    shuffled_hits = hits[plan.pairings, columns].sum(axis=1)[usable] / shuffled_counts[usable]

    centre = float(shuffled.mean())
    edge = direction * (after_firing - centre)

    group_edges: dict[str, float | None] = {}
    for name, members in (groups or {}).items():
        members = np.asarray(members, dtype=bool)
        if members.shape != (sessions,):
            raise ValueError(f"group {name!r} must be one flag per session")
        fired_here = counts[columns, columns][members].sum()
        if fired_here < MIN_GROUP_FIRINGS:
            group_edges[name] = None
            continue
        # The group's own returns: after its firings, and under the firings each
        # shuffle pairs with its sessions.
        after = sums[columns, columns][members].sum() / fired_here
        other_counts = paired_counts[:, members].sum(axis=1)[usable]
        other_sums = paired_sums[:, members].sum(axis=1)[usable]
        has = other_counts > 0
        if not has.any():
            group_edges[name] = None
            continue
        other = float((other_sums[has] / other_counts[has]).mean())
        group_edges[name] = float(direction * (after - other))

    luck = direction * (shuffled - centre)
    return CellResult(
        tested=True,
        after_firing=after_firing,
        all_bars=float(value[seen].mean()),
        on_other_days=centre,
        edge=float(edge),
        luck_low=float(np.percentile(luck, 2.5)),
        luck_high=float(np.percentile(luck, 97.5)),
        luck_top=float(luck.max()),
        p_value=float((1 + (luck >= edge).sum()) / (1 + len(luck))),
        hit_rate=float(hits[columns, columns].sum() / firings),
        hit_rate_on_other_days=float(shuffled_hits.mean()),
        shuffles=int(usable.sum()),
        block_sessions=plan.block,
        group_edges=group_edges,
        **base,
    )


def name_edges(
    condition: np.ndarray,
    returns: np.ndarray,
    *,
    direction: Direction,
    block: int,
) -> np.ndarray:
    """The edge of each name on its own (names are the last axis): its mean return
    after its own firings, less what the same firings find on the sessions at least a
    block away, which is what the shuffle pairs them with. NaN for a name with fewer
    than ``MIN_GROUP_FIRINGS`` firings. Exact, no shuffle: the sum over the sessions
    far from each one is the total less a window around it."""
    sessions = condition.shape[0]
    defined = np.isfinite(condition)
    measured = np.isfinite(returns)
    fired = (defined & (np.where(defined, condition, 0.0) > 0)).astype(float)
    value = np.where(measured, returns, 0.0)
    counted = measured.astype(float)

    def far(x: np.ndarray) -> np.ndarray:
        total = x.sum(axis=0, keepdims=True)
        reach = block - 1  # sessions either side that are too near
        if 2 * reach + 1 >= sessions:
            return np.zeros_like(x)
        wrapped = np.concatenate([x[sessions - reach :], x, x[:reach]], axis=0)
        running = np.cumsum(np.concatenate([np.zeros_like(x[:1]), wrapped], axis=0), axis=0)
        near = running[2 * reach + 1 :] - running[:sessions]
        return total - near

    over = tuple(range(condition.ndim - 1))
    firings = (fired * counted).sum(axis=over)
    other_counts = (fired * far(counted)).sum(axis=over)
    with np.errstate(invalid="ignore", divide="ignore"):
        after = (fired * value).sum(axis=over) / firings
        other = (fired * far(value)).sum(axis=over) / other_counts
    edge = direction * (after - other)
    edge[(firings < MIN_GROUP_FIRINGS) | (other_counts <= 0)] = np.nan
    return edge


def benjamini_hochberg(p_values: Sequence[float | None], *, q: float) -> list[bool]:
    """Which p-values pass at false discovery rate ``q``. ``None`` and NaN are cells
    that were not tested: they do not pass and do not count toward the number tested.

    Step-up: with the m tested values sorted, find the largest rank k whose value is at
    most ``k / m * q``; every value of rank k or less passes."""
    if not 0 < q < 1:
        raise ValueError("q must be between 0 and 1")
    tested = [(p, index) for index, p in enumerate(p_values) if p is not None and not math.isnan(p)]
    passed = [False] * len(p_values)
    if not tested:
        return passed
    tested.sort()
    count = len(tested)
    last = 0
    for rank, (p, _) in enumerate(tested, start=1):
        if p <= rank / count * q:
            last = rank
    for _, index in tested[:last]:
        passed[index] = True
    return passed
