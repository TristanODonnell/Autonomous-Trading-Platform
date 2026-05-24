"""
SimulatedExecutionModel — execution-policy-aware child order planner for simulation.

Converts a parent OrderIntent into a schedule of (bar_offset, child_intent) pairs
according to an ExecutionPolicyConfig.  This is the simulation-side counterpart to
live broker order routing — it reuses the same policy slicers and order-type resolver
as the live ExecutionPolicyEngine but never calls broker code.

Policy behaviour in simulation:

  PASSTHROUGH   → [(0, original_intent)] — identical to pre-policy simulation.
  MARKET        → [(0, market_intent)] — order type forced to MARKET.
  LIMIT         → [(0, limit_intent)] — limit price computed from reference_price.
  TWAP          → [(0, c0), (1, c1), …, (N-1, cN-1)] — one child per slice, each
                  scheduled bar_offset=slice_index bars after the signal bar.
  VWAP_LITE     → same as TWAP but quantities weighted by volume profile.

All child intent IDs are deterministic UUID5 values derived from the parent
intent_id, slice_index, and policy mode — guaranteeing identical replay for the
same dataset, strategy output, policy config, and seed.

Child intents carry parent traceability metadata:
  intent.metadata["parent_intent_id"]  — str(parent.intent_id)
  intent.metadata["slice_index"]       — int
  intent.metadata["policy_mode"]       — str
  intent.metadata["slice_quantity"]    — str(Decimal)
  intent.metadata["slice_weight"]      — str(Decimal)

The resulting child intents flow into the existing simulation fill pipeline
(latency scheduling, participation caps, probabilistic partial fills, limit-order
realism, rejection/expiry modeling) unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from autonomous_trading_platform.contracts.common.enums import OrderType
from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
    PolicyMode,
)
from autonomous_trading_platform.contracts.execution.execution_policy_result import OrderSlice
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.policy.order_type_resolver import OrderTypeResolver
from autonomous_trading_platform.execution.policy.twap_slicer import TWAPSlicer
from autonomous_trading_platform.execution.policy.vwap_lite_slicer import VWAPLiteSlicer

# Stable namespace — same parent_intent_id + slice_index + policy always yields the same child ID.
_CHILD_NS = uuid5(NAMESPACE_URL, "autonomous-trading-platform:child-intent")


class SimulatedExecutionModel:
    """
    Simulation-side implementation of the execution model abstraction (IExecutionModel).

    Converts a parent OrderIntent into a list of (bar_offset, child_intent) pairs
    without touching any broker code.
    """

    def __init__(
        self,
        twap_slicer: TWAPSlicer | None = None,
        vwap_lite_slicer: VWAPLiteSlicer | None = None,
        order_type_resolver: OrderTypeResolver | None = None,
    ) -> None:
        self._twap_slicer = twap_slicer or TWAPSlicer()
        self._vwap_lite_slicer = vwap_lite_slicer or VWAPLiteSlicer()
        self._order_type_resolver = order_type_resolver or OrderTypeResolver()

    def plan(
        self,
        intent: OrderIntent,
        config: ExecutionPolicyConfig,
        reference_price: Decimal | None = None,
    ) -> list[tuple[int, OrderIntent]]:
        """
        Return list of (bar_offset, child_intent) pairs for the given parent intent.

        Args:
            intent:          Parent OrderIntent from strategy evaluation.
            config:          ExecutionPolicyConfig for this simulation run.
            reference_price: Bar close price used as mid-price for limit/slippage math.
                             None is safe — limit fallback and slicing remain functional.

        Returns:
            List of (bar_offset, child_intent) pairs.  bar_offset=0 means the child
            executes on the same bar as the signal (subject to latency_bars offset).
        """
        mode = config.policy_mode

        if mode == PolicyMode.PASSTHROUGH:
            return [(0, intent)]

        if mode == PolicyMode.MARKET:
            transformed = self._order_type_resolver.resolve(
                intent=intent,
                config=config,
                mid_price=reference_price,
            )
            return [(0, transformed)]

        if mode == PolicyMode.LIMIT:
            return [(0, self._resolve_limit(intent, config, reference_price))]

        if mode == PolicyMode.TWAP:
            slices = self._twap_slicer.build_slices(
                intent=intent,
                config=config.twap,
                mid_price=reference_price,
            )
            if not slices:
                return [(0, intent)]
            return self._slices_to_child_intents(intent, slices, mode)

        if mode == PolicyMode.VWAP_LITE:
            slices = self._vwap_lite_slicer.build_slices(
                intent=intent,
                config=config.vwap_lite,
            )
            if not slices:
                return [(0, intent)]
            return self._slices_to_child_intents(intent, slices, mode)

        # Unknown future policy mode: preserve original intent unchanged.
        return [(0, intent)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_limit(
        self,
        intent: OrderIntent,
        config: ExecutionPolicyConfig,
        reference_price: Decimal | None,
    ) -> OrderIntent:
        """Apply LIMIT policy: compute limit_price from reference_price.

        Falls back to the original intent if reference_price is unavailable
        so simulation can continue rather than crashing.
        """
        if reference_price is None:
            return intent
        try:
            return self._order_type_resolver.resolve(
                intent=intent,
                config=config,
                mid_price=reference_price,
            )
        except Exception:
            # MissingMarketDataError or unexpected: fall back gracefully.
            return intent

    def _slices_to_child_intents(
        self,
        parent: OrderIntent,
        slices: list[OrderSlice],
        mode: PolicyMode,
    ) -> list[tuple[int, OrderIntent]]:
        """Convert OrderSlice list into (bar_offset, child_intent) pairs.

        bar_offset equals slice_index so:
          - Slice 0 executes on the signal bar (+ latency_bars).
          - Slice k executes k bars after the signal bar (+ latency_bars).
        """
        result: list[tuple[int, OrderIntent]] = []
        for slice_ in slices:
            child_id = uuid5(
                _CHILD_NS,
                f"{parent.intent_id}:{slice_.slice_index}:{mode.value}",
            )
            order_type = OrderType.LIMIT if slice_.limit_price is not None else OrderType.MARKET
            child_intent = parent.model_copy(
                update={
                    "intent_id": child_id,
                    "qty": slice_.quantity,
                    "order_type": order_type,
                    "limit_price": slice_.limit_price,
                    "metadata": {
                        **(parent.metadata or {}),
                        "parent_intent_id": str(parent.intent_id),
                        "slice_index": slice_.slice_index,
                        "policy_mode": mode.value,
                        "slice_quantity": str(slice_.quantity),
                        "slice_weight": str(slice_.weight),
                    },
                }
            )
            result.append((slice_.slice_index, child_intent))
        return result
