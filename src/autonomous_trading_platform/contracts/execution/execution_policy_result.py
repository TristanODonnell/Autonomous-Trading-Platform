# autonomous_trading_platform/contracts/execution/execution_policy_result.py

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import Money, Quantity
from autonomous_trading_platform.contracts.execution.execution_policy_config import PolicyMode
from autonomous_trading_platform.contracts.trading.cost_breakdown import CostBreakdown
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.slippage_measurement import SlippageMeasurement


class OrderSlice(BaseModel):
    slice_index: int
    scheduled_offset_minutes: float  # minutes after submission to send this slice
    quantity: Quantity
    weight: Decimal  # fraction of total order (sums to ~1.0)
    limit_price: Money | None = None  # set for limit slices


class ExecutionPolicyResult(BaseModel):
    transformed_intent: OrderIntent
    policy_mode_applied: PolicyMode
    reference_price: Money | None = None
    expected_slippage: SlippageMeasurement | None = None
    expected_cost: CostBreakdown | None = None
    slices: list[OrderSlice] = []
    policy_metadata: dict[str, Any] = {}
