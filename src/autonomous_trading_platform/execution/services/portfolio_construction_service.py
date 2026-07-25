# autonomous_trading_platform/execution/services/portfolio_construction_service.py

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    SignalDirection,
    TimeInForce,
)
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.execution.services.position_sizer import (
    PositionSizer,
    SizingResult,
)
from autonomous_trading_platform.execution.services.sharpe_scaling_service import (
    SharpeScalingService,
)
from autonomous_trading_platform.execution.services.volatility_scaling_config import (
    VolatilityScalingConfig,
)
from autonomous_trading_platform.execution.services.volatility_scaling_service import (
    VolatilityScalingService,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.metrics import (
    ratp_position_scaling_absent_total,
    ratp_position_scaling_applied_total,
    ratp_vol_scalar_value,
)
from autonomous_trading_platform.portfolio.exceptions import (
    AllocationDeniedError,
    InsufficientCapitalError,
    MissingPositionScalingDataError,
    NoPolicyFoundError,
)
from autonomous_trading_platform.safety.errors import SafetyError
from autonomous_trading_platform.safety.services.pre_trade_risk_service import PreTradeRiskService

ZERO = Decimal("0")
ONE = Decimal("1")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScalarResult:
    """
    Output of PortfolioConstructionService._compute_combined_scalar().

    vol_scalar and sharpe_scalar are the individual per-dimension factors.
    combined_scalar is their product, clamped to (0, 1].  When either
    individual scalar is None the other is used alone (combined = available).
    When both are None combined_scalar is None and scaling_missing_reason
    explains why.

    This result is used for:
      - deciding whether to raise MissingPositionScalingDataError (live mode)
      - emitting POSITION_SCALING_ABSENT metrics (paper mode)
      - populating sizing diagnostics in OrderIntent.metadata
    """

    vol_scalar: Decimal | None
    sharpe_scalar: Decimal | None
    combined_scalar: Decimal | None
    scaling_missing_reason: str | None


class PortfolioConstructionService:
    def __init__(
        self,
        pre_trade_risk_service: PreTradeRiskService,
        position_sizer: PositionSizer,
        volatility_scaling_service: VolatilityScalingService | None = None,
        sharpe_scaling_service: SharpeScalingService | None = None,
        volatility_scaling_config: VolatilityScalingConfig | None = None,
        trading_environment: TradingEnvironment | None = None,
    ) -> None:
        self.pre_trade_risk_service = pre_trade_risk_service
        self._position_sizer = position_sizer
        self._vol_scaling = volatility_scaling_service
        self._sharpe_scaling = sharpe_scaling_service
        self._vol_config = volatility_scaling_config or VolatilityScalingConfig()
        self._trading_environment = trading_environment

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate_order_intents(
        self,
        signals: list[Signal],
        positions: Mapping[str, int | Position],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        now: datetime,
        approval_status: GovernanceState | None = None,
        performance_tier: str | None = None,
        recent_closes: dict[str, list[float]] | None = None,
        realized_drawdown: float | None = None,
    ):
        signals_by_symbol = {signal.symbol: signal for signal in signals}
        target_positions, per_symbol_metadata = self._compute_target_positions(
            signals=signals,
            prices=prices,
            strategy_id=strategy_id,
            approval_status=approval_status,
            performance_tier=performance_tier,
            recent_closes=recent_closes,
            realized_drawdown=realized_drawdown,
        )
        deltas = self.calculate_deltas(positions, target_positions)

        for delta in deltas:
            symbol = str(delta["symbol"])
            try:
                order_intent = self.build_order_intent(
                    delta=delta,
                    prices=prices,
                    run_id=run_id,
                    strategy_id=strategy_id,
                    bar_timestamp=bar_timestamp,
                    now=now,
                )
            except KeyError:
                logger.warning(
                    "order_intent.skipped_missing_price",
                    extra={"symbol": symbol, "run_id": str(run_id)},
                )
                continue
            combined_metadata: dict[str, Any] = {}
            signal = signals_by_symbol.get(symbol)
            if signal is not None:
                combined_metadata["signal_id"] = str(signal.signal_id)
                combined_metadata["signal_generated_at"] = signal.timestamp.isoformat()
            sizing_meta = per_symbol_metadata.get(symbol)
            if sizing_meta:
                combined_metadata.update(sizing_meta)
            if combined_metadata:
                order_intent.metadata = {**(order_intent.metadata or {}), **combined_metadata}
            try:
                self.pre_trade_risk_service.assert_order_allowed(order_intent, now=now)
            except SafetyError as exc:
                logger.warning(
                    "order_intent.skipped_risk_rejected",
                    extra={
                        "symbol": symbol,
                        "run_id": str(run_id),
                        "reason": f"{exc.__class__.__name__}: {exc}",
                    },
                )
                continue
            yield order_intent

    # ------------------------------------------------------------------
    # Internal sizing pipeline
    # ------------------------------------------------------------------

    def _compute_target_positions(
        self,
        signals: list[Signal],
        prices: dict[str, float],
        strategy_id: str,
        approval_status: GovernanceState | None = None,
        performance_tier: str | None = None,
        recent_closes: dict[str, list[float]] | None = None,
        realized_drawdown: float | None = None,
    ) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
        """
        Returns (target_positions, per_symbol_sizing_metadata).

        per_symbol_sizing_metadata maps each BUY symbol to a dict ready to
        merge into OrderIntent.metadata.  Symbols with no sizing (SELL/FLAT
        or missing price) have no entry.
        """
        target_positions: dict[str, int] = {}
        per_symbol_metadata: dict[str, dict[str, Any]] = {}

        for signal in signals:
            if signal.direction in (SignalDirection.SELL, SignalDirection.FLAT):
                target_positions[signal.symbol] = 0
                continue

            if signal.direction != SignalDirection.BUY:
                logger.warning(
                    "portfolio_construction.unknown_direction",
                    extra={
                        "strategy_id": strategy_id,
                        "symbol": signal.symbol,
                        "direction": str(signal.direction),
                    },
                )
                target_positions[signal.symbol] = 0
                continue

            raw_price = prices.get(signal.symbol)
            if raw_price is None:
                logger.warning(
                    "portfolio_construction.missing_price",
                    extra={"strategy_id": strategy_id, "symbol": signal.symbol},
                )
                target_positions[signal.symbol] = 0
                continue

            current_price = Decimal(str(raw_price))
            scalar_result = self._compute_combined_scalar(
                symbol=signal.symbol,
                recent_closes=recent_closes,
            )

            # Mode-based enforcement when scaling is active but data is missing.
            # _enforce_active is True only when scaling is enabled AND at least one
            # scaling service is wired — deliberate no-service configs are not errors.
            if self._enforce_active and scalar_result.combined_scalar is None:
                reason = scalar_result.scaling_missing_reason or "unknown"
                if (
                    self._trading_environment == TradingEnvironment.LIVE
                    and self._vol_config.require_vol_scalar_for_live
                ):
                    raise MissingPositionScalingDataError(
                        strategy_id=strategy_id,
                        symbol=signal.symbol,
                        trading_mode="live",
                        reason=reason,
                    )

            try:
                sizing_result = self._position_sizer.compute_quantity(
                    strategy_id=strategy_id,
                    approval_status=approval_status,
                    symbol=signal.symbol,
                    current_price=current_price,
                    performance_tier=performance_tier,
                    combined_scalar=scalar_result.combined_scalar,
                    realized_drawdown=realized_drawdown,
                )
            except (AllocationDeniedError, NoPolicyFoundError, InsufficientCapitalError) as exc:
                logger.warning(
                    "portfolio_construction.allocation_skipped",
                    extra={
                        "strategy_id": strategy_id,
                        "symbol": signal.symbol,
                        "reason": str(exc),
                    },
                )
                target_positions[signal.symbol] = 0
                continue

            # Post-sizing observability
            if self._enforce_active:
                if scalar_result.combined_scalar is None:
                    self._emit_scaling_absent(
                        strategy_id=strategy_id,
                        symbol=signal.symbol,
                        sizing_result=sizing_result,
                        scalar_result=scalar_result,
                    )
                elif sizing_result.scaling_applied:
                    ratp_vol_scalar_value.record(
                        float(scalar_result.combined_scalar),
                        {"strategy_id": strategy_id},
                    )
                    ratp_position_scaling_applied_total.add(1, {"strategy_id": strategy_id})

            per_symbol_metadata[signal.symbol] = self._build_sizing_metadata(
                scalar_result=scalar_result,
                sizing_result=sizing_result,
            )
            target_positions[signal.symbol] = sizing_result.quantity

        return target_positions, per_symbol_metadata

    # ------------------------------------------------------------------
    # Scalar computation
    # ------------------------------------------------------------------

    def _compute_combined_scalar(
        self,
        symbol: str,
        recent_closes: dict[str, list[float]] | None,
    ) -> ScalarResult:
        """
        Compute the combined vol/Sharpe scalar for a single symbol.

        Multiplication rules:
          - Both services unavailable → combined = None (caller decides fallback)
          - One service returns None (insufficient bars) → use the other alone
          - Both return values → multiply, clamp to (0, 1]

        The clamp ensures we only scale positions down, never up.  Neither
        individual scalar can exceed 1.0 by construction (both services cap
        at 1.0), so the product can only equal 1.0 when both are at maximum.
        Explicit clamping is a defensive invariant for future extension points
        (e.g. leverage-aware scaling) that might relax individual service caps.
        """
        if not self._vol_config.volatility_scaling_enabled:
            return ScalarResult(
                vol_scalar=None,
                sharpe_scalar=None,
                combined_scalar=None,
                scaling_missing_reason="scaling_disabled",
            )

        if self._vol_scaling is None and self._sharpe_scaling is None:
            return ScalarResult(
                vol_scalar=None,
                sharpe_scalar=None,
                combined_scalar=None,
                scaling_missing_reason="no_scaling_services",
            )

        if recent_closes is None:
            return ScalarResult(
                vol_scalar=None,
                sharpe_scalar=None,
                combined_scalar=None,
                scaling_missing_reason="no_closes_data",
            )

        closes = recent_closes.get(symbol)
        if not closes:
            return ScalarResult(
                vol_scalar=None,
                sharpe_scalar=None,
                combined_scalar=None,
                scaling_missing_reason="no_symbol_closes",
            )

        vol_scalar: Decimal | None = None
        if self._vol_scaling is not None:
            vol_scalar = self._vol_scaling.compute_scalar(symbol=symbol, closes=closes)

        sharpe_scalar: Decimal | None = None
        if self._sharpe_scaling is not None:
            sharpe_scalar = self._sharpe_scaling.compute_scalar(symbol=symbol, closes=closes)

        if vol_scalar is None and sharpe_scalar is None:
            return ScalarResult(
                vol_scalar=None,
                sharpe_scalar=None,
                combined_scalar=None,
                scaling_missing_reason="insufficient_bars",
            )

        combined = (vol_scalar or ONE) * (sharpe_scalar or ONE)

        # Clamp to (0, 1] — we only scale down, never up.  Both services are
        # designed to return values ≤ 1.0, so this clamp normally has no effect.
        # It exists as a hard invariant so future extensions that might loosen
        # individual service caps cannot silently produce leverage > 1.0.
        combined = min(combined, ONE)

        logger.debug(
            "portfolio_construction.combined_scalar",
            extra={
                "symbol": symbol,
                "vol_scalar": float(vol_scalar) if vol_scalar is not None else None,
                "sharpe_scalar": float(sharpe_scalar) if sharpe_scalar is not None else None,
                "combined_scalar": float(combined),
            },
        )

        return ScalarResult(
            vol_scalar=vol_scalar,
            sharpe_scalar=sharpe_scalar,
            combined_scalar=combined,
            scaling_missing_reason=None,
        )

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    @property
    def _enforce_active(self) -> bool:
        """True when scaling is enabled and at least one scaling service is wired."""
        return self._vol_config.volatility_scaling_enabled and (
            self._vol_scaling is not None or self._sharpe_scaling is not None
        )

    def _emit_scaling_absent(
        self,
        strategy_id: str,
        symbol: str,
        sizing_result: SizingResult,
        scalar_result: ScalarResult,
    ) -> None:
        reason = scalar_result.scaling_missing_reason or "unknown"
        trading_mode = (
            str(self._trading_environment.value)
            if self._trading_environment is not None
            else "simulation"
        )
        extra: dict[str, Any] = {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "base_notional": float(sizing_result.base_notional),
            "final_notional": float(sizing_result.final_notional),
            "vol_scalar": None,
            "sharpe_scalar": None,
            "trading_mode": trading_mode,
            "scaling_required": str(self._vol_config.require_vol_scalar_for_live),
            "fallback_allowed": str(self._vol_config.allow_full_size_when_scalars_missing),
            "reason_missing": reason,
        }

        if self._trading_environment is not None:
            logger.warning("POSITION_SCALING_ABSENT", extra=extra)
            if (
                sizing_result.base_notional
                >= self._vol_config.min_position_scaling_notional_for_audit
            ):
                ratp_position_scaling_absent_total.add(
                    1,
                    {
                        "strategy_id": strategy_id,
                        "trading_mode": trading_mode,
                        "reason": reason,
                    },
                )
        else:
            logger.debug("position_scaling.scalars_absent_simulation", extra=extra)

    @staticmethod
    def _build_sizing_metadata(
        scalar_result: ScalarResult,
        sizing_result: SizingResult,
    ) -> dict[str, Any]:
        return {
            "sizing": {
                "base_notional": float(sizing_result.base_notional),
                "scaled_notional": float(sizing_result.final_notional),
                "vol_scalar": (
                    float(scalar_result.vol_scalar)
                    if scalar_result.vol_scalar is not None
                    else None
                ),
                "sharpe_scalar": (
                    float(scalar_result.sharpe_scalar)
                    if scalar_result.sharpe_scalar is not None
                    else None
                ),
                "combined_scalar": (
                    float(scalar_result.combined_scalar)
                    if scalar_result.combined_scalar is not None
                    else None
                ),
                "scaling_applied": sizing_result.scaling_applied,
                "scaling_missing_reason": scalar_result.scaling_missing_reason,
            }
        }

    # ------------------------------------------------------------------
    # Delta / intent construction
    # ------------------------------------------------------------------

    def calculate_deltas(
        self,
        current_positions: Mapping[str, int | Position],
        target_positions: dict[str, int],
    ) -> list[dict[str, int | str]]:
        deltas: list[dict[str, int | str]] = []

        all_symbols = sorted(set(current_positions) | set(target_positions))
        for symbol in all_symbols:
            current_position = current_positions.get(symbol, 0)

            if hasattr(current_position, "quantity"):
                current_qty = int(current_position.quantity)
            else:
                current_qty = int(current_position)

            target_qty = target_positions.get(symbol, 0)
            delta_qty = target_qty - current_qty

            if delta_qty != 0:
                deltas.append(
                    {
                        "symbol": symbol,
                        "current_qty": current_qty,
                        "target_qty": target_qty,
                        "delta_qty": delta_qty,
                    }
                )

        return deltas

    def build_order_intent(
        self,
        delta: dict[str, Any],
        prices: dict[str, float],
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        now: datetime,
    ) -> OrderIntent:
        symbol = str(delta["symbol"])
        delta_qty = int(delta["delta_qty"])

        side = Side.BUY if delta_qty > 0 else Side.SELL
        qty = abs(delta_qty)
        raw_price = prices.get(symbol)
        if raw_price is None:
            raise KeyError(f"No price available for {symbol} — cannot build order intent")
        price = Decimal(str(raw_price))

        client_order_id = self._build_client_order_id(
            run_id=run_id,
            strategy_id=strategy_id,
            bar_timestamp=bar_timestamp,
            symbol=symbol,
            side=side,
            qty=qty,
        )
        intent_id = self._build_intent_id(client_order_id=client_order_id)

        return OrderIntent(
            intent_id=intent_id,
            idempotency_key=client_order_id,
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp=now,
            bar_timestamp=bar_timestamp,
            symbol=symbol,
            side=side,
            qty=Decimal(qty),
            notional=None,
            order_type=OrderType.MARKET,
            limit_price=price,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=client_order_id,
            metadata=None,
        )

    @staticmethod
    def _build_client_order_id(
        *,
        run_id: UUID,
        strategy_id: str,
        bar_timestamp: UTCDateTime,
        symbol: str,
        side: Side,
        qty: int,
    ) -> str:
        seed = (
            f"run_id={run_id}|"
            f"strategy_id={strategy_id}|"
            f"bar_timestamp={bar_timestamp.isoformat()}|"
            f"symbol={symbol}|"
            f"side={side.value}|"
            f"qty={qty}"
        )
        deterministic_uuid = uuid5(NAMESPACE_URL, seed)
        return f"{strategy_id}-{symbol}-{deterministic_uuid.hex[:16]}"

    @staticmethod
    def _build_intent_id(*, client_order_id: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"order-intent:{client_order_id}")
