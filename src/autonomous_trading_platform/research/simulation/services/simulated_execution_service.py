from __future__ import annotations

import copy
import dataclasses
import random
from decimal import ROUND_DOWN, Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelService,
)

# Stable namespaces for deterministic UUID5 generation.
# These are fixed constants so the same logical key always maps to the same UUID.
_FILL_NS = uuid5(NAMESPACE_URL, "autonomous-trading-platform:fill")
_ORDER_NS = uuid5(NAMESPACE_URL, "autonomous-trading-platform:order")


@dataclasses.dataclass(slots=True)
class FillBatch:
    """Result of a fill pass: completed fills plus orders that could not be fully executed."""

    fills: list[Fill]
    # OrderIntents (or duck-typed equivalents) carrying the remaining unfilled quantity.
    # The engine reschedules these for the next eligible bar.
    carry_forward: list[Any]


class SimulatedExecutionService:
    def __init__(
        self,
        simulation_cost_model_service: SimulationCostModelService,
        fill_model_config: SimulatedFillModelConfig,
        rng: random.Random | None = None,
    ):
        self.simulation_cost_model_service = simulation_cost_model_service
        self.fill_model_config = fill_model_config
        # Isolated RNG for all stochastic decisions (F-01 partial fills, F-02 rejection,
        # R-07 touch probability). Caller must provide a seeded instance via reset_for_run()
        # before each simulation run to guarantee deterministic replay.
        self._rng: random.Random = rng if rng is not None else random.Random()
        # Monotonic counter reset at the start of each run; used as part of the
        # deterministic key for fill_id / broker_order_id generation (P-01).
        self._fill_counter: int = 0

    def reset_for_run(self, rng: random.Random | None = None) -> None:
        """Reset per-run mutable state before each simulation run.

        Must be called by SimulationRunner at the start of every run to guarantee:
          - deterministic fill ID generation (fill counter reset to 0)
          - isolated, seeded RNG (no cross-run state leakage)
        """
        self._fill_counter = 0
        if rng is not None:
            self._rng = rng

    @property
    def cost_model_summary(self) -> dict:
        return {
            k: str(v) if isinstance(v, Decimal) else v
            for k, v in dataclasses.asdict(self.simulation_cost_model_service.config).items()
        }

    @property
    def slippage_model_summary(self) -> dict[str, object]:
        return cast(
            dict[str, object],
            self.simulation_cost_model_service.slippage_model.config_summary(),
        )

    def fill(
        self,
        *,
        order_intents: list[Any],
        bars_at_timestamp: dict[str, Any],
    ) -> FillBatch:
        fills: list[Fill] = []
        carry_forward: list[Any] = []

        for intent in order_intents:
            bar = bars_at_timestamp.get(intent.symbol)
            if bar is None:
                continue

            if intent.qty is None or intent.qty <= 0:
                continue

            original_qty = Decimal(str(intent.qty))
            order_type = self._normalize_enum_value(intent.order_type)

            # F-02: Apply stochastic rejection before any order-type-specific logic.
            # Rejected orders produce no fill and do not carry forward.
            if self._is_order_rejected():
                continue

            if order_type == "limit":
                # R-07: Limit orders check eligibility first (gap-open vs. intrabar touch),
                # apply touch-probability gate for intrabar touches, then run participation
                # cap and probabilistic fill on the eligible quantity.
                batch = self._process_limit_intent(intent, bar, original_qty)
                fills.extend(batch.fills)
                carry_forward.extend(batch.carry_forward)
                continue

            # Market orders: apply participation cap and probabilistic fill, then execute.
            # Step 1 (R-04): Apply volume participation cap.
            capped_qty = self._cap_by_participation(original_qty, bar)

            if capped_qty <= Decimal("0"):
                # Bar volume too thin to fill anything; carry the entire order forward.
                carry_forward.append(self._make_carry_forward(intent, original_qty))
                continue

            # Step 2 (F-01): Apply probabilistic partial-fill reduction.
            actual_fill_qty = self._apply_probabilistic_fill(capped_qty)

            if actual_fill_qty <= Decimal("0"):
                # Probabilistic decision: nothing executes this bar.
                carry_forward.append(self._make_carry_forward(intent, original_qty))
                continue

            remaining_qty = original_qty - actual_fill_qty

            # Step 3: Execute with the resolved quantity.
            fill_result = self._build_fill_for_intent(
                intent=intent, bar=bar, effective_qty=actual_fill_qty
            )

            if fill_result is not None:
                fills.append(fill_result)
                if remaining_qty > Decimal("0"):
                    carry_forward.append(self._make_carry_forward(intent, remaining_qty))

        return FillBatch(fills=fills, carry_forward=carry_forward)

    # ------------------------------------------------------------------
    # Volume participation helpers (R-04)
    # ------------------------------------------------------------------

    def _cap_by_participation(self, qty: Decimal, bar: Any) -> Decimal:
        rate = self.fill_model_config.max_volume_participation_rate
        if rate is None:
            return qty

        bar_volume = getattr(bar, "volume", None)
        if bar_volume is None or bar_volume <= 0:
            # No usable volume data; nothing can fill this bar.
            return Decimal("0")

        max_fill = (Decimal(str(bar_volume)) * Decimal(str(rate))).to_integral_value(
            rounding=ROUND_DOWN
        )
        return min(qty, max_fill)

    # ------------------------------------------------------------------
    # Probabilistic partial-fill helpers (F-01)
    # ------------------------------------------------------------------

    def _apply_probabilistic_fill(self, qty: Decimal) -> Decimal:
        """Optionally reduce fill quantity stochastically to simulate queue fragmentation.

        When partial_fill_probability triggers:
          - sample a fraction in [partial_fill_min_fraction, partial_fill_max_fraction]
          - return floor(qty * fraction), at least 1 share
        Otherwise return qty unchanged.
        """
        prob = self.fill_model_config.partial_fill_probability
        if prob is None:
            return qty

        if self._rng.random() < prob:
            fraction = self._rng.uniform(
                self.fill_model_config.partial_fill_min_fraction,
                self.fill_model_config.partial_fill_max_fraction,
            )
            partial = (qty * Decimal(str(fraction))).to_integral_value(rounding=ROUND_DOWN)
            # Guarantee at least 1 share so a triggered partial fill always makes progress.
            return max(partial, Decimal("1"))

        return qty

    def _make_carry_forward(self, intent: Any, qty: Decimal) -> Any:
        try:
            return intent.model_copy(update={"qty": qty})
        except AttributeError:
            cf = copy.copy(intent)
            cf.qty = qty
            return cf

    # ------------------------------------------------------------------
    # Fill construction
    # ------------------------------------------------------------------

    def _build_fill_for_intent(
        self, *, intent: Any, bar: Any, effective_qty: Decimal
    ) -> Fill | None:
        order_type = self._normalize_enum_value(intent.order_type)

        if order_type == "market":
            return self._fill_market_order(intent=intent, bar=bar, qty=effective_qty)

        if order_type == "limit":
            return self._fill_limit_order(intent=intent, bar=bar, qty=effective_qty)

        return None

    def _fill_market_order(self, *, intent: Any, bar: Any, qty: Decimal) -> Fill:
        # latency_bars=0 → order executes this bar, priced at close.
        # latency_bars>=1 → order was deferred; the engine passes the execution bar's
        #   data, so price at open reflects entry after bar N closes.
        reference_price = bar.close if self.fill_model_config.latency_bars == 0 else bar.open
        return self._create_fill(intent=intent, bar=bar, reference_price=reference_price, qty=qty)

    def _is_order_rejected(self) -> bool:
        """Draw from seeded RNG to determine if an order is stochastically rejected (F-02)."""
        prob = self.fill_model_config.order_rejection_probability
        if prob is None:
            return False
        return self._rng.random() < prob

    def _unfilled_limit_result(self, intent: Any, original_qty: Decimal) -> FillBatch:
        """Return the appropriate empty-fill result for an eligible-but-unfilled limit order.

        When expire_unfilled_limit_orders is True, the order expires (no carry-forward).
        Otherwise the full quantity carries forward for re-evaluation on the next bar (R-07).
        This helper is NOT used for not-price-eligible orders — those are always dropped.
        """
        if self.fill_model_config.expire_unfilled_limit_orders:
            return FillBatch(fills=[], carry_forward=[])
        return FillBatch(fills=[], carry_forward=[self._make_carry_forward(intent, original_qty)])

    def _process_limit_intent(self, intent: Any, bar: Any, original_qty: Decimal) -> FillBatch:
        """Evaluate a limit order for eligibility, price improvement, and touch uncertainty (R-07).

        Execution flow:
          1. Determine eligibility: gap-open (bar.open through limit) or intrabar touch.
             Not-price-eligible orders are dropped (no carry-forward, regardless of expiry).
          2. If intrabar touch and fill_probability_on_touch is set: draw from seeded RNG;
             if rejected → expire or carry forward depending on expire_unfilled_limit_orders.
          3. Apply volume participation cap (R-04).
             If zero volume → expire or carry forward depending on expire_unfilled_limit_orders.
          4. Apply probabilistic partial-fill reduction (F-01).
          5. Emit fill at realistic price; carry forward any remaining quantity.
        """
        limit_price = getattr(intent, "limit_price", None)
        if limit_price is None:
            return FillBatch(fills=[], carry_forward=[])

        limit_price_d = Decimal(str(limit_price))
        bar_open_d = Decimal(str(bar.open))
        side = self._normalize_enum_value(intent.side)

        if side == "buy":
            if bar_open_d <= limit_price_d:
                # Gap-open: market opened at or below the buy limit.
                # Fill at bar.open (price improvement over limit_price).
                is_gap_open = True
                fill_price = bar_open_d
            elif Decimal(str(bar.low)) <= limit_price_d:
                # Intrabar touch: bar.open > limit_price but bar.low reaches it.
                is_gap_open = False
                fill_price = limit_price_d
            else:
                # Bar never reached limit; always drop regardless of expiry flag —
                # there is no price activity to expire against.
                return FillBatch(fills=[], carry_forward=[])
        elif side == "sell":
            if bar_open_d >= limit_price_d:
                # Gap-open: market opened at or above the sell limit.
                # Fill at bar.open (price improvement over limit_price).
                is_gap_open = True
                fill_price = bar_open_d
            elif Decimal(str(bar.high)) >= limit_price_d:
                # Intrabar touch: bar.open < limit_price but bar.high reaches it.
                is_gap_open = False
                fill_price = limit_price_d
            else:
                return FillBatch(fills=[], carry_forward=[])
        else:
            return FillBatch(fills=[], carry_forward=[])

        # Queue-position uncertainty for intrabar touches (R-07).
        # Gap-open fills bypass this gate — the market opened through the limit so
        # the order was at the front of the queue.
        if not is_gap_open:
            touch_prob = self.fill_model_config.fill_probability_on_touch
            if touch_prob is not None and self._rng.random() >= touch_prob:
                # Touch-rejected: expire or carry forward depending on DAY expiry config.
                return self._unfilled_limit_result(intent, original_qty)

        # Step 1 (R-04): Apply volume participation cap.
        capped_qty = self._cap_by_participation(original_qty, bar)
        if capped_qty <= Decimal("0"):
            # Zero bar volume: expire or carry forward.
            return self._unfilled_limit_result(intent, original_qty)

        # Step 2 (F-01): Apply probabilistic partial-fill reduction.
        actual_fill_qty = self._apply_probabilistic_fill(capped_qty)
        if actual_fill_qty <= Decimal("0"):
            return self._unfilled_limit_result(intent, original_qty)

        remaining_qty = original_qty - actual_fill_qty
        fill_result = self._create_fill(
            intent=intent, bar=bar, reference_price=fill_price, qty=actual_fill_qty
        )

        # Partial-fill remainder always carries forward — quantity that did execute
        # is done; only the unfilled portion needs rescheduling.
        cf: list[Any] = []
        if remaining_qty > Decimal("0"):
            cf.append(self._make_carry_forward(intent, remaining_qty))

        return FillBatch(fills=[fill_result], carry_forward=cf)

    def _fill_limit_order(self, *, intent: Any, bar: Any, qty: Decimal) -> Fill | None:
        # Legacy path retained for _build_fill_for_intent compatibility.
        # The primary limit-order path is now _process_limit_intent, called directly
        # from fill() before participation cap and probabilistic fill are applied.
        limit_price = getattr(intent, "limit_price", None)

        if limit_price is None:
            return None

        limit_price_decimal = Decimal(str(limit_price))
        side = self._normalize_enum_value(intent.side)

        if side == "buy":
            crossed = Decimal(str(bar.low)) <= limit_price_decimal
        elif side == "sell":
            crossed = Decimal(str(bar.high)) >= limit_price_decimal
        else:
            return None

        if not crossed:
            return None

        return self._create_fill(
            intent=intent,
            bar=bar,
            reference_price=limit_price_decimal,
            qty=qty,
        )

    def _create_fill(
        self,
        *,
        intent: Any,
        bar: Any,
        reference_price: Any,
        qty: Decimal,
    ) -> Fill:
        context = SlippageContext(
            quantity=qty,
            bar_volume=(
                Decimal(str(bar.volume)) if getattr(bar, "volume", None) is not None else None
            ),
            bar_high=(Decimal(str(bar.high)) if getattr(bar, "high", None) is not None else None),
            bar_low=(Decimal(str(bar.low)) if getattr(bar, "low", None) is not None else None),
            bar_close=(
                Decimal(str(bar.close)) if getattr(bar, "close", None) is not None else None
            ),
        )
        costs = self.simulation_cost_model_service.apply_costs(
            side=intent.side,
            reference_price=reference_price,
            quantity=qty,
            context=context,
        )

        self._fill_counter += 1
        # Deterministic IDs (P-01): stable across reruns given identical inputs + seed.
        # Key encodes run identity + intent identity + monotonic fill index to ensure
        # uniqueness even for fragmented / carry-forward fills from the same intent.
        _fill_key = f"{intent.run_id}:{intent.intent_id}:{self._fill_counter}"
        fill_id = str(uuid5(_FILL_NS, _fill_key))
        broker_order_id = str(uuid5(_ORDER_NS, _fill_key))

        return Fill(
            fill_id=fill_id,
            broker_order_id=broker_order_id,
            intent_id=intent.intent_id,
            run_id=intent.run_id,
            symbol=intent.symbol,
            timestamp=bar.timestamp,
            side=intent.side,
            quantity=qty,
            price=costs.fill_price,
            fees=costs.commission,
            liquidity=None,
            venue="simulated",
        )

    def _normalize_enum_value(self, value: Any) -> str:
        raw_value = getattr(value, "value", value)
        return str(raw_value).lower()
