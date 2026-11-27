"""Reading a screen's result into G10: the rule fixed with the user on 2026-11-27,
before the run (research plan, "Signal screen, stage 1").

G10 is the open decision of the generator review: what the strategy generator searches
for next. The screen is the measurement it is picked from, so what each result means
was written down before any result existed.

**A finding** is a cell that

- passes the false discovery control,
- has its edge in the predicted direction on the selection piece and on the evaluation
  piece, each on its own, and
- shows that edge in at least half of the names it could be measured on.

A finding is **tradeable as is** when its edge is also above the round-trip cost.

A row at a horizon is counted once, whichever return shows it. Against the other names
in force the edge says which name to hold; raw, it also says when to hold any. A
platform that only buys can use either, and the control was run over both.

**Where each outcome sends G10.** Rows that predict a higher return are what a platform
that only buys can act on, so they pick the direction; rows that predict a lower one
are reasons to be out.

- Tradeable findings at intraday horizons (5 minutes to the close): richer 5-minute
  strategies, built around the families that passed.
- Findings mostly at one session or longer, or on daily closes: slower strategies.
- Findings, all under the cost: slower strategies too. The information is real and too
  small to trade at 5-minute turnover.
- No findings: stop searching variations of today's indicators; stage 2 (a long daily
  history, rows that rank names) before any generator build.
- Only rows that predict a lower return: exits or filters. That alone picks no direction.

**"Nothing there" needs a screen that could have seen it.** A horizon with no finding
counts as empty only where luck alone stays under the cost: the upper end of the luck
range (its 97.5th percentile), at the middle cell of that horizon, is below it. Where
luck reaches the cost, an edge worth trading could sit inside it, and the horizon reads
"not enough data". Both readings point to stage 2; they say different things about why.

Standing above the luck range is less than passing the control: among hundreds of cells
an edge passes on its own only above every shuffle. That number is reported beside the
range, so each "nothing there" shows how firm it is. It does not change the label.

When the findings fit more than one of the outcomes, or none as written, every outcome
that applies is listed with its count and the choice is made with the user, not here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

import numpy as np

PIECES = ("selection", "evaluation")
# A finding shows in at least this share of the names it could be measured on.
MIN_NAMES_SHARE = 0.5

RULE = "fixed with the user on 2026-11-27, before the run"

OUTCOMES: dict[str, tuple[str, str]] = {
    "tradeable_intraday": (
        "tradeable findings at intraday horizons (5 minutes to the close)",
        "richer 5-minute strategies, built around the families that passed",
    ),
    "mostly_longer": (
        "findings mostly at one session or longer, or on daily closes",
        "slower strategies: daily decisions, holding for days",
    ),
    "all_under_cost": (
        "findings, all under the cost",
        "slower strategies: the information is too small to trade at 5-minute turnover",
    ),
    "none": (
        "no findings",
        "stop searching variations of today's indicators; stage 2 (a long daily history, "
        "rows that rank names) before any generator build",
    ),
    "only_lower": (
        "only rows that predict a lower return are findings",
        "exits or filters; this alone picks no direction",
    ),
}


def is_finding(cell: Mapping[str, Any]) -> bool:
    if not cell["passes"]:
        return False
    by_piece = cell["edge_by_piece_bp"]
    for piece in PIECES:
        edge = by_piece.get(piece)
        if edge is None or edge <= 0:
            return False  # not there on this piece, or too few firings to say
    share = cell["names_positive"]
    return share is not None and share >= MIN_NAMES_SHARE


def is_intraday(cell: Mapping[str, Any]) -> bool:
    """5 minutes to the close: the return ends inside the session it started in."""
    kind, _, count = cell["horizon"].partition(":")
    return bool(cell["scale"] == "5min" and (kind == "bars" or count == "0"))


def _outcome(key: str, count: int, **more: Any) -> dict[str, Any]:
    outcome, direction = OUTCOMES[key]
    return {"key": key, "outcome": outcome, "direction": direction, "findings": count, **more}


def _found(cells: Sequence[Mapping[str, Any]], predicts: Mapping[str, str]) -> list[dict[str, Any]]:
    """The findings, a row at a horizon once: tradeable if either return's cell is."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in cells:
        if not cell["finding"]:
            continue
        entry = found.setdefault(
            (cell["row"], cell["horizon"]),
            {
                "row": cell["row"],
                "scale": cell["scale"],
                "horizon": cell["horizon"],
                "horizon_label": cell["horizon_label"],
                "predicts": predicts[cell["row"]],
                "intraday": is_intraday(cell),
                "tradeable": False,
                "returns": [],
            },
        )
        entry["tradeable"] = entry["tradeable"] or cell["tradeable"]
        entry["returns"].append(cell["returns"])
    return list(found.values())


def _middle(cells: Sequence[Mapping[str, Any]], key: str) -> float | None:
    return round(float(np.median([cell[key] for cell in cells])), 4) if cells else None


def _by_horizon(cells: Sequence[Mapping[str, Any]], cost_bp: float) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for cell in cells:
        grouped.setdefault((cell["scale"], cell["horizon"]), []).append(cell)
    out = []
    for (scale, horizon), members in grouped.items():
        tested = [cell for cell in members if cell["tested"]]
        findings = len({cell["row"] for cell in members if cell["finding"]})
        reaches = _middle(tested, "luck_high_bp")
        under_cost = reaches is not None and reaches < cost_bp
        if not tested:
            reads = "not tested"
        elif findings:
            reads = "findings"
        else:
            reads = "nothing there" if under_cost else "not enough data"
        out.append(
            {
                "scale": scale,
                "horizon": horizon,
                "horizon_label": members[0]["horizon_label"],
                "intraday": is_intraday(members[0]),
                "cells_tested": len(tested),
                "findings": findings,
                "tradeable": len({cell["row"] for cell in members if cell["tradeable"]}),
                # At the horizon's middle cell: the upper end of the luck range, and the
                # largest edge any shuffle gave.
                "luck_reaches_bp": reaches,
                "above_every_shuffle_bp": _middle(tested, "luck_top_bp"),
                "luck_stays_under_cost": under_cost,
                "reads": reads,
            }
        )
    return out


def read_into_g10(
    *,
    cells: list[dict[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    cost_bp: float,
    pieces: Collection[str],
) -> dict[str, Any]:
    """Marks each cell ``finding`` and ``tradeable``, and says which outcomes of the
    rule apply. ``rows``: the screen's row facts (each with ``id`` and ``predicts``);
    ``pieces``: the groups of sessions the screen was run with."""
    # Without both pieces no cell can be shown to hold on each: that is not "no findings".
    readable = all(piece in pieces for piece in PIECES)
    predicts = {row["id"]: row["predicts"] for row in rows}
    for cell in cells:
        cell["finding"] = readable and is_finding(cell)
        cell["tradeable"] = bool(cell["finding"] and cell["edge_less_cost_bp"] > 0)

    found = _found(cells, predicts)
    higher = [entry for entry in found if entry["predicts"] == "higher"]
    lower = [entry for entry in found if entry["predicts"] == "lower"]
    by_horizon = _by_horizon(cells, cost_bp)

    applies: list[dict[str, Any]] = []
    note: str | None = None
    if not readable:
        note = (
            "not read: the rule needs the selection and the evaluation piece, and this "
            "run had " + (", ".join(sorted(pieces)) or "neither")
        )
    elif higher:
        tradeable_intraday = [e for e in higher if e["tradeable"] and e["intraday"]]
        longer = [e for e in higher if not e["intraday"]]
        if tradeable_intraday:
            applies.append(_outcome("tradeable_intraday", len(tradeable_intraday)))
        if 2 * len(longer) > len(higher):
            applies.append(_outcome("mostly_longer", len(longer)))
        if not any(e["tradeable"] for e in higher):
            applies.append(_outcome("all_under_cost", len(higher)))
        if len(applies) > 1:
            note = "more than one outcome applies: the choice between them is made with the user"
        elif not applies:
            note = "the findings fit no outcome as written: read with the user"
    elif lower:
        applies.append(_outcome("only_lower", len(lower)))
    else:
        applies.append(
            _outcome(
                "none",
                0,
                nothing_there=[
                    f"{h['scale']} {h['horizon_label']}"
                    for h in by_horizon
                    if h["reads"] == "nothing there"
                ],
                not_enough_data=[
                    f"{h['scale']} {h['horizon_label']}"
                    for h in by_horizon
                    if h["reads"] == "not enough data"
                ],
            )
        )

    return {
        "rule": RULE,
        "readable": readable,
        "cost_bp": cost_bp,
        # A row at a horizon once; the cells count each return apart.
        "findings": len(found),
        "cells_that_are_findings": sum(1 for cell in cells if cell["finding"]),
        "tradeable": sum(1 for entry in found if entry["tradeable"]),
        "predicting_higher": len(higher),
        "predicting_lower": len(lower),
        "found": found,
        "by_horizon": by_horizon,
        "applies": applies,
        "note": note,
    }
