# autonomous_trading_platform/contracts/trading/signal.py
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import SignalDirection


class Signal(BaseModel):
    signal_id: UUID
    run_id: UUID
    timestamp: datetime
    bar_timestamp: datetime
    strategy_id: str
    symbol: str
    direction: SignalDirection
    confidence: float | None = None
    target_position: float | None = None
    params: dict[str, Any] | None = None
