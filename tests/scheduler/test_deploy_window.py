from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts.deploy_window import freeze_status

_ET = ZoneInfo("America/New_York")


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_ET)


@pytest.mark.parametrize(
    ("now", "expected_frozen"),
    [
        # Monday 2026-10-05, a normal session (09:30-16:00 ET)
        (_et(2026, 10, 5, 8, 59), False),
        (_et(2026, 10, 5, 9, 0), True),
        (_et(2026, 10, 5, 12, 0), True),
        (_et(2026, 10, 5, 16, 29), True),
        (_et(2026, 10, 5, 16, 30), False),
        (_et(2026, 10, 5, 20, 0), False),
        # Weekend
        (_et(2026, 10, 3, 12, 0), False),
        # Thanksgiving 2026: exchange holiday on a weekday
        (_et(2026, 11, 26, 12, 0), False),
        # Day after Thanksgiving: half day, closes 13:00 ET, so the freeze ends 13:30
        (_et(2026, 11, 27, 13, 29), True),
        (_et(2026, 11, 27, 13, 30), False),
    ],
)
def test_freeze_status(now: datetime, expected_frozen: bool) -> None:
    frozen, reason = freeze_status(now)
    assert frozen is expected_frozen, reason


def test_freeze_tracks_daylight_saving() -> None:
    # 14:15 UTC is 09:15 ET in winter (frozen) but 10:15 ET in summer (also frozen);
    # 13:15 UTC is 08:15 ET in winter (open for deploys) and 09:15 ET in summer (frozen).
    winter = datetime(2026, 1, 12, 13, 15, tzinfo=ZoneInfo("UTC"))
    summer = datetime(2026, 7, 13, 13, 15, tzinfo=ZoneInfo("UTC"))
    assert freeze_status(winter)[0] is False
    assert freeze_status(summer)[0] is True
