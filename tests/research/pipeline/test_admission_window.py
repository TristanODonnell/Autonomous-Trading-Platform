"""The admission window layout (research plan R2, the hook layout, piece 1).

Selection, evaluation, the test folds and the holdout all follow from the holdout
date. Most tests use a calendar of weekdays, so they do not depend on a holiday list;
one test pins the real dates on the NYSE calendar.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest

from autonomous_trading_platform.research.pipeline.admission_window import (
    AdmissionWindowError,
    AdmissionWindowLayout,
    admission_window_layout,
    months_before,
)
from autonomous_trading_platform.research.pipeline.holdout import (
    HOLDOUT_START,
    HOLDOUT_STEP_MONTHS,
)

_DATA_FIRST = date(2024, 10, 1)
_DATA_LAST = date(2026, 11, 16)
_AS_OF = date(2026, 11, 16)


def _weekdays(first: date, last: date) -> Iterator[date]:
    day = first
    while day <= last:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _layout(**overrides: Any) -> AdmissionWindowLayout:
    arguments: dict[str, Any] = dict(
        holdout_start=HOLDOUT_START,
        as_of=_AS_OF,
        data_first=_DATA_FIRST,
        data_last=_DATA_LAST,
        trading_days=_weekdays,
    )
    arguments.update(overrides)
    return admission_window_layout(**arguments)


class TestTheDeclaredHoldoutDate:
    def test_it_is_the_date_decided(self) -> None:
        assert date(2026, 5, 18) == HOLDOUT_START
        assert HOLDOUT_STEP_MONTHS == 6

    def test_it_is_a_trading_session(self) -> None:
        from autonomous_trading_platform.runtime.clock import RealMarketCalendar

        assert RealMarketCalendar().is_trading_day(HOLDOUT_START)


class TestThePiecesFollowFromTheHoldoutDate:
    def test_nine_months_of_evaluation_end_the_day_before_the_holdout(self) -> None:
        layout = _layout()

        assert layout.evaluation.first == date(2025, 8, 18)
        # 2026-05-17 is a Sunday: the last session is the Friday.
        assert layout.evaluation.last == date(2026, 5, 15)
        assert layout.last_gate_day == date(2026, 5, 15)

    def test_nine_months_of_selection_end_the_day_before_the_evaluation(self) -> None:
        layout = _layout()

        assert layout.selection.first == date(2024, 11, 18)
        assert layout.selection.last == date(2025, 8, 15)
        assert layout.start == date(2024, 11, 18)

    def test_the_holdout_runs_from_its_date_to_the_day_asked_for(self) -> None:
        layout = _layout()

        assert layout.holdout_start == HOLDOUT_START
        assert layout.holdout is not None
        assert layout.holdout.first == date(2026, 5, 18)
        assert layout.holdout.last == date(2026, 11, 16)

    def test_no_piece_shares_a_day_with_the_next(self) -> None:
        layout = _layout()

        assert layout.selection.last < layout.evaluation.first
        assert layout.evaluation.last < layout.holdout_start

    def test_the_selection_and_evaluation_days_do_not_move_with_the_day_asked_for(self) -> None:
        """Every month's candidates are judged on the same days until the date moves."""
        early = _layout(as_of=date(2026, 6, 30))
        late = _layout(as_of=date(2026, 11, 16))

        assert (early.selection, early.evaluation, early.test_folds) == (
            late.selection,
            late.evaluation,
            late.test_folds,
        )
        assert early.holdout is not None and late.holdout is not None
        assert early.holdout.sessions < late.holdout.sessions

    def test_moving_the_holdout_date_moves_every_piece_with_it(self) -> None:
        moved = months_before(date(2026, 11, 18), 0)
        layout = _layout(
            holdout_start=moved, as_of=date(2026, 11, 18), data_last=date(2026, 11, 18)
        )

        assert layout.evaluation.first == date(2026, 2, 18)
        assert layout.selection.first == date(2025, 5, 19)  # the 18th is a Sunday
        assert layout.evaluation.last == date(2026, 11, 17)


class TestTheTestFolds:
    def test_three_folds_cover_the_evaluation_piece_and_nothing_else(self) -> None:
        layout = _layout()
        folds = layout.test_folds

        assert len(folds) == 3
        assert folds[0].first == layout.evaluation.first
        assert folds[-1].last == layout.evaluation.last
        assert sum(fold.sessions for fold in folds) == layout.evaluation.sessions

    def test_a_fold_starts_on_the_session_after_the_one_before_it_ends(self) -> None:
        layout = _layout()
        evaluation_days = list(_weekdays(layout.evaluation.first, layout.evaluation.last))

        for earlier, later in zip(layout.test_folds, layout.test_folds[1:], strict=False):
            assert earlier.last < later.first
            assert evaluation_days.index(later.first) == evaluation_days.index(earlier.last) + 1

    def test_fold_lengths_differ_by_one_session_at_most(self) -> None:
        sizes = [fold.sessions for fold in _layout().test_folds]

        assert max(sizes) - min(sizes) <= 1
        assert sizes == sorted(sizes, reverse=True)

    def test_no_fold_reaches_the_holdout(self) -> None:
        layout = _layout()

        assert all(fold.last < layout.holdout_start for fold in layout.test_folds)
        assert all(fold.first > layout.selection.last for fold in layout.test_folds)

    def test_every_fold_counts_each_session_once(self) -> None:
        layout = _layout()

        for fold in layout.test_folds:
            assert fold.sessions == len(list(_weekdays(fold.first, fold.last)))


class TestOnTheRealCalendar:
    def test_the_real_dates_and_session_counts(self) -> None:
        """Pinned on the NYSE schedule: the numbers the plan records."""
        from autonomous_trading_platform.runtime.clock import RealMarketCalendar

        layout = _layout(trading_days=RealMarketCalendar().trading_days)

        assert (layout.selection.first, layout.selection.last, layout.selection.sessions) == (
            date(2024, 11, 18),
            date(2025, 8, 15),
            185,
        )
        assert (layout.evaluation.first, layout.evaluation.last, layout.evaluation.sessions) == (
            date(2025, 8, 18),
            date(2026, 5, 15),
            188,
        )
        assert [(f.first, f.last, f.sessions) for f in layout.test_folds] == [
            (date(2025, 8, 18), date(2025, 11, 13), 63),
            (date(2025, 11, 14), date(2026, 2, 17), 63),
            (date(2026, 2, 18), date(2026, 5, 15), 62),
        ]
        assert layout.holdout is not None
        assert (layout.holdout.first, layout.holdout.last, layout.holdout.sessions) == (
            date(2026, 5, 18),
            date(2026, 11, 16),
            127,
        )


class TestTheHoldoutSoFar:
    def test_on_the_holdout_date_itself_it_is_one_session(self) -> None:
        layout = _layout(as_of=HOLDOUT_START)

        assert layout.holdout is not None
        assert (layout.holdout.first, layout.holdout.last, layout.holdout.sessions) == (
            HOLDOUT_START,
            HOLDOUT_START,
            1,
        )

    def test_it_is_none_when_no_session_has_come_yet(self) -> None:
        saturday = date(2026, 5, 16)
        layout = _layout(
            holdout_start=saturday, as_of=date(2026, 5, 17), data_last=date(2026, 5, 17)
        )

        assert layout.holdout is None
        assert layout.holdout_start == saturday

    def test_it_stops_where_the_bars_stop(self) -> None:
        layout = _layout(as_of=date(2026, 12, 31), data_last=date(2026, 11, 16))

        assert layout.holdout is not None
        assert layout.holdout.last == date(2026, 11, 16)


class TestRefused:
    def test_research_run_before_the_holdout_date(self) -> None:
        with pytest.raises(AdmissionWindowError, match="before the holdout date 2026-05-18"):
            _layout(as_of=date(2026, 5, 15))

    def test_bars_that_start_after_the_selection_piece(self) -> None:
        with pytest.raises(AdmissionWindowError, match="the bars start on 2024-12-02"):
            _layout(data_first=date(2024, 12, 2))

    def test_bars_that_start_on_the_selection_pieces_first_day_are_enough(self) -> None:
        assert _layout(data_first=date(2024, 11, 18)).selection.first == date(2024, 11, 18)

    def test_bars_that_end_before_the_evaluation_piece_does(self) -> None:
        with pytest.raises(AdmissionWindowError, match="the bars end on 2026-05-14"):
            _layout(data_last=date(2026, 5, 14), as_of=HOLDOUT_START)

    def test_a_fold_shorter_than_the_minimum(self) -> None:
        with pytest.raises(AdmissionWindowError, match="fewer than the 60 a fold needs"):
            _layout(test_folds=4)

    def test_the_layout_is_not_shortened_to_fit(self) -> None:
        """Four folds of 49 sessions fit the days; they are refused, not built."""
        with pytest.raises(AdmissionWindowError):
            _layout(test_folds=4)
        assert len(_layout(test_folds=4, min_test_fold_sessions=40).test_folds) == 4

    def test_a_calendar_with_no_session_in_a_piece(self) -> None:
        def only_after_august_2025(first: date, last: date) -> Iterator[date]:
            return (day for day in _weekdays(first, last) if day >= date(2025, 8, 18))

        with pytest.raises(AdmissionWindowError, match="the selection piece is empty"):
            _layout(trading_days=only_after_august_2025)


class TestMonthsBefore:
    @pytest.mark.parametrize(
        ("day", "months", "expected"),
        [
            (date(2026, 5, 18), 9, date(2025, 8, 18)),
            (date(2026, 5, 18), 18, date(2024, 11, 18)),
            (date(2026, 5, 18), 0, date(2026, 5, 18)),
            (date(2026, 1, 15), 1, date(2025, 12, 15)),
            (date(2026, 5, 31), 3, date(2026, 2, 28)),
            (date(2024, 5, 31), 3, date(2024, 2, 29)),
            (date(2026, 3, 31), 12, date(2025, 3, 31)),
        ],
    )
    def test_same_day_of_the_month_or_its_last_day(
        self, day: date, months: int, expected: date
    ) -> None:
        assert months_before(day, months) == expected

    def test_a_negative_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            months_before(date(2026, 5, 18), -1)


class TestACalendarThatHandsBackTooMuch:
    def test_days_outside_the_span_asked_for_are_dropped(self) -> None:
        def generous(first: date, last: date) -> Iterator[date]:
            return _weekdays(first - timedelta(days=10), last + timedelta(days=10))

        assert _layout(trading_days=generous) == _layout()
