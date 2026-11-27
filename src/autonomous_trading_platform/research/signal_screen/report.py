"""A screen's result as text: one table of edges per scale and kind of return, then
every cell that passed with what stands behind it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_KIND_TITLES = {
    "excess": "less the mean of the names in force (which name, or when)",
    "raw": "the name's own return (what a strategy would have seen)",
}
_SCALE_TITLES = {"5min": "5-minute bars", "daily": "daily closes"}


def _table(
    header: Sequence[str], lines: Sequence[Sequence[str]], *, numbers: bool = True
) -> list[str]:
    """The first column to the left; the rest to the right when they are numbers."""
    widths = [max(len(str(line[i])) for line in [header, *lines]) for i in range(len(header))]
    out = []
    for line in [header, *lines]:
        first = str(line[0]).ljust(widths[0])
        rest = [
            str(value).rjust(widths[i + 1]) if numbers else str(value).ljust(widths[i + 1])
            for i, value in enumerate(line[1:])
        ]
        out.append("  ".join([first, *rest]).rstrip())
    out.insert(1, "  ".join("-" * width for width in widths))
    return out


def _edge(cell: Mapping[str, Any] | None) -> str:
    if cell is None:
        return ""
    if not cell["tested"]:
        return "n/t"
    return f"{cell['edge_bp']:+.2f}{'*' if cell['passes'] else ' '}"


def _number(value: float | None, pattern: str = "{:+.2f}") -> str:
    return "none" if value is None else pattern.format(value)


def describe_library(described: Mapping[str, Any]) -> str:
    """A library's rows as a table (``Library.describe()``). The exact condition of
    each row, with every setting, is in the JSON form."""
    out = [f"Library {described['name']}: {len(described['rows'])} rows", ""]
    out += _table(
        ["row", "family", "the idea", "return after"],
        [[row["id"], row["family"], row["reads"], row["predicts"]] for row in described["rows"]],
        numbers=False,
    )
    out += ["", f"hash {described['hash']}"]
    return "\n".join(out)


def _standing(cell: Mapping[str, Any]) -> str:
    if cell["tradeable"]:
        return "a finding, tradeable as is"
    if cell["finding"]:
        return "a finding, under the cost"
    return "passes, not a finding (one piece or most names lack it)"


def _reading_lines(reading: Mapping[str, Any]) -> list[str]:
    """Which outcomes of the rule apply, and what each horizon could have shown."""
    out = ["", f"Reading into G10 (rule {reading['rule']})"]
    if not reading["readable"]:
        return out + [reading["note"]]
    out.append(
        f"{reading['findings']} findings ({reading['tradeable']} tradeable as is; a row at "
        f"a horizon counts once, on either return): {reading['predicting_higher']} predict "
        f"a higher return, {reading['predicting_lower']} a lower one."
    )
    for outcome in reading["applies"]:
        out.append(f"- {outcome['outcome']} ({outcome['findings']}): {outcome['direction']}")
        for key in ("nothing_there", "not_enough_data"):
            if outcome.get(key):
                out.append(f"    {key.replace('_', ' ')}: {', '.join(outcome[key])}")
    if reading["note"]:
        out.append(f"  ({reading['note']})")
    out.append("")
    out += _table(
        [
            "horizon",
            "cells tested",
            "findings",
            "tradeable",
            "luck reaches (bp)",
            "above every shuffle (bp)",
            "reads",
        ],
        [
            [
                f"{h['scale']} {h['horizon_label']}",
                str(h["cells_tested"]),
                str(h["findings"]),
                str(h["tradeable"]),
                _number(h["luck_reaches_bp"]),
                _number(h["above_every_shuffle_bp"]),
                h["reads"],
            ]
            for h in reading["by_horizon"]
        ],
    )
    out += [
        "A horizon with no finding reads 'nothing there' only where luck's upper end "
        f"(97.5%) stays under the {reading['cost_bp']:g} bp cost; where it reaches the cost, "
        "'not enough data'.",
        "Passing the control on its own takes more: an edge above every shuffle, the "
        "column beside it.",
    ]
    return out


def render_report(report: Mapping[str, Any]) -> str:
    library = report["library"]
    settings = report["settings"]
    summary = report["summary"]
    out = [
        f"Signal screen: library {library['name']} ({library['rows_run']} of "
        f"{library['rows']} rows run), hash {library['hash'][:12]}",
    ]
    if not library["complete"]:
        out.append("NOT THE WHOLE LIBRARY: a rehearsal, not the declared run.")
    if not settings["declared"]:
        out.append("NOT THE DECLARED SETTINGS (shuffles or seed changed): a rehearsal.")
    source = report.get("source")
    if source:
        out.append(
            f"Bars {source['dataset_version']}, {source['first_session']} to "
            f"{source['last_session']} ({source['sessions']} sessions, "
            f"{source['names']} names); nothing from {source['holdout_start']} on is read."
        )
    out += [
        f"{summary['tested']} of {summary['cells']} cells tested; {summary['passed']} pass "
        f"at a {settings['q']:.0%} false discovery rate "
        f"(about {summary['expected_luck_among_passed']} of them expected to be luck).",
        f"Edge: basis points over what the same firings find on other days, "
        f"up to {settings['shuffles']} block shuffles, one-sided. * passes. n/t not tested.",
    ]
    for reason, count in summary["not_tested"].items():
        out.append(f"  not tested, {count} cells: {reason}")

    rows = {row["id"]: row for row in report["rows"]}
    cells: dict[tuple[str, str, str], Mapping[str, Any]] = {
        (cell["row"], cell["horizon"], cell["returns"]): cell for cell in report["cells"]
    }
    for scale, facts in report["scales"].items():
        horizons = facts["horizons"]
        for kind in settings["returns"]:
            out += [
                "",
                f"{_SCALE_TITLES.get(scale, scale)}, {kind}: {_KIND_TITLES.get(kind, kind)}",
            ]
            lines = [
                [row["id"], row["predicts"]]
                + [_edge(cells.get((row["id"], horizon["key"], kind))) for horizon in horizons]
                for row in rows.values()
                if row["scale"] == scale
            ]
            out += _table(["row", "predicts"] + [h["label"] for h in horizons], lines)

    out += _reading_lines(report["reading"])

    passed = [cell for cell in report["cells"] if cell["passes"]]
    out += ["", f"Cells that pass ({len(passed)})"]
    for cell in sorted(passed, key=lambda c: c["p_value"]):
        row = rows[cell["row"]]
        pieces = ", ".join(
            f"{name} {_number(edge)}" for name, edge in cell["edge_by_piece_bp"].items()
        )
        out += [
            f"- {cell['row']} ({row['condition']}; predicts {row['predicts']}), "
            f"{cell['scale']}, {cell['horizon_label']}, {cell['returns']}: "
            f"{_standing(cell)}",
            f"    edge {cell['edge_bp']:+.2f} bp (luck {cell['luck_low_bp']:+.2f} to "
            f"{cell['luck_high_bp']:+.2f}), p {cell['p_value']:.4f} over "
            f"{cell['shuffles']} shuffles, "
            f"{cell['firings']} firings ({cell['firing_share']:.1%} of bars)",
            f"    after a firing {cell['after_firing_bp']:+.2f} bp, on other days "
            f"{cell['on_other_days_bp']:+.2f} bp; right way {cell['hit_rate']:.1%} against "
            f"{cell['hit_rate_on_other_days']:.1%}",
            f"    by piece: {pieces or 'none'}; names with the edge "
            f"{_number(cell['names_positive'], '{:.0%}')} of {cell['names_measured']}; "
            f"less a {settings['round_trip_cost'] * 1e4:g} bp round trip "
            f"{cell['edge_less_cost_bp']:+.2f} bp",
        ]
    if not passed:
        out.append("- none")
    return "\n".join(out)
