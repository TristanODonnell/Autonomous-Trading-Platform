from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
)
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent


@runtime_checkable
class IExecutionModel(Protocol):
    """Shared execution-model abstraction for policy-aware order planning.

    Separates execution policy semantics (shared) from runtime mechanics:
      - Simulation: child intents flow through the simulated fill pipeline
        (latency, participation caps, probabilistic fills, limit realism).
      - Live/paper: child intents are submitted to the broker adapter.

    plan() converts a single parent OrderIntent into one or more
    (bar_offset, child_intent) pairs:
      - bar_offset=0: execute on the same bar as the signal (adjusted for latency).
      - bar_offset=N: execute N bars after the signal bar (in addition to latency).

    PASSTHROUGH must always return [(0, original_intent)] so callers can use this
    interface unconditionally without changing behaviour for unconfigured strategies.
    """

    def plan(
        self,
        intent: OrderIntent,
        config: ExecutionPolicyConfig,
        reference_price: Decimal | None = None,
    ) -> list[tuple[int, OrderIntent]]:
        """Return list of (bar_offset, child_intent) pairs for the given parent intent."""
        ...
