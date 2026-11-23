"""Joining regime labels onto an equity curve: the one-row-per-timestamp shortcut.

When no timestamp repeats in the labels, the join skips the per-timestamp
aggregation (a Python call per timestamp per column). These tests hold the
shortcut to the aggregation's answer, and check that labels with repeated
timestamps still take the aggregation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.research.analysis.regimes.regime_join_service import (
    RegimeJoinService,
)

_COLUMNS = ["regime_trend", "regime_volatility"]


def _timestamps(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-02 14:30", periods=n, freq="5min", tz="UTC")


def _equity(n: int) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": _timestamps(n), "equity": np.linspace(100_000, 101_000, n)})


def _labels(n: int) -> pd.DataFrame:
    """One row per timestamp, with every kind of missing label in it."""
    trend: list[object] = ["bull", "bear", "sideways", None, np.nan] * (n // 5)
    volatility: list[object] = [None] * n  # a dimension with no label at all
    volatility[0] = "high_volatility"
    return pd.DataFrame(
        {"timestamp": _timestamps(n), "regime_trend": trend, "regime_volatility": volatility}
    )


def _same_labels(left: pd.DataFrame, right: pd.DataFrame) -> None:
    assert list(left.columns) == list(right.columns)
    assert len(left) == len(right)
    assert list(left["timestamp"]) == list(right["timestamp"])
    for column in _COLUMNS:
        assert list(left[column].isna()) == list(right[column].isna()), column
        present = left[column].notna()
        assert list(left.loc[present, column]) == list(right.loc[present, column]), column


class TestOneRowPerTimestamp:
    def test_the_shortcut_gives_the_aggregations_answer(self) -> None:
        labels = _labels(40)

        shortcut = RegimeJoinService._one_row_per_timestamp(labels, _COLUMNS)
        aggregated = RegimeJoinService._modal_by_timestamp(labels)

        _same_labels(shortcut, aggregated)

    def test_a_missing_label_is_none_as_the_aggregation_leaves_it(self) -> None:
        shortcut = RegimeJoinService._one_row_per_timestamp(_labels(10), _COLUMNS)

        assert shortcut["regime_trend"].iloc[3] is None  # was None
        assert shortcut["regime_trend"].iloc[4] is None  # was NaN
        assert shortcut["regime_volatility"].iloc[1] is None

    def test_a_row_without_a_timestamp_is_dropped_as_the_aggregation_drops_it(self) -> None:
        labels = _labels(10)
        labels.loc[2, "timestamp"] = pd.NaT

        shortcut = RegimeJoinService._one_row_per_timestamp(labels, _COLUMNS)
        aggregated = RegimeJoinService._modal_by_timestamp(labels)

        assert len(shortcut) == 9
        _same_labels(shortcut, aggregated)

    def test_the_joined_curve_is_the_same_either_way(self, monkeypatch) -> None:
        equity, labels = _equity(60), _labels(40)  # the last 20 bars have no label row
        service = RegimeJoinService()

        by_shortcut = service.join_equity_curve(equity, labels)
        monkeypatch.setattr(
            RegimeJoinService,
            "_one_row_per_timestamp",
            staticmethod(lambda frame, _columns: RegimeJoinService._modal_by_timestamp(frame)),
        )
        by_aggregation = service.join_equity_curve(equity, labels)

        _same_labels(by_shortcut, by_aggregation)
        assert list(by_shortcut["equity"]) == list(equity["equity"])
        assert by_shortcut["regime_trend"].iloc[40:].isna().all()

    def test_the_input_frames_are_left_alone(self) -> None:
        equity, labels = _equity(10), _labels(10)
        before = labels.copy(deep=True)

        RegimeJoinService().join_equity_curve(equity, labels)

        pd.testing.assert_frame_equal(labels, before)


class TestRepeatedTimestamps:
    def test_several_symbols_at_a_timestamp_still_give_the_most_common_label(self) -> None:
        stamps = _timestamps(2)
        labels = pd.DataFrame(
            {
                "timestamp": [stamps[0]] * 3 + [stamps[1]] * 3,
                "regime_trend": ["bull", "bull", "bear", "bear", None, "bear"],
            }
        )

        joined = RegimeJoinService().join_equity_curve(_equity(2), labels)

        assert list(joined["regime_trend"]) == ["bull", "bear"]

    def test_repeated_timestamps_do_not_take_the_shortcut(self, monkeypatch) -> None:
        def refuse(*_: object) -> pd.DataFrame:
            raise AssertionError("the shortcut is for labels with one row per timestamp")

        monkeypatch.setattr(RegimeJoinService, "_one_row_per_timestamp", staticmethod(refuse))
        stamps = _timestamps(1)
        labels = pd.DataFrame({"timestamp": [stamps[0]] * 2, "regime_trend": ["bull", "bull"]})

        joined = RegimeJoinService().join_equity_curve(_equity(1), labels)

        assert list(joined["regime_trend"]) == ["bull"]


@pytest.mark.parametrize("labels", [pd.DataFrame(), pd.DataFrame({"timestamp": _timestamps(3)})])
def test_no_labels_returns_the_curve_unchanged(labels: pd.DataFrame) -> None:
    equity = _equity(3)

    joined = RegimeJoinService().join_equity_curve(equity, labels)

    pd.testing.assert_frame_equal(joined, equity)
