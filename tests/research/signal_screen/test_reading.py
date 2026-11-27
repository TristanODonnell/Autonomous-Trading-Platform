"""The rule for reading a screen into G10, fixed with the user on 2026-11-27 before the
run. Each test builds the cells of a made-up result by hand and checks what the rule
says of them, so the rule is pinned apart from the statistics that feed it."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.signal_screen.reading import (
    MIN_NAMES_SHARE,
    PIECES,
    is_finding,
    is_intraday,
    read_into_g10,
)

COST = 2.0
LABELS = {
    "bars:1": "5 minutes",
    "bars:12": "1 hour",
    "sessions:0": "to the close",
    "sessions:1": "1 session",
    "sessions:5": "5 sessions",
}
ROWS = [
    {"id": "up_a", "predicts": "higher"},
    {"id": "up_b", "predicts": "higher"},
    {"id": "up_c", "predicts": "higher"},
    {"id": "up_d", "predicts": "higher"},
    {"id": "down_a", "predicts": "lower"},
    {"id": "d.up_a", "predicts": "higher"},
]


def _cell(
    row="up_a",
    horizon="bars:1",
    returns="excess",
    *,
    passes=True,
    edge=5.0,
    selection=4.0,
    evaluation=6.0,
    names=0.8,
    luck_high=0.5,
    luck_top=1.2,
    tested=True,
):
    return {
        "row": row,
        "scale": "daily" if row.startswith("d.") else "5min",
        "horizon": horizon,
        "horizon_label": LABELS[horizon],
        "returns": returns,
        "tested": tested,
        "passes": passes and tested,
        "edge_bp": edge if tested else None,
        "edge_less_cost_bp": edge - COST if tested else None,
        "edge_by_piece_bp": {"selection": selection, "evaluation": evaluation} if tested else {},
        "names_positive": names if tested else None,
        "luck_high_bp": luck_high if tested else None,
        "luck_top_bp": luck_top if tested else None,
    }


def _noise(row="up_a", horizon="bars:1", **more):
    """A cell that was tested and did not pass."""
    return _cell(row, horizon, passes=False, edge=0.1, **more)


def _read(cells, pieces=PIECES):
    return read_into_g10(cells=cells, rows=ROWS, cost_bp=COST, pieces=pieces)


def _keys(reading):
    return [outcome["key"] for outcome in reading["applies"]]


@pytest.mark.parametrize(
    ("change", "finding"),
    [
        ({}, True),
        ({"passes": False}, False),  # luck could give it, among this many cells
        ({"selection": -0.5}, False),  # the other way on the selection months
        ({"evaluation": -0.5}, False),  # the other way on the evaluation months
        ({"evaluation": 0.0}, False),  # no edge is not "the same sign"
        ({"selection": None}, False),  # too few firings on a piece to say
        ({"names": 0.49}, False),  # carried by a minority of the names
        ({"names": MIN_NAMES_SHARE}, True),  # half the names is enough
        ({"names": None}, False),  # no name could be measured on its own
    ],
)
def test_a_finding_passes_the_control_on_both_pieces_and_in_half_the_names(change, finding):
    assert is_finding(_cell(**change)) is finding


def test_a_cell_with_a_piece_missing_is_not_a_finding():
    cell = _cell()
    del cell["edge_by_piece_bp"]["evaluation"]
    assert is_finding(cell) is False


def test_tradeable_is_a_finding_above_the_cost():
    above, at, under, not_found = (
        _cell("up_a", edge=2.5),
        _cell("up_b", edge=2.0),
        _cell("up_c", edge=1.5),
        _cell("up_d", edge=9.0, names=0.2),
    )
    reading = _read([above, at, under, not_found])
    assert [cell["finding"] for cell in (above, at, under, not_found)] == [True, True, True, False]
    # The edge has to clear the cost, not match it; and a large edge that is no finding
    # is not tradeable either.
    assert [cell["tradeable"] for cell in (above, at, under, not_found)] == [
        True,
        False,
        False,
        False,
    ]
    assert (reading["findings"], reading["tradeable"]) == (3, 1)


def test_intraday_ends_inside_the_session_it_started_in():
    assert is_intraday(_cell(horizon="bars:1"))
    assert is_intraday(_cell(horizon="bars:12"))
    assert is_intraday(_cell(horizon="sessions:0"))  # to the close
    assert not is_intraday(_cell(horizon="sessions:1"))  # the next close
    assert not is_intraday(_cell(horizon="sessions:5"))
    assert not is_intraday(_cell("d.up_a", horizon="sessions:1"))  # daily closes, always
    # A bar on the daily scale is a session, so nothing daily is intraday.
    assert not is_intraday(_cell("d.up_a", horizon="bars:1"))


def test_a_row_at_a_horizon_counts_once_on_either_return():
    cells = [
        _cell("up_a", "bars:1", "excess", edge=1.0),  # under the cost against the others
        _cell("up_a", "bars:1", "raw", edge=3.0),  # above it raw
        _cell("up_b", "bars:1", "excess", passes=False),
        _cell("up_b", "bars:1", "raw", edge=3.0),  # found on the raw return only
    ]
    reading = _read(cells)
    assert reading["cells_that_are_findings"] == 3
    assert (reading["findings"], reading["tradeable"]) == (2, 2)
    found = {entry["row"]: entry for entry in reading["found"]}
    assert found["up_a"]["returns"] == ["excess", "raw"] and found["up_a"]["tradeable"]
    assert found["up_b"]["returns"] == ["raw"]
    assert reading["by_horizon"][0]["findings"] == 2
    assert reading["by_horizon"][0]["cells_tested"] == 4


def test_tradeable_intraday_findings_point_to_richer_five_minute_strategies():
    reading = _read(
        [
            _cell("up_a", "bars:1", edge=4.0),
            _cell("up_b", "sessions:0", edge=6.0),
            _cell("up_c", "bars:12", edge=1.0),  # a finding, under the cost
            _noise("up_d", "sessions:1"),
        ]
    )
    assert _keys(reading) == ["tradeable_intraday"]
    assert reading["applies"][0]["findings"] == 2
    assert "richer 5-minute strategies" in reading["applies"][0]["direction"]
    assert reading["note"] is None


def test_findings_mostly_at_a_session_or_longer_point_to_slower_strategies():
    reading = _read(
        [
            _cell("up_a", "sessions:1", edge=30.0),
            _cell("d.up_a", "sessions:5", edge=60.0),
            _cell("up_b", "bars:1", edge=1.0),  # one intraday finding, under the cost
        ]
    )
    assert _keys(reading) == ["mostly_longer"]
    assert reading["applies"][0]["findings"] == 2
    assert "slower strategies" in reading["applies"][0]["direction"]


def test_half_the_findings_is_not_most_of_them():
    reading = _read(
        [
            _cell("up_a", "sessions:1", edge=30.0),
            _cell("up_b", "bars:1", edge=1.0),
        ]
    )
    # One of two is longer, and one is tradeable (not intraday): no outcome as written.
    assert _keys(reading) == []
    assert "fit no outcome as written" in reading["note"]


def test_findings_all_under_the_cost_point_to_slower_strategies():
    reading = _read(
        [
            _cell("up_a", "bars:1", edge=0.4),
            _cell("up_b", "bars:1", edge=1.9),
            _cell("up_c", "sessions:0", edge=2.0),  # at the cost is not above it
        ]
    )
    assert _keys(reading) == ["all_under_cost"]
    assert reading["applies"][0]["findings"] == 3
    assert "too small to trade" in reading["applies"][0]["direction"]


def test_more_than_one_outcome_is_left_to_the_user():
    reading = _read(
        [
            _cell("up_a", "bars:1", edge=4.0),
            _cell("up_b", "sessions:1", edge=30.0),
            _cell("d.up_a", "sessions:5", edge=60.0),
        ]
    )
    assert _keys(reading) == ["tradeable_intraday", "mostly_longer"]
    assert "more than one outcome applies" in reading["note"]


def test_no_findings_points_to_stage_two_and_says_where_the_screen_could_see():
    reading = _read(
        [
            # Luck stays under the 2 bp cost: an edge worth trading would stand out.
            _noise("up_a", "bars:1", luck_high=0.4),
            _noise("up_b", "bars:1", luck_high=0.6),
            _noise("up_c", "bars:1", luck_high=5.0),  # one wide cell does not move the middle
            # Luck reaches the cost: nothing can be said either way.
            _noise("up_a", "sessions:1", luck_high=2.0),
            _noise("up_b", "sessions:1", luck_high=40.0),
            _noise("up_c", "sessions:1", luck_high=1.0),
            # Nothing could be tested here at all.
            _cell("up_a", "sessions:5", tested=False),
        ]
    )
    assert _keys(reading) == ["none"]
    outcome = reading["applies"][0]
    assert "stage 2" in outcome["direction"]
    assert outcome["nothing_there"] == ["5min 5 minutes"]
    assert outcome["not_enough_data"] == ["5min 1 session"]
    by_horizon = {h["horizon"]: h for h in reading["by_horizon"]}
    assert by_horizon["bars:1"]["luck_reaches_bp"] == 0.6
    assert by_horizon["bars:1"]["luck_stays_under_cost"] is True
    assert by_horizon["sessions:1"]["luck_reaches_bp"] == 2.0
    assert by_horizon["sessions:1"]["luck_stays_under_cost"] is False
    assert by_horizon["sessions:5"]["reads"] == "not tested"
    assert by_horizon["sessions:5"]["luck_reaches_bp"] is None
    assert (reading["findings"], reading["note"]) == (0, None)


def test_the_edge_above_every_shuffle_is_reported_and_does_not_change_the_label():
    reading = _read(
        [
            _noise("up_a", "bars:1", luck_high=1.5, luck_top=3.0),
            _noise("up_b", "bars:1", luck_high=1.7, luck_top=3.4),
        ]
    )
    horizon = reading["by_horizon"][0]
    # A 2 bp edge would stand above the luck range and still under some shuffles: the
    # label is the rule's, and the number beside it says how firm it is.
    assert horizon["reads"] == "nothing there"
    assert horizon["above_every_shuffle_bp"] == 3.2


def test_a_horizon_with_a_finding_reads_findings_whatever_luck_reaches():
    reading = _read([_cell("up_a", "sessions:1", edge=90.0, luck_high=30.0)])
    assert reading["by_horizon"][0]["reads"] == "findings"


def test_only_rows_that_predict_lower_are_exits_or_filters():
    reading = _read([_cell("down_a", "bars:1", edge=4.0), _noise("up_a", "bars:1")])
    assert _keys(reading) == ["only_lower"]
    assert "exits or filters" in reading["applies"][0]["direction"]
    assert (reading["predicting_higher"], reading["predicting_lower"]) == (0, 1)


def test_rows_that_predict_lower_do_not_pick_the_direction_beside_ones_that_predict_higher():
    reading = _read(
        [
            _cell("up_a", "bars:1", edge=4.0),
            _cell("down_a", "sessions:1", edge=40.0),
            _cell("down_a", "sessions:5", edge=40.0),
        ]
    )
    # Two of the three findings are longer, and both of those say "lower": the one row
    # a platform that only buys can act on is intraday.
    assert _keys(reading) == ["tradeable_intraday"]
    assert (reading["predicting_higher"], reading["predicting_lower"]) == (1, 2)


@pytest.mark.parametrize("pieces", [(), ("selection",), ("evaluation", "other")])
def test_the_rule_is_not_read_without_both_pieces(pieces):
    cells = [_cell("up_a", "bars:1", edge=4.0)]
    reading = _read(cells, pieces=pieces)
    # Not "no findings": the run could not have shown a finding.
    assert reading["readable"] is False
    assert reading["applies"] == [] and reading["findings"] == 0
    assert reading["note"].startswith("not read: the rule needs the selection and the evaluation")
    assert cells[0]["finding"] is False and cells[0]["tradeable"] is False
