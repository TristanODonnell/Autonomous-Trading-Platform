from datetime import datetime

from pydantic import BaseModel


class StrategyBarReadinessResult(BaseModel):
    target_bar_timestamp: datetime | None
    reason: str | None = None
