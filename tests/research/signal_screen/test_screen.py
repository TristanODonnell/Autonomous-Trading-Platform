"""The whole screen on a made-up market where the answer is known.

Six names, forty sessions of 5-minute bars. One edge is planted: after a bar whose
10-bar z-score is under -1.5, the bar after the entry returns 20 bp more. Everything
else is noise. The screen must find the planted row in its direction, must not find it
in the other, and must say what it could not test.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.research.signal_screen.bars import build_name_bars
from autonomous_trading_platform.research.signal_screen.library import (
    Condition,
    Library,
    Row,
    reading,
)
from autonomous_trading_platform.research.signal_screen.panel import Horizon, build_panel
from autonomous_trading_platform.research.signal_screen.report import (
    describe_library,
    render_report,
)
from autonomous_trading_platform.research.signal_screen.screen import (
    ScreenSettings,
    run_screen,
)

SESSIONS, NAMES, WARMUP = 40, 6, 2
BUMP = 0.0020
SETTINGS = ScreenSettings(shuffles=500, seed=7)
HORIZONS = {
    "5min": (Horizon("bars", 1), Horizon("sessions", 0)),
    "daily": (Horizon("sessions", 1),),
}


def _row(row_id, scale, direction, *conditions) -> Row:
    return Row(
        id=row_id,
        family="test",
        scale=scale,
        conditions=tuple(conditions),
        direction=direction,
        reads=row_id,
    )


Z10_LOW = Condition(reading("z_score", window=10), "<", -1.5)
LIBRARY = Library(
    "planted",
    (
        _row("z10_low", "5min", 1, Z10_LOW),
        _row("z10_low_wrong_way", "5min", -1, Z10_LOW),
        _row("volume_noise", "5min", 1, Condition(reading("volume_ratio", window=10), ">", 1.2)),
        _row("daily_noise", "daily", 1, Condition(reading("rate_of_change", lookback=1), ">", 0.0)),
        # Reaches back 20 sessions: 38 sessions cannot be shuffled in blocks of 20.
        _row("daily_too_slow", "daily", 1, Condition(reading("z_score", window=20), "<", -1.0)),
    ),
)


def _market():
    rng = np.random.default_rng(5)
    days = list(pd.bdate_range("2025-01-06", periods=SESSIONS).date)
    stamps = np.array(
        [
            np.datetime64(f"{day}T14:30") + np.timedelta64(5 * slot, "m")  # 09:30 in winter
            for day in days
            for slot in range(78)
        ]
    )
    total = len(stamps)
    names = []
    for index in range(NAMES):
        returns = rng.normal(0.0, 0.0010, total)
        closes = np.empty(total)
        closes[0] = 100.0
        for t in range(1, total):
            closes[t] = closes[t - 1] * (1.0 + returns[t])
            if t >= 9 and t + 2 < total:
                window = closes[t - 9 : t + 1]
                deviation = window.std(ddof=1)
                if deviation > 0 and (closes[t] - window.mean()) / deviation < -1.5:
                    returns[t + 2] += BUMP  # read at t, traded at t + 1, paid by t + 2
        names.append(
            build_name_bars(
                symbol=f"N{index}",
                timestamps=stamps,
                close=closes,
                volume=rng.integers(100, 1000, total).astype(float),
                actions=[],
                sessions=days,
            )
        )
    in_force = np.ones((SESSIONS, NAMES), dtype=bool)
    return {
        scale: build_panel(
            scale=scale, names=names, sessions=days, first_observed=WARMUP, in_force=in_force
        )
        for scale in ("5min", "daily")
    }


@pytest.fixture(scope="module")
def market():
    return _market()


@pytest.fixture(scope="module")
def report(market):
    observed = SESSIONS - WARMUP
    first_half = np.arange(observed) < observed // 2
    return run_screen(
        panels=market,
        library=LIBRARY,
        groups={"selection": first_half, "evaluation": ~first_half},
        settings=SETTINGS,
        horizons=HORIZONS,
    )


def _cell(report, row, horizon, kind):
    return next(
        cell
        for cell in report["cells"]
        if (cell["row"], cell["horizon"], cell["returns"]) == (row, horizon, kind)
    )


def test_the_planted_edge_is_found_with_its_size(report):
    raw = _cell(report, "z10_low", "bars:1", "raw")
    assert raw["tested"] and raw["passes"]
    assert raw["p_value"] == pytest.approx(1 / 501)  # no shuffle did as well
    # 20 bp where it fires; the other days it is compared with hold some of it too.
    assert 14.0 < raw["edge_bp"] < 21.0
    assert raw["luck_low_bp"] < 0 < raw["luck_high_bp"] < raw["edge_bp"]
    assert raw["after_firing_bp"] - raw["on_other_days_bp"] == pytest.approx(raw["edge_bp"])
    assert raw["edge_less_cost_bp"] == pytest.approx(raw["edge_bp"] - 2.0)
    assert raw["hit_rate"] > raw["hit_rate_on_other_days"] + 0.2
    assert 0.02 < raw["firing_share"] < 0.15
    assert raw["names_measured"] == NAMES and raw["names_positive"] == 1.0
    assert raw["edge_by_piece_bp"]["selection"] > 10 and raw["edge_by_piece_bp"]["evaluation"] > 10
    assert raw["block_sessions"] == 5 and raw["shuffles"] == 500

    # Less the market of six names, one name's bump is five sixths of itself.
    excess = _cell(report, "z10_low", "bars:1", "excess")
    assert excess["passes"]
    assert excess["edge_bp"] == pytest.approx(raw["edge_bp"] * 5 / 6, rel=0.15)


def test_the_planted_edge_is_read_into_g10_as_the_rule_says(report):
    """The planted row buys a dip that comes back within the session: by the rule fixed
    with the user, tradeable findings at intraday horizons, on both returns."""
    reading = report["reading"]
    assert reading["readable"] is True
    assert [outcome["key"] for outcome in reading["applies"]] == ["tradeable_intraday"]
    assert {(entry["row"], entry["horizon"]) for entry in reading["found"]} == {
        ("z10_low", "bars:1"),
        ("z10_low", "sessions:0"),
    }
    assert all(entry["returns"] == ["excess", "raw"] for entry in reading["found"])
    assert (reading["findings"], reading["cells_that_are_findings"]) == (2, 4)
    assert (reading["tradeable"], reading["predicting_lower"]) == (2, 0)
    for cell in report["cells"]:
        assert cell["finding"] is (cell["row"] == "z10_low" and cell["scale"] == "5min")
        assert cell["tradeable"] is cell["finding"]
    # Every shuffle sits under the planted edge, and the top of them above the range.
    found = _cell(report, "z10_low", "bars:1", "excess")
    assert found["luck_high_bp"] < found["luck_top_bp"] < found["edge_bp"]
    daily = next(h for h in reading["by_horizon"] if h["scale"] == "daily")
    # The cost reaches the rule in basis points, and the label follows from it.
    assert reading["cost_bp"] == 2.0 and daily["findings"] == 0
    under = daily["luck_reaches_bp"] < 2.0
    assert daily["luck_stays_under_cost"] is under
    assert daily["reads"] == ("nothing there" if under else "not enough data")
    assert daily["luck_reaches_bp"] < daily["above_every_shuffle_bp"]


def test_the_same_condition_predicted_the_other_way_is_not_a_finding(report):
    for horizon in ("bars:1", "sessions:0"):
        for kind in ("raw", "excess"):
            cell = _cell(report, "z10_low_wrong_way", horizon, kind)
            assert cell["tested"] and not cell["passes"]
    wrong = _cell(report, "z10_low_wrong_way", "bars:1", "raw")
    assert wrong["p_value"] > 0.99
    assert wrong["edge_bp"] == pytest.approx(-_cell(report, "z10_low", "bars:1", "raw")["edge_bp"])
    assert wrong["names_positive"] == 0.0


def test_a_small_p_value_among_many_noise_cells_is_not_a_finding(market):
    """Forty-eight cells of noise. A few fall under 10% by luck; counted one at a time
    they would be findings. Benjamini-Hochberg over all of them passes none."""
    rows = tuple(
        _row(
            f"noise_{window}_{direction}",
            "5min",
            direction,
            Condition(reading("volume_ratio", window=window), ">", 1.1),
        )
        for window in (5, 8, 12, 16, 20, 30)
        for direction in (1, -1)
    )
    report = run_screen(
        panels={"5min": market["5min"]},
        library=Library("noise", rows),
        settings=SETTINGS,
        horizons=HORIZONS,
    )
    cells = report["cells"]
    assert len(cells) == 48 and all(cell["tested"] for cell in cells)
    assert sum(cell["p_value"] < SETTINGS.q for cell in cells) >= 2
    assert report["summary"]["passed"] == 0 and not any(cell["passes"] for cell in cells)
    assert "- none" in render_report(report)


def test_what_cannot_be_tested_says_why(report):
    for kind in ("raw", "excess"):
        cell = _cell(report, "daily_too_slow", "sessions:1", kind)
        assert not cell["tested"] and not cell["passes"]
        assert "cannot be shuffled in blocks of 20" in cell["reason"]
        assert cell["p_value"] is None and cell["edge_bp"] is None
        assert cell["observations"] > 0  # counted, though not tested
    assert list(report["summary"]["not_tested"].values()) == [2]


def test_the_report_counts_what_was_run(report):
    assert report["library"] == {
        "name": "planted",
        "hash": LIBRARY.content_hash(),
        "rows": 5,
        "rows_run": 5,
        "complete": True,
    }
    summary = report["summary"]
    assert summary["cells"] == 3 * 2 * 2 + 2 * 1 * 2
    assert summary["tested"] == summary["cells"] - 2
    assert summary["passed"] == sum(cell["passes"] for cell in report["cells"])
    assert "z10_low" in summary["rows_with_a_pass"]
    assert "z10_low_wrong_way" not in summary["rows_with_a_pass"]
    assert summary["expected_luck_among_passed"] == pytest.approx(0.1 * summary["passed"])
    assert summary["smallest_p_value"] == pytest.approx(1 / 501)
    assert summary["passed_by"]["5min | 5 minutes | raw"] >= 1
    # The noise rows: at a 10% false discovery rate one may pass, not most.
    noise = [c for c in report["cells"] if c["row"] in ("volume_noise", "daily_noise")]
    assert sum(cell["passes"] for cell in noise) <= 1

    five = report["scales"]["5min"]
    assert (five["sessions"], five["slots"], five["names"]) == (SESSIONS - WARMUP, 78, NAMES)
    # Every bar of an observed session but the very last has a bar to trade on.
    assert five["decisions_counted"] == NAMES * ((SESSIONS - WARMUP) * 78 - 1)
    assert [h["label"] for h in five["horizons"]] == ["5 minutes", "to the close"]
    assert report["scales"]["daily"]["decisions_counted"] == NAMES * (SESSIONS - WARMUP)

    rows = {row["id"]: row for row in report["rows"]}
    assert rows["z10_low"]["predicts"] == "higher" and rows["z10_low"]["flat_windows"] == 0
    assert 0 < rows["z10_low"]["decisions_held"] < rows["z10_low"]["decisions_with_value"]
    assert report["settings"]["pieces"] == ["evaluation", "selection"]
    assert report["settings"]["declared"] is False  # 500 shuffles is not the declared run
    assert json.loads(json.dumps(report)) == report  # plain numbers all the way down


def test_the_report_reads_as_text(report):
    text = render_report(report)
    lines = text.splitlines()
    assert lines[0].startswith("Signal screen: library planted (5 of 5 rows run)")
    assert "NOT THE DECLARED SETTINGS" in text and "NOT THE WHOLE LIBRARY" not in text
    planted = next(line for line in lines if line.startswith("z10_low  "))
    assert "higher" in planted and "*" in planted
    assert "*" not in next(line for line in lines if line.startswith("z10_low_wrong_way"))
    assert "n/t" in next(line for line in lines if line.startswith("daily_too_slow"))
    assert "not tested, 2 cells:" in text
    assert f"Cells that pass ({report['summary']['passed']})" in text
    assert "- z10_low (z_score(window=10) < -1.5; predicts higher), 5min, 5 minutes" in text
    assert "by piece: selection +" in text and "names with the edge 100% of 6" in text
    assert "less a 2 bp round trip" in text
    assert "Reading into G10 (rule fixed with the user on 2026-11-27, before the run)" in text
    assert "2 findings (2 tradeable as is; a row at a horizon counts once" in text
    assert "- tradeable findings at intraday horizons (5 minutes to the close) (2): " in text
    assert "5min, 5 minutes, excess: a finding, tradeable as is" in text
    assert "above every shuffle (bp)" in text


def test_a_scale_left_out_makes_the_run_a_rehearsal(market):
    report = run_screen(
        panels={"5min": market["5min"]},
        library=LIBRARY,
        settings=ScreenSettings(shuffles=50, seed=1),
        horizons={"5min": (Horizon("bars", 1),)},
    )
    assert report["library"]["rows_run"] == 3 and report["library"]["complete"] is False
    assert list(report["scales"]) == ["5min"]
    assert {cell["scale"] for cell in report["cells"]} == {"5min"}
    assert all(cell["edge_by_piece_bp"] == {} for cell in report["cells"])
    # Run without the two pieces, the rule is not read: that is not "no findings".
    assert report["summary"]["passed"] > 0
    assert report["reading"]["readable"] is False and report["reading"]["applies"] == []
    assert not any(cell["finding"] for cell in report["cells"])
    text = render_report(report)
    assert "NOT THE WHOLE LIBRARY" in text
    assert "not read: the rule needs the selection and the evaluation piece" in text


def test_the_declared_settings_are_the_run_the_plan_names():
    settings = ScreenSettings()
    assert (settings.shuffles, settings.q, settings.round_trip_cost) == (10_000, 0.10, 0.0002)


def test_a_group_of_the_wrong_length_is_refused(market):
    with pytest.raises(ValueError, match="one flag per observed session"):
        run_screen(
            panels=market,
            library=LIBRARY,
            groups={"selection": np.ones(5, dtype=bool)},
            settings=SETTINGS,
            horizons=HORIZONS,
        )


def test_a_library_is_described_before_anything_is_read():
    text = describe_library(LIBRARY.describe())
    assert text.splitlines()[0] == "Library planted: 5 rows"
    assert "z10_low_wrong_way" in text and "lower" in text
    assert text.splitlines()[-1] == f"hash {LIBRARY.content_hash()}"
