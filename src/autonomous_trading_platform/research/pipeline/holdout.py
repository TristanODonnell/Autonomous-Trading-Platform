"""The holdout date: one date, declared here and nowhere else.

Research admits strategies on a window cut in three: selection days rank the
candidates, evaluation days gate them, and the holdout is read by no gate. The
holdout is every day from ``HOLDOUT_START`` on. It is scored once for the final
survivors and reported; it eliminates nobody.

The date is fixed and moves on a schedule (decided 2026-11-16, research plan, "Universe
over the long window"): it only moves forward, ``HOLDOUT_STEP_MONTHS`` at a time, and a
move falls due when the holdout has grown to twelve months. Between moves every month's
candidates are judged on the same selection and evaluation days.

    in force from   holdout starts   next move due
    2026-11-16      2026-05-18       2027-05-18 (to 2026-11-18)

Moving the date is a change to this file, recorded in the plan. Nothing derives it from
today's date: a holdout that follows the clock is read a month at a time by the gates.
"""

from __future__ import annotations

from datetime import date

# The first day no gate may read.
HOLDOUT_START = date(2026, 5, 18)

# How far the date moves when it moves.
HOLDOUT_STEP_MONTHS = 6
