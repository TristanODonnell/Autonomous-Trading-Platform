# autonomous_trading_platform/execution/policy/execution_policy_engine.py
"""
ExecutionPolicyEngine — orchestrates execution policy for a single OrderIntent.

It sits between the idempotency check and execution_context.order_execution_service.submit()
inside run_order_submission_job.

Responsibilities:
  1. Resolve execution policy config for the intent
  2. Fetch market data (mid-price, bid/ask) from AlpacaBrokerClient
  3. Decide order type: market vs limit via OrderTypeResolver.
  4. Build TWAP slice schedule if policy_mode=TWAP
  5. Build VWAP-lite slice schedule if policy_mode=VWAP_LITE
  6. Compute expected SlippageMeasurement and CostBreakdown
  7. Return ExecutionPolicyResult with the transformed intent + all analytics.

What it does NOT do:
  - Submit the order (that remains in order_execution_service.submit()).
  - Persist analytics (that's FillQualityAnalyser's job, post-fill).
  - Apply risk checks (those stay in order_throttle_service, before this runs).

Integration in run_order_submission_job:
    # After idempotency check, before submit():
    policy_result = execution_policy_engine.apply(
        intent=intent,
        config=execution_policy_config,
    )
    response = execution_context.order_execution_service.submit(
        policy_result.transformed_intent
    )
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
    PolicyMode,
)
from autonomous_trading_platform.contracts.execution.execution_policy_result import (
    ExecutionPolicyResult,
    OrderSlice,
)
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.policy.errors import MissingMarketDataError
from autonomous_trading_platform.execution.policy.order_type_resolver import OrderTypeResolver
from autonomous_trading_platform.execution.policy.slippage_calculator import SlippageCalculator
from autonomous_trading_platform.execution.policy.twap_slicer import TWAPSlicer
from autonomous_trading_platform.execution.policy.vwap_lite_slicer import VWAPLiteSlicer
from autonomous_trading_platform.observability.logging import get_logger

logger = get_logger(__name__)


class ExecutionPolicyEngine:
    """
    Applies execution policy to a single OrderIntent and returns
    the transformed intent with full analytics metadata.

    Constructor args:
        broker_client:      AlpacaBrokerClient for live quote fetching.
        order_type_resolver: OrderTypeResolver instance.
        twap_slicer:        TWAPSlicer instance.
        vwap_lite_slicer:   VWAPLiteSlicer instance.

    Designed for dependency injection; components are swappable for testing.
    """

    def __init__(
        self,
        *,
        broker_client: AlpacaBrokerClient,
        order_type_resolver: OrderTypeResolver | None = None,
        twap_slicer: TWAPSlicer | None = None,
        vwap_lite_slicer: VWAPLiteSlicer | None = None,
    ) -> None:
        self._broker_client = broker_client
        self._order_type_resolver = order_type_resolver or OrderTypeResolver()
        self._twap_slicer = twap_slicer or TWAPSlicer()
        self._vwap_lite_slicer = vwap_lite_slicer or VWAPLiteSlicer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        *,
        intent: OrderIntent,
        config: ExecutionPolicyConfig,
    ) -> ExecutionPolicyResult:
        """
        Apply execution policy to an intent.

        Always returns an ExecutionPolicyResult.  Slippage/cost fields are
        None if market data is unavailable (non-fatal; logged as warning).

        Args:
            intent:  Original OrderIntent from trading evaluation.
            config:  ExecutionPolicyConfig for this strategy/instrument.

        Returns:
            ExecutionPolicyResult with transformed_intent ready for submission.
        """
        symbol = intent.symbol
        mode = config.policy_mode

        # --- Step 1: fetch live market data ---
        mid_price, ask_price, bid_price = self._fetch_quote(symbol)

        # --- Step 2: resolve order type (market / limit / passthrough) ---
        try:
            transformed_intent = self._order_type_resolver.resolve(
                intent=intent,
                config=config,
                mid_price=mid_price,
            )
        except MissingMarketDataError:
            # LIMIT mode without a quote: log and fall back to market.
            logger.warning(
                "execution_policy.limit_fallback_to_market",
                extra={
                    "symbol": symbol,
                    "policy_mode": mode.value,
                    "reason": "no_quote_available",
                },
            )
            from autonomous_trading_platform.execution.policy.order_type_resolver import (
                OrderTypeResolver,
            )

            transformed_intent = OrderTypeResolver._force_market(intent)

        # --- Step 3: build slice schedule (TWAP / VWAP-lite) ---
        slices: list[OrderSlice] = []
        slice_metadata: dict[str, Any] = {}

        if mode == PolicyMode.TWAP:
            slices = self._twap_slicer.build_slices(
                intent=transformed_intent,
                config=config.twap,
                mid_price=mid_price,
            )
            slice_metadata = self._twap_slicer.to_metadata(slices)
            transformed_intent = self._embed_slice_metadata(transformed_intent, slice_metadata)

        elif mode == PolicyMode.VWAP_LITE:
            slices = self._vwap_lite_slicer.build_slices(
                intent=transformed_intent,
                config=config.vwap_lite,
            )
            slice_metadata = self._vwap_lite_slicer.to_metadata(
                slices, config.vwap_lite.volume_profile
            )
            transformed_intent = self._embed_slice_metadata(transformed_intent, slice_metadata)

        # --- Step 4: compute expected slippage and cost ---
        expected_slippage = None
        expected_cost = None

        if mid_price is not None:
            qty = self._resolve_quantity(intent)
            if qty is not None and qty > Decimal("0"):
                slippage_calc = SlippageCalculator(config.slippage)
                expected_slippage = slippage_calc.expected_slippage(
                    side=intent.side,
                    reference_price=mid_price,
                    quantity=qty,
                    ask_price=ask_price,
                    bid_price=bid_price,
                )
                expected_cost = slippage_calc.cost_breakdown(
                    expected_slippage,
                    ask_price=ask_price,
                    bid_price=bid_price,
                )

        # --- Step 5: assemble result ---
        policy_metadata: dict[str, Any] = {
            "policy_mode": mode.value,
            "num_slices": len(slices),
            **slice_metadata,
        }
        if mid_price is not None:
            policy_metadata["reference_price"] = str(mid_price)
        if expected_slippage is not None:
            policy_metadata["expected_slippage_bps"] = str(expected_slippage.slippage_bps)

        logger.info(
            "execution_policy.applied",
            extra={
                "symbol": symbol,
                "policy_mode": mode.value,
                "order_type": transformed_intent.order_type.value,
                "num_slices": len(slices),
                "reference_price": str(mid_price) if mid_price else None,
                "expected_slippage_bps": (
                    str(expected_slippage.slippage_bps) if expected_slippage else None
                ),
                "intent_id": str(intent.intent_id),
                "strategy_id": intent.strategy_id,
                "run_id": str(intent.run_id),
            },
        )

        return ExecutionPolicyResult(
            transformed_intent=transformed_intent,
            policy_mode_applied=mode,
            reference_price=mid_price,
            expected_slippage=expected_slippage,
            expected_cost=expected_cost,
            slices=slices,
            policy_metadata=policy_metadata,
        )

    def _fetch_quote(self, symbol: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        """
        Fetch latest quote for symbol.  Returns (mid, ask, bid) or (None, None, None).

        Non-fatal: if quote fetch fails we log a warning and return Nones.
        The engine will then skip slippage computation and fall back to
        passthrough / market if LIMIT was requested.
        """
        try:
            quotes = self._broker_client.get_latest_quotes([symbol])
            quote = quotes.get(symbol)
            if not quote:
                logger.warning(
                    "execution_policy.no_quote",
                    extra={"symbol": symbol, "reason": "symbol_not_in_response"},
                )
                return None, None, None

            ask = Decimal(str(quote.get("ap") or quote.get("ask_price", 0)))
            bid = Decimal(str(quote.get("bp") or quote.get("bid_price", 0)))

            if ask <= Decimal("0") or bid <= Decimal("0"):
                logger.warning(
                    "execution_policy.invalid_quote",
                    extra={"symbol": symbol, "ask": str(ask), "bid": str(bid)},
                )
                return None, None, None

            mid = (ask + bid) / Decimal("2")
            return mid, ask, bid

        except Exception as exc:
            logger.warning(
                "execution_policy.quote_fetch_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return None, None, None

    @staticmethod
    def _embed_slice_metadata(intent: OrderIntent, metadata: dict[str, Any]) -> OrderIntent:
        existing = dict(intent.metadata or {})
        existing.update(metadata)
        return cast(OrderIntent, intent.model_copy(update={"metadata": existing}))

    @staticmethod
    def _resolve_quantity(intent: OrderIntent) -> Decimal | None:
        if intent.qty is not None:
            return Decimal(str(intent.qty))
        return None  # notional orders: skip slippage qty computation
