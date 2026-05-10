from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    snapshot_id: UUID
    broker: str
    trading_environment: str
    broker_account_id: str
    account_status: str | None

    cash: Decimal | None
    buying_power: Decimal | None
    equity: Decimal | None
    portfolio_value: Decimal | None

    observed_at: datetime
