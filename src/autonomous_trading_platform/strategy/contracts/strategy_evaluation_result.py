from datetime import datetime

from pydantic import BaseModel

from autonomous_trading_platform.contracts.trading.signal import Signal


class StrategyEvaluationResult(BaseModel):
    strategy_id: str
    bar_timestamp: datetime
    signals: list[Signal]
