# autonomous_trading_platform/contracts/execution/fill_quality_record.py

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime
from autonomous_trading_platform.contracts.execution.execution_policy_config import PolicyMode


class FillQualityRecord(BaseModel):
    # Identity
    record_id: UUID
    run_id: UUID
    intent_id: UUID
    fill_id: str
    strategy_id: str
    symbol: str

    # Timestamps (for latency computation)
    signal_timestamp: UTCDateTime  # when strategy emitted the signal
    submission_timestamp: UTCDateTime  # when submit() was called
    fill_timestamp: UTCDateTime  # when fill was confirmed
    submission_latency_seconds: float  # submission_timestamp - signal_timestamp
    fill_latency_seconds: float | None  # fill_timestamp - submission_timestamp

    # Price quality
    reference_price: Money  # mid-price at submission
    fill_price: Money  # actual fill price from broker
    expected_fill_price: Money | None  # model-estimated fill price pre-submission
    slippage_per_share: Money
    slippage_notional: Money
    slippage_bps: Decimal
    fill_vs_expected_bps: Decimal | None  # actual fill vs expected fill (model accuracy)

    # Cost
    commission_cost: Money
    spread_cost: Money
    slippage_cost: Money
    total_cost: Money

    # Execution policy context
    policy_mode: PolicyMode
    quantity: Quantity
    policy_metadata: dict[str, Any] = {}

    # Derived quality flag
    is_adverse_fill: bool  # True if slippage_bps > configured threshold
