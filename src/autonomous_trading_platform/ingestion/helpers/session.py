from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from autonomous_trading_platform.contracts.common.enums import MarketSession

EASTERN = ZoneInfo("America/New_York")


def classify_market_session(timestamp_utc: datetime) -> MarketSession:
    ts_et = timestamp_utc.astimezone(EASTERN)
    current_time = ts_et.time()

    if time(4, 0) <= current_time < time(9, 30):
        return MarketSession.PREMARKET
    if time(9, 30) <= current_time < time(16, 0):
        return MarketSession.REGULAR
    if time(16, 0) <= current_time < time(20, 0):
        return MarketSession.POSTMARKET
    return MarketSession.OVERNIGHT
