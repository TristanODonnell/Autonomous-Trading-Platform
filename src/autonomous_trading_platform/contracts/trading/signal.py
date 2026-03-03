# autonomous_trading_platform/contracts/trading/signal.py
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.common.types import Quantity, UTCDateTime


class Signal(BaseModel):
    signal_id: UUID
    run_id: UUID
    timestamp: UTCDateTime
    bar_timestamp: UTCDateTime
    strategy_id: str
    symbol: str
    direction: SignalDirection
    confidence: float | None = None
    target_position: Quantity | None = None
    params: dict[str, Any] | None = None
