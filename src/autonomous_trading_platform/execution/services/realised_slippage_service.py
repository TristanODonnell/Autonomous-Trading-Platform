# autonomous_trading_platform/execution/services/realised_slippage_service.py
from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity
from autonomous_trading_platform.contracts.execution.execution_policy_config import PolicyMode
from autonomous_trading_platform.contracts.execution.execution_policy_result import (
    ExecutionPolicyResult,
)
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.slippage_measurement import SlippageMeasurement
from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import FillQualityMetrics
from autonomous_trading_platform.storage.sor.repositories.fill_quality_metrics_repository import (
    FillQualityMetricsRepository,
)

BPS_DENOMINATOR = Decimal("10000")
_ADVERSE_SLIPPAGE_THRESHOLD_BPS = Decimal("10")  # flag fills worse than 10 bps


class RealisedSlippageService:
    """
    Calculates realised slippage from fills and persists fill quality
    analytics to the fill_quality_metrics table (TASK-204).

    Two-phase write:
      Phase 1 — record_submission_context()
          Called in run_order_submission_job immediately after submit().
          Writes the row with pre-execution data: reference_price,
          expected slippage, submission_latency, policy context.
          fill_price / fill_timestamp / fill_latency are NULL at this point.

      Phase 2 — record_fill_actuals()
          Called in run_order_reconciliation_job when a Fill arrives.
          Updates the existing row with actual fill_price, fill_timestamp,
          realised slippage, fill_vs_expected_bps, and is_adverse_fill.
    """

    def __init__(self, repository: FillQualityMetricsRepository) -> None:
        self._repo = repository

    def calculate(
        self,
        side: Side,
        reference_price: Money,
        fill_price: Money,
        quantity: Quantity,
    ) -> SlippageMeasurement:
        if reference_price <= 0:
            raise ValueError("reference_price must be positive")
        if fill_price <= 0:
            raise ValueError("fill_price must be positive")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        if side == Side.BUY:
            slippage_per_share = fill_price - reference_price
        elif side == Side.SELL:
            slippage_per_share = reference_price - fill_price
        else:
            raise ValueError(f"unsupported side: {side}")

        slippage_notional = slippage_per_share * quantity
        slippage_bps = (slippage_per_share / reference_price) * BPS_DENOMINATOR
        return SlippageMeasurement(
            side=side,
            reference_price=reference_price,
            fill_price=fill_price,
            quantity=quantity,
            slippage_per_share=slippage_per_share,
            slippage_notional=slippage_notional,
            slippage_bps=slippage_bps,
        )

    def record_submission_context(
        self,
        *,
        intent: OrderIntent,
        broker_order: BrokerOrder,
        policy_result: ExecutionPolicyResult,
        submit_duration_seconds: float,
        now_utc: datetime,
    ) -> None:
        """
        Persist a fill_quality_metrics row immediately after order submission.

        fill_price, fill_timestamp, fill_latency_seconds, and realised slippage
        fields are left NULL — they are populated by record_fill_actuals() when
        the fill arrives during reconciliation.
        """
        expected_slippage = policy_result.expected_slippage
        expected_cost = policy_result.expected_cost
        qty = self._resolve_quantity(intent)

        row = FillQualityMetrics(
            record_id=str(uuid4()),
            run_id=str(intent.run_id),
            intent_id=str(intent.intent_id),
            fill_id=None,  # populated in phase 2
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            signal_timestamp=intent.timestamp,
            submission_timestamp=now_utc,
            fill_timestamp=None,  # populated in phase 2
            submission_latency_seconds=submit_duration_seconds,
            fill_latency_seconds=None,  # populated in phase 2
            reference_price=(
                policy_result.reference_price
                if policy_result.reference_price is not None
                else Decimal("0")
            ),
            fill_price=None,  # populated in phase 2
            expected_fill_price=(
                Decimal(str(expected_slippage.fill_price))
                if expected_slippage is not None
                else None
            ),
            slippage_per_share=None,  # populated in phase 2 (realised)
            slippage_notional=None,  # populated in phase 2 (realised)
            slippage_bps=None,  # populated in phase 2 (realised)
            fill_vs_expected_bps=None,  # populated in phase 2
            commission_cost=(
                Decimal(str(expected_cost.commission_cost)) if expected_cost is not None else None
            ),
            spread_cost=(
                Decimal(str(expected_cost.spread_cost)) if expected_cost is not None else None
            ),
            slippage_cost=(
                Decimal(str(expected_cost.slippage_cost)) if expected_cost is not None else None
            ),
            total_cost=(
                Decimal(str(expected_cost.total_cost)) if expected_cost is not None else None
            ),
            policy_mode=policy_result.policy_mode_applied.value,
            quantity=qty,
            is_adverse_fill=None,  # populated in phase 2
            policy_metadata_json=policy_result.policy_metadata or None,
        )

        self._repo.insert(row)

    def record_fill_actuals(
        self,
        *,
        fill: Fill,
        reference_price: Money | None = None,
        now_utc: datetime,
    ) -> None:
        """
        Update the fill_quality_metrics row with post-fill actuals.

        Called from run_order_reconciliation_job when a Fill is confirmed.
        Looks up the existing row by intent_id and updates it in place.

        If no row exists (e.g. analytics were disabled at submit time or the
        record was not written due to a non-fatal error), a minimal row is
        inserted so fill data is never silently lost.

        Args:
            fill:            The confirmed Fill from BrokerOrderMapper.
            reference_price: Mid-price at submission time, carried from the
                             phase-1 row.  If the row exists this is ignored
                             (we use the stored value).  If inserting fresh,
                             this is used as the reference.
            now_utc:         Current UTC time (for fill_timestamp if fill
                             doesn't carry its own timestamp).
        """
        intent_id_str = str(fill.intent_id)
        existing = self._repo.get_by_intent_id(intent_id_str)

        fill_price = Decimal(str(fill.price))
        fill_qty = Decimal(str(fill.quantity))
        fill_ts = fill.timestamp if fill.timestamp is not None else now_utc

        # Determine reference price: prefer the stored value (set at submit),
        # fall back to caller-supplied value, then fill_price (zero slippage).
        if existing is not None and existing.reference_price:
            ref_price = Decimal(str(existing.reference_price))
        elif reference_price is not None:
            ref_price = Decimal(str(reference_price))
        else:
            ref_price = fill_price

        # Compute realised slippage
        realised = self.calculate(
            side=fill.side,
            reference_price=ref_price,
            fill_price=fill_price,
            quantity=fill_qty,
        )

        # Compute fill_vs_expected_bps (how accurate was our pre-execution model?)
        fill_vs_expected_bps: Decimal | None = None
        if existing is not None and existing.expected_fill_price is not None:
            expected_fp = Decimal(str(existing.expected_fill_price))
            if expected_fp > Decimal("0"):
                fill_vs_expected_bps = (
                    (fill_price - expected_fp) / expected_fp * BPS_DENOMINATOR
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Fill latency: fill_timestamp - submission_timestamp
        fill_latency: float | None = None
        if existing is not None and existing.submission_timestamp is not None:
            sub_ts = existing.submission_timestamp
            if sub_ts.tzinfo is None:
                sub_ts = sub_ts.replace(tzinfo=UTC)
            fill_latency = (fill_ts - sub_ts).total_seconds()

        is_adverse = abs(realised.slippage_bps) > _ADVERSE_SLIPPAGE_THRESHOLD_BPS

        if existing is not None:
            existing.fill_id = fill.fill_id
            existing.fill_price = fill_price
            existing.fill_timestamp = fill_ts
            existing.fill_latency_seconds = fill_latency
            existing.slippage_per_share = realised.slippage_per_share
            existing.slippage_notional = realised.slippage_notional
            existing.slippage_bps = realised.slippage_bps
            existing.fill_vs_expected_bps = fill_vs_expected_bps
            existing.is_adverse_fill = is_adverse
            # quantity: update in case the fill is partial
            existing.quantity = fill_qty
            self._repo.upsert(existing)
        else:
            # Phase-1 row missing — insert a minimal record so fill data
            # is never silently dropped.
            row = FillQualityMetrics(
                record_id=str(uuid4()),
                run_id=str(fill.run_id),
                intent_id=intent_id_str,
                fill_id=fill.fill_id,
                strategy_id="unknown",  # strategy_id not on Fill; best effort
                symbol=fill.symbol,
                signal_timestamp=fill_ts,
                submission_timestamp=fill_ts,
                fill_timestamp=fill_ts,
                submission_latency_seconds=0.0,
                fill_latency_seconds=fill_latency,
                reference_price=ref_price,
                fill_price=fill_price,
                expected_fill_price=None,
                slippage_per_share=realised.slippage_per_share,
                slippage_notional=realised.slippage_notional,
                slippage_bps=realised.slippage_bps,
                fill_vs_expected_bps=fill_vs_expected_bps,
                commission_cost=None,
                spread_cost=None,
                slippage_cost=None,
                total_cost=None,
                policy_mode=PolicyMode.PASSTHROUGH.value,
                quantity=fill_qty,
                is_adverse_fill=is_adverse,
                policy_metadata_json=None,
            )
            self._repo.insert(row)

    @staticmethod
    def _resolve_quantity(intent: OrderIntent) -> Decimal | None:
        if intent.qty is not None:
            return Decimal(str(intent.qty))
        return None
