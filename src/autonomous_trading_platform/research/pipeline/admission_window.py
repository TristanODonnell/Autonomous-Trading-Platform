"""The admission window: selection, evaluation and holdout, laid out in one place.

Everything follows from the holdout date (``research/pipeline/holdout.py``):

    selection    the SELECTION_MONTHS calendar months before the evaluation piece
    evaluation   the EVALUATION_MONTHS calendar months before the holdout date
    test folds   the evaluation piece's sessions cut into TEST_FOLDS equal parts
    holdout      from the holdout date to the day the layout is asked for

The pieces are bounded in calendar months, as they were decided (9 / 9 / 6, research
plan, "Universe over the long window"). Inside a piece everything is counted in trading
sessions, and every span is given by its first and last session, both included. Two
spans that follow each other therefore share no day.

The calendar is handed in (a function listing the trading days between two dates, both
included), so this module reads nothing and the research package does not depend on
the runtime clock.

A window that cannot hold the layout is refused with the reason. It is never shortened
to fit: a shorter layout is a different experiment.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta

SELECTION_MONTHS = 9
EVALUATION_MONTHS = 9
TEST_FOLDS = 3
# A Sharpe over fewer sessions than this is close to a coin flip (standard error of the
# yearly figure about 2 at 63 sessions).
MIN_TEST_FOLD_SESSIONS = 60

TradingDays = Callable[[date, date], Iterable[date]]


class AdmissionWindowError(ValueError):
    """The window asked for cannot hold the admission layout."""


@dataclass(frozen=True)
class SessionSpan:
    """A run of trading sessions, given by its first and last session (both included)."""

    first: date
    last: date
    sessions: int

    def __post_init__(self) -> None:
        if self.sessions < 1:
            raise ValueError("a span holds at least one session")
        if self.last < self.first:
            raise ValueError("a span ends on or after the day it starts")


@dataclass(frozen=True)
class AdmissionWindowLayout:
    selection: SessionSpan
    evaluation: SessionSpan
    # Inside the evaluation piece, in order, not overlapping, covering all of it.
    test_folds: tuple[SessionSpan, ...]
    holdout_start: date
    # The holdout sessions there are so far; None when there is none yet.
    holdout: SessionSpan | None

    @property
    def start(self) -> date:
        """The first day any stage reads."""
        return self.selection.first

    @property
    def last_gate_day(self) -> date:
        """The last day a gate may read. Every later day is holdout."""
        return self.evaluation.last


def months_before(day: date, months: int) -> date:
    """The same day of the month ``months`` earlier; the month's last day when that day
    does not exist there (the 31st, or the 29th and 30th in February)."""
    if months < 0:
        raise ValueError("months must not be negative")
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def admission_window_layout(
    *,
    holdout_start: date,
    as_of: date,
    data_first: date,
    data_last: date,
    trading_days: TradingDays,
    selection_months: int = SELECTION_MONTHS,
    evaluation_months: int = EVALUATION_MONTHS,
    test_folds: int = TEST_FOLDS,
    min_test_fold_sessions: int = MIN_TEST_FOLD_SESSIONS,
) -> AdmissionWindowLayout:
    """Lay the admission window out.

    holdout_start : the first day no gate may read.
    as_of         : the day the research runs. The holdout is reported up to it.
    data_first,
    data_last     : the first and last day the bars dataset covers.
    trading_days  : the trading days between two dates, both included.

    Raises AdmissionWindowError when the research runs before the holdout date, when
    the bars do not reach back to the selection piece's first day or forward to the
    evaluation piece's last session, or when a test fold would be shorter than
    ``min_test_fold_sessions``.
    """
    if selection_months < 1 or evaluation_months < 1:
        raise ValueError("selection_months and evaluation_months must be positive")
    if test_folds < 1:
        raise ValueError("test_folds must be positive")
    if data_last < data_first:
        raise ValueError("data_last is before data_first")

    if as_of < holdout_start:
        raise AdmissionWindowError(
            f"the research runs on {as_of}, before the holdout date {holdout_start}: "
            "the evaluation piece would not be complete"
        )

    evaluation_from = months_before(holdout_start, evaluation_months)
    selection_from = months_before(evaluation_from, selection_months)

    if data_first > selection_from:
        raise AdmissionWindowError(
            f"the bars start on {data_first}; the selection piece starts on {selection_from} "
            f"({selection_months + evaluation_months} months before the holdout date "
            f"{holdout_start})"
        )

    selection_days = _sessions(trading_days, selection_from, evaluation_from - timedelta(days=1))
    evaluation_days = _sessions(trading_days, evaluation_from, holdout_start - timedelta(days=1))
    if not selection_days:
        raise AdmissionWindowError(
            f"no trading session from {selection_from} to {evaluation_from - timedelta(days=1)}: "
            "the selection piece is empty"
        )
    if not evaluation_days:
        raise AdmissionWindowError(
            f"no trading session from {evaluation_from} to {holdout_start - timedelta(days=1)}: "
            "the evaluation piece is empty"
        )
    if data_last < evaluation_days[-1]:
        raise AdmissionWindowError(
            f"the bars end on {data_last}; the evaluation piece ends on {evaluation_days[-1]}"
        )

    shortest = len(evaluation_days) // test_folds
    if shortest < min_test_fold_sessions:
        raise AdmissionWindowError(
            f"the evaluation piece holds {len(evaluation_days)} sessions: {test_folds} test "
            f"fold(s) would have {shortest} each, fewer than the {min_test_fold_sessions} a "
            "fold needs"
        )

    holdout_days = _sessions(trading_days, holdout_start, min(as_of, data_last))
    return AdmissionWindowLayout(
        selection=_span(selection_days),
        evaluation=_span(evaluation_days),
        test_folds=tuple(_span(part) for part in _equal_parts(evaluation_days, test_folds)),
        holdout_start=holdout_start,
        holdout=_span(holdout_days) if holdout_days else None,
    )


def _sessions(trading_days: TradingDays, first: date, last: date) -> list[date]:
    """The calendar's sessions from ``first`` to ``last``, in order, each once. Days the
    calendar hands back outside the span asked for are dropped."""
    if last < first:
        return []
    return sorted({day for day in trading_days(first, last) if first <= day <= last})


def _span(days: list[date]) -> SessionSpan:
    return SessionSpan(first=days[0], last=days[-1], sessions=len(days))


def _equal_parts(days: list[date], parts: int) -> list[list[date]]:
    """``days`` cut into ``parts`` runs whose lengths differ by one at most, the longer
    ones first."""
    size, extra = divmod(len(days), parts)
    cut: list[list[date]] = []
    start = 0
    for index in range(parts):
        end = start + size + (1 if index < extra else 0)
        cut.append(days[start:end])
        start = end
    return cut
