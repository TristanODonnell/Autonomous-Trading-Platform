from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from autonomous_trading_platform.contracts.execution.execution_policy_config import PolicyMode
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent


@dataclass(frozen=True)
class ChildOrderIntent:
    """A child order produced by slicing a parent OrderIntent through an execution policy.

    bar_offset is the number of bars after the parent signal bar (plus latency) at which
    this child should execute.  slice_index 0 always has bar_offset 0 (first bar of the
    execution window, same bar as the parent signal adjusted for latency).

    Parent-child traceability is preserved via:
      - child_intent_id: deterministic UUID5 (parent.intent_id + slice_index + policy_mode)
      - parent_intent_id: the original parent OrderIntent.intent_id
      - child.intent.metadata["parent_intent_id"]: embedded for downstream analytics
    """

    child_intent_id: UUID
    parent_intent_id: UUID
    slice_index: int
    bar_offset: int
    intent: OrderIntent
    policy_mode: PolicyMode
