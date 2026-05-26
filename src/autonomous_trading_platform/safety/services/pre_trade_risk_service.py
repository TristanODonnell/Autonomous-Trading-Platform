import logging
from datetime import datetime
from decimal import Decimal

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.safety.errors import (
    DailyNotionalLimitExceededError,
    GrossExposureLimitExceededError,
    PortfolioSymbolExposureLimitExceededError,
    SymbolExposureLimitExceededError,
)
from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
    PortfolioRiskStateReader,
)

logger = logging.getLogger(__name__)


class PreTradeRiskService:
    def __init__(
        self,
        settings: Settings,
        risk_state_reader,
        *,
        portfolio_risk_state_reader: PortfolioRiskStateReader | None = None,
        max_portfolio_symbol_exposure_usd: float | None = None,
        max_portfolio_symbol_pct: float | None = None,
        audit_log_repo=None,
    ) -> None:
        self.settings = settings
        self.risk_state_reader = risk_state_reader
        self._portfolio_reader = portfolio_risk_state_reader
        self._max_portfolio_symbol_exposure_usd = self._resolve_optional_limit(
            explicit=max_portfolio_symbol_exposure_usd,
            configured=getattr(settings, "max_portfolio_symbol_exposure_usd", None),
        )
        self._max_portfolio_symbol_pct = self._resolve_optional_limit(
            explicit=max_portfolio_symbol_pct,
            configured=getattr(settings, "max_portfolio_symbol_pct", None),
        )
        self._audit_log_repo = audit_log_repo

    def assert_order_allowed(self, order_intent: OrderIntent, now: datetime) -> None:
        order_notional = self._estimate_order_notional(order_intent)

        current_gross_exposure = float(self.risk_state_reader.get_gross_exposure())
        current_symbol_exposure = float(
            self.risk_state_reader.get_symbol_exposure(order_intent.symbol)
        )
        current_daily_notional = float(self.risk_state_reader.get_daily_notional_traded(now.date()))
        current_reserved_cash = float(self.risk_state_reader.get_reserved_cash())
        current_position_qty = float(self.risk_state_reader.get_position_qty(order_intent.symbol))

        exposure_delta = self._calculate_exposure_delta(
            order_intent=order_intent,
            order_notional=order_notional,
            current_position_qty=current_position_qty,
            current_symbol_exposure=current_symbol_exposure,
        )

        projected_gross_exposure = current_gross_exposure + exposure_delta
        projected_symbol_exposure = current_symbol_exposure + exposure_delta
        projected_daily_notional = current_daily_notional + order_notional

        if projected_gross_exposure > float(self.settings.max_gross_exposure):
            raise GrossExposureLimitExceededError(
                f"Projected gross exposure {projected_gross_exposure} exceeds "
                f"limit {self.settings.max_gross_exposure}."
            )

        if projected_symbol_exposure > float(self.settings.max_symbol_exposure):
            raise SymbolExposureLimitExceededError(
                f"Projected symbol exposure for {order_intent.symbol} "
                f"{projected_symbol_exposure} exceeds limit "
                f"{self.settings.max_symbol_exposure}."
            )

        if projected_daily_notional > float(self.settings.max_daily_notional_traded):
            raise DailyNotionalLimitExceededError(
                f"Projected daily notional {projected_daily_notional} exceeds "
                f"limit {self.settings.max_daily_notional_traded}."
            )

        self._assert_reserved_cash_capacity(
            order_intent=order_intent,
            order_notional=order_notional,
            current_reserved_cash=current_reserved_cash,
        )

        self._assert_portfolio_symbol_exposure(
            order_intent=order_intent,
            order_notional=order_notional,
            now=now,
        )

    def _estimate_order_notional(self, order_intent: OrderIntent) -> float:
        if order_intent.qty is None:
            raise ValueError("Order intent qty must be set for pre-trade risk checks.")

        price = self._resolve_reference_price(order_intent)
        return abs(float(order_intent.qty)) * price

    def _resolve_reference_price(self, order_intent: OrderIntent) -> float:
        if order_intent.limit_price is not None:
            return float(order_intent.limit_price)
        raise ValueError("Order intent must provide limit_price.")

    def _calculate_exposure_delta(
        self,
        *,
        order_intent: OrderIntent,
        order_notional: float,
        current_position_qty: float,
        current_symbol_exposure: float,
    ) -> float:
        signed_order_qty = self._signed_order_qty(order_intent)

        if current_position_qty == 0:
            return order_notional

        if current_position_qty * signed_order_qty > 0:
            return order_notional

        reduction_notional = min(order_notional, current_symbol_exposure)
        remainder_notional = max(order_notional - reduction_notional, 0.0)

        return remainder_notional - reduction_notional

    def _signed_order_qty(self, order_intent: OrderIntent) -> float:
        if order_intent.qty is None:
            raise ValueError("Order intent qty must be set for pre-trade risk checks.")

        abs_qty = abs(float(order_intent.qty))

        if order_intent.side == Side.BUY:
            return abs_qty
        if order_intent.side == Side.SELL:
            return -abs_qty

        raise ValueError(f"Unsupported order side: {order_intent.side}")

    def _assert_reserved_cash_capacity(
        self,
        *,
        order_intent: OrderIntent,
        order_notional: float,
        current_reserved_cash: float,
    ) -> None:
        if order_intent.side != Side.BUY:
            return

        projected_reserved_cash = current_reserved_cash + order_notional
        max_reserved_cash = float(self.settings.max_reserved_cash)

        if projected_reserved_cash > max_reserved_cash:
            raise GrossExposureLimitExceededError(
                f"Projected reserved cash {projected_reserved_cash} exceeds "
                f"limit {max_reserved_cash}."
            )

    def _assert_portfolio_symbol_exposure(
        self,
        *,
        order_intent: OrderIntent,
        order_notional: float,
        now: datetime,
    ) -> None:
        if self._portfolio_reader is None:
            return
        if (
            self._max_portfolio_symbol_exposure_usd is None
            and self._max_portfolio_symbol_pct is None
        ):
            return

        symbol = order_intent.symbol
        current_usd = float(self._portfolio_reader.get_symbol_exposure_usd(symbol))
        portfolio_exposure_delta = self._calculate_portfolio_symbol_exposure_delta(
            order_intent=order_intent,
            order_notional=order_notional,
            current_symbol_exposure=current_usd,
        )
        projected_usd = current_usd + portfolio_exposure_delta

        total_equity = float(self._portfolio_reader.total_equity)
        current_pct = (current_usd / total_equity) if total_equity > 0 else 0.0
        projected_pct = (projected_usd / total_equity) if total_equity > 0 else 0.0

        if projected_usd <= current_usd:
            logger.debug(
                "pre_trade_risk.portfolio_symbol_exposure_reduced",
                extra={
                    "symbol": symbol,
                    "strategy_id": order_intent.strategy_id,
                    "current_usd": current_usd,
                    "projected_usd": projected_usd,
                    "current_pct": current_pct,
                    "projected_pct": projected_pct,
                    "order_notional": order_notional,
                    "run_id": str(order_intent.run_id),
                },
            )
            return

        usd_breached = (
            self._max_portfolio_symbol_exposure_usd is not None
            and projected_usd > self._max_portfolio_symbol_exposure_usd
        )
        pct_breached = (
            self._max_portfolio_symbol_pct is not None
            and projected_pct > self._max_portfolio_symbol_pct
        )

        if not usd_breached and not pct_breached:
            logger.debug(
                "pre_trade_risk.portfolio_symbol_exposure_ok",
                extra={
                    "symbol": symbol,
                    "strategy_id": order_intent.strategy_id,
                    "current_usd": current_usd,
                    "projected_usd": projected_usd,
                    "current_pct": current_pct,
                    "projected_pct": projected_pct,
                    "order_notional": order_notional,
                    "run_id": str(order_intent.run_id),
                },
            )
            return

        logger.warning(
            "pre_trade_risk.portfolio_symbol_exposure_blocked",
            extra={
                "symbol": symbol,
                "strategy_id": order_intent.strategy_id,
                "current_usd": current_usd,
                "projected_usd": projected_usd,
                "current_pct": current_pct,
                "projected_pct": projected_pct,
                "limit_usd": self._max_portfolio_symbol_exposure_usd,
                "limit_pct": self._max_portfolio_symbol_pct,
                "order_notional": order_notional,
                "run_id": str(order_intent.run_id),
            },
        )

        if self._audit_log_repo is not None:
            self._audit_log_repo.record_operator_action(
                action="PORTFOLIO_SYMBOL_EXPOSURE_BLOCKED",
                actor=order_intent.strategy_id,
                reason="portfolio symbol concentration limit exceeded",
                occurred_at=now,
                component="pre_trade_risk",
                metadata={
                    "strategy_id": order_intent.strategy_id,
                    "symbol": symbol,
                    "current_symbol_exposure_pct": f"{current_pct:.4f}",
                    "projected_symbol_exposure_pct": f"{projected_pct:.4f}",
                    "configured_limit_pct": (
                        f"{self._max_portfolio_symbol_pct:.4f}"
                        if self._max_portfolio_symbol_pct is not None
                        else None
                    ),
                    "current_symbol_exposure_usd": f"{current_usd:.2f}",
                    "projected_symbol_exposure_usd": f"{projected_usd:.2f}",
                    "configured_limit_usd": (
                        f"{self._max_portfolio_symbol_exposure_usd:.2f}"
                        if self._max_portfolio_symbol_exposure_usd is not None
                        else None
                    ),
                    "order_notional": f"{order_notional:.2f}",
                    "run_id": str(order_intent.run_id),
                },
            )

        raise PortfolioSymbolExposureLimitExceededError(
            symbol=symbol,
            strategy_id=order_intent.strategy_id,
            current_exposure_usd=current_usd,
            projected_exposure_usd=projected_usd,
            limit_usd=self._max_portfolio_symbol_exposure_usd,
            current_exposure_pct=current_pct,
            projected_exposure_pct=projected_pct,
            limit_pct=self._max_portfolio_symbol_pct,
        )

    def _calculate_portfolio_symbol_exposure_delta(
        self,
        *,
        order_intent: OrderIntent,
        order_notional: float,
        current_symbol_exposure: float,
    ) -> float:
        if order_intent.side == Side.BUY:
            return order_notional

        if order_intent.side == Side.SELL:
            reader = self._portfolio_reader
            has_quantity = reader is not None and reader.has_symbol_quantity(order_intent.symbol)
            current_qty = (
                float(reader.get_symbol_quantity(order_intent.symbol))
                if has_quantity and reader is not None
                else 0.0
            )
            if current_qty > 0 or (not has_quantity and current_symbol_exposure > 0):
                reduction_notional = min(order_notional, current_symbol_exposure)
                remainder_notional = max(order_notional - reduction_notional, 0.0)
                return remainder_notional - reduction_notional
            return order_notional

        raise ValueError(f"Unsupported order side: {order_intent.side}")

    def _resolve_optional_limit(
        self,
        *,
        explicit: float | None,
        configured,
    ) -> float | None:
        value = explicit if explicit is not None else configured
        if value is None:
            return None
        if isinstance(value, Decimal | int | float):
            return float(value)
        return None
