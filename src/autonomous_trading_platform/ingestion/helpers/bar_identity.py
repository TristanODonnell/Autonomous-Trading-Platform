from __future__ import annotations

import hashlib
from datetime import datetime

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis


def build_bar_id(
    symbol: str,
    timestamp: datetime,
    interval: BarInterval,
    price_basis: PriceBasis,
) -> str:

    key = f"{symbol}:{interval.value}:{price_basis.value}:{timestamp.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()
