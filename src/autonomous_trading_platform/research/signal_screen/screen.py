"""Every row of a library, at every horizon of its scale, against luck.

A **cell** is one row at one horizon on one kind of return:

- ``excess``: the name's return less the mean return of the names in force over the
  same interval. This is the question the screen is for: does the row pick *which*
  name, or *when* to hold one, beyond holding them all.
- ``raw``: the name's own return, beside it, to show what a strategy would have seen.

Each cell gets an edge, the range luck gives, and a one-sided p-value from the block
shuffle (``statistics.py``). Benjamini-Hochberg then runs once over every cell tested,
so the number of rows, horizons and kinds of return tried is paid for in one place.

Nothing here reads a file or a database: it is handed panels (``panel.py``) and returns
a plain dict a report or a JSON file is written from.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .bars import SLOTS_PER_SESSION
from .indicators import flat_windows
from .library import Library, Row
from .panel import HORIZONS, Horizon, Panel, Scale, forward_grid
from .reading import read_into_g10
from .statistics import (
    MIN_FIRINGS,
    CellResult,
    ShufflePlan,
    benjamini_hochberg,
    block_sessions_for,
    name_edges,
    screen_cell,
    shuffle_plan,
)

RETURN_KINDS = ("excess", "raw")


@dataclass(frozen=True)
class ScreenSettings:
    # At most: a cell has as many as there are distinct ways to shuffle its blocks.
    shuffles: int = 10_000
    seed: int = 20261127
    # False discovery rate: of the cells that pass, about this share are luck.
    q: float = 0.10
    # In and out at 1 bp of notional a side, what the simulator charges.
    round_trip_cost: float = 0.0002


def memory_sessions(row: Row) -> int:
    """How many sessions back the row's slowest reading reaches."""
    if row.scale == "5min":
        return math.ceil(row.bars / SLOTS_PER_SESSION)
    return row.bars


def _bp(value: float | None) -> float | None:
    return None if value is None else round(value * 1e4, 4)


def _counts(condition: np.ndarray, returns: np.ndarray) -> tuple[int, int]:
    measured = np.isfinite(returns)
    defined = np.isfinite(condition)
    fired = defined & (np.nan_to_num(condition) > 0)
    return int((defined & measured).sum()), int((fired & measured).sum())


def _cell(
    row: Row,
    horizon: Horizon,
    kind: str,
    result: CellResult,
    names: np.ndarray | None,
    cost: float,
) -> dict[str, Any]:
    measured = int(np.isfinite(names).sum()) if names is not None else 0
    positive = int((names[np.isfinite(names)] > 0).sum()) if names is not None else 0
    return {
        "row": row.id,
        "scale": row.scale,
        "horizon": horizon.key,
        "horizon_label": horizon.label,
        "returns": kind,
        "tested": result.tested,
        "reason": result.reason,
        "observations": result.observations,
        "firings": result.firings,
        "firing_share": (
            round(result.firings / result.observations, 6) if result.observations else None
        ),
        "after_firing_bp": _bp(result.after_firing),
        "on_other_days_bp": _bp(result.on_other_days),
        "all_bars_bp": _bp(result.all_bars),
        "edge_bp": _bp(result.edge),
        "luck_low_bp": _bp(result.luck_low),
        "luck_high_bp": _bp(result.luck_high),
        "luck_top_bp": _bp(result.luck_top),
        "p_value": result.p_value,
        "passes": False,
        "edge_less_cost_bp": _bp(result.edge - cost) if result.edge is not None else None,
        "hit_rate": result.hit_rate,
        "hit_rate_on_other_days": result.hit_rate_on_other_days,
        "edge_by_piece_bp": {name: _bp(edge) for name, edge in result.group_edges.items()},
        "names_measured": measured,
        "names_positive": round(positive / measured, 4) if measured else None,
        "block_sessions": result.block_sessions,
        "shuffles": result.shuffles,
    }


def _row_facts(row: Row, panel: Panel, condition: np.ndarray) -> dict[str, Any]:
    """What the row's condition looks like before any return is read."""
    flat = 0
    for reading in row.readings:
        if reading.indicator != "z_score":
            continue
        window = int(dict(reading.parameters)["window"])
        for decisions, counted in zip(panel.decisions, panel.counted, strict=True):
            flat += int(flat_windows(decisions.indicator_input, window=window)[counted].sum())
    return {
        **row.describe(),
        "decisions_with_value": int(np.isfinite(condition).sum()),
        "decisions_held": int((np.nan_to_num(condition) > 0).sum()),
        # z-score windows that did not move: left out (see indicators.py).
        "flat_windows": flat,
    }


def run_screen(
    *,
    panels: Mapping[Scale, Panel],
    library: Library,
    groups: Mapping[str, np.ndarray] | None = None,
    settings: ScreenSettings | None = None,
    horizons: Mapping[Scale, Sequence[Horizon]] = HORIZONS,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """``groups``: named sets of observed sessions (one flag per session of a grid) to
    report every edge on, the selection and the evaluation piece for one. A scale the
    library has rows on and no panel is given for is not run, and the report says so."""
    settings = settings or ScreenSettings()
    groups = dict(groups or {})
    plans: dict[tuple[int, int], ShufflePlan | str] = {}

    def plan_for(sessions: int, block: int) -> ShufflePlan | str:
        key = (sessions, block)
        if key not in plans:
            try:
                plans[key] = shuffle_plan(
                    sessions=sessions, block=block, shuffles=settings.shuffles, seed=settings.seed
                )
            except (ValueError, RuntimeError) as exc:
                plans[key] = str(exc)
        return plans[key]

    scales: dict[str, Any] = {}
    rows_out: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for scale, panel in panels.items():
        rows = library.on_scale(scale)
        if not rows:
            continue
        sessions = panel.shape[0]
        for name, flags in groups.items():
            if np.asarray(flags).shape != (sessions,):
                raise ValueError(f"group {name!r} must be one flag per observed session")
        grids = {horizon.key: forward_grid(panel, horizon) for horizon in horizons[scale]}
        scales[scale] = {
            "sessions": sessions,
            "slots": panel.slots,
            "names": len(panel.symbols),
            "symbols": list(panel.symbols),
            "decisions_counted": int(sum(int(counted.sum()) for counted in panel.counted)),
            "horizons": [
                {
                    "key": horizon.key,
                    "label": horizon.label,
                    "measured": grids[horizon.key].measured,
                    "stale": grids[horizon.key].stale,
                }
                for horizon in horizons[scale]
            ],
        }
        for index, row in enumerate(rows, start=1):
            if progress is not None:
                progress(f"{scale} {index}/{len(rows)} {row.id}")
            condition = panel.grid(
                [row.column(decisions.indicator_input) for decisions in panel.decisions]
            )
            rows_out.append(_row_facts(row, panel, condition))
            for horizon in horizons[scale]:
                block = block_sessions_for(
                    horizon_sessions=horizon.sessions_spanned,
                    memory_sessions=memory_sessions(row),
                )
                plan = plan_for(sessions, block)
                for kind in RETURN_KINDS:
                    returns = getattr(grids[horizon.key], kind)
                    names = None
                    if isinstance(plan, str):
                        observations, firings = _counts(condition, returns)
                        result = CellResult(
                            tested=False, observations=observations, firings=firings, reason=plan
                        )
                    else:
                        result = screen_cell(
                            condition, returns, direction=row.direction, plan=plan, groups=groups
                        )
                        if result.tested:
                            names = name_edges(
                                condition, returns, direction=row.direction, block=block
                            )
                    cells.append(_cell(row, horizon, kind, result, names, settings.round_trip_cost))

    passes = benjamini_hochberg([cell["p_value"] for cell in cells], q=settings.q)
    for cell, passed in zip(cells, passes, strict=True):
        cell["passes"] = passed

    # Marks each cell a finding or not, by the rule fixed before the run.
    reading = read_into_g10(
        cells=cells, rows=rows_out, cost_bp=settings.round_trip_cost * 1e4, pieces=groups
    )

    rows_run = len(rows_out)
    return {
        "library": {
            "name": library.name,
            "hash": library.content_hash(),
            "rows": len(library.rows),
            "rows_run": rows_run,
            # False when a scale was left out: the result is then a rehearsal, not the
            # run the library was declared for.
            "complete": rows_run == len(library.rows),
        },
        "settings": {
            **asdict(settings),
            # False when a run changed the shuffles, the seed, the rate or the cost.
            "declared": settings == ScreenSettings(),
            "min_firings": MIN_FIRINGS,
            "returns": list(RETURN_KINDS),
            "p_value": "one-sided, in the direction each row declares",
            "pieces": sorted(groups),
        },
        "scales": scales,
        "rows": rows_out,
        "cells": cells,
        "summary": _summary(cells, settings.q),
        "reading": reading,
    }


def _summary(cells: Sequence[Mapping[str, Any]], q: float) -> dict[str, Any]:
    tested = [cell for cell in cells if cell["tested"]]
    passed = [cell for cell in cells if cell["passes"]]
    by: dict[str, int] = {}
    for cell in passed:
        key = f"{cell['scale']} | {cell['horizon_label']} | {cell['returns']}"
        by[key] = by.get(key, 0) + 1
    not_tested: dict[str, int] = {}
    for cell in cells:
        if not cell["tested"]:
            not_tested[cell["reason"]] = not_tested.get(cell["reason"], 0) + 1
    return {
        "cells": len(cells),
        "tested": len(tested),
        "not_tested": not_tested,
        "passed": len(passed),
        # Of the cells that pass, about this many are expected to be luck.
        "expected_luck_among_passed": round(q * len(passed), 2),
        "smallest_p_value": min((cell["p_value"] for cell in tested), default=None),
        "passed_by": by,
        "rows_with_a_pass": sorted({cell["row"] for cell in passed}),
    }
