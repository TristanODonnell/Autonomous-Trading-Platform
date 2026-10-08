"""Deploy freeze check for .github/workflows/deploy.yml.

Deploys are frozen from 30 minutes before the NYSE open to 30 minutes after the close
(09:00-16:30 ET on a normal day, earlier on a half day). Weekends and exchange holidays
are never frozen.

Standalone on purpose: the workflow runs it with only exchange_calendars installed.

    python scripts/deploy_window.py                      # now
    python scripts/deploy_window.py --at 2026-10-05T14:00:00+00:00
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

_ET = ZoneInfo("America/New_York")
_MARGIN = timedelta(minutes=30)


def freeze_status(now_utc: datetime) -> tuple[bool, str]:
    """Return (frozen, human-readable reason) for *now_utc*."""
    calendar = xcals.get_calendar("XNYS")
    today_et = now_utc.astimezone(_ET).date()
    if not calendar.is_session(today_et):
        return False, f"{today_et} is not a trading day"

    freeze_start = calendar.session_open(today_et).to_pydatetime() - _MARGIN
    freeze_end = calendar.session_close(today_et).to_pydatetime() + _MARGIN
    window = (
        f"{freeze_start.astimezone(_ET):%H:%M}-{freeze_end.astimezone(_ET):%H:%M} ET on {today_et}"
    )
    if freeze_start <= now_utc < freeze_end:
        return True, f"inside the market-hours freeze ({window})"
    return False, f"outside the market-hours freeze ({window})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--at", help="ISO8601 timestamp to check instead of now")
    args = parser.parse_args()

    now_utc = datetime.fromisoformat(args.at).astimezone(UTC) if args.at else datetime.now(UTC)
    frozen, reason = freeze_status(now_utc)
    print(f"frozen={str(frozen).lower()} ({reason})")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as fh:
            fh.write(f"frozen={str(frozen).lower()}\n")
            fh.write(f"reason={reason}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
