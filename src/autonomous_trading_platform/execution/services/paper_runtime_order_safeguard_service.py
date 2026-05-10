from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from autonomous_trading_platform.execution.errors import OrderNotAllowedForSubmissionError


@dataclass(frozen=True)
class PaperRuntimeOrderSafeguardConfig:
    enabled: bool = False
    max_order_qty: Decimal = Decimal("1")
    max_order_notional: Decimal = Decimal("1.00")
    max_limit_price: Decimal = Decimal("1.00")
    allowed_symbols: frozenset[str] = frozenset()
    require_limit_orders: bool = True

    @classmethod
    def from_settings(cls, settings: Any | None) -> PaperRuntimeOrderSafeguardConfig:
        if settings is None:
            return cls()

        return cls(
            enabled=bool(getattr(settings, "paper_runtime_order_safeguards_enabled", False)),
            max_order_qty=Decimal(str(getattr(settings, "paper_runtime_max_order_qty", "1"))),
            max_order_notional=Decimal(
                str(getattr(settings, "paper_runtime_max_order_notional", "1.00"))
            ),
            max_limit_price=Decimal(
                str(getattr(settings, "paper_runtime_max_limit_price", "1.00"))
            ),
            allowed_symbols=frozenset(
                symbol.strip().upper()
                for symbol in getattr(settings, "paper_runtime_allowed_symbols", [])
                if symbol.strip()
            ),
            require_limit_orders=bool(
                getattr(settings, "paper_runtime_require_limit_orders", True)
            ),
        )


class PaperRuntimeOrderSafeguardService:
    """Fail-closed order caps for explicit external paper-runtime validation."""

    def __init__(self, config: PaperRuntimeOrderSafeguardConfig | None = None) -> None:
        self.config = config or PaperRuntimeOrderSafeguardConfig()

    @classmethod
    def from_settings(cls, settings: Any | None) -> PaperRuntimeOrderSafeguardService:
        return cls(PaperRuntimeOrderSafeguardConfig.from_settings(settings))

    def assert_payload_allowed(self, payload: dict[str, Any]) -> None:
        if not self.config.enabled:
            return

        symbol = str(payload.get("symbol", "")).strip().upper()
        if not symbol:
            self._reject("paper runtime safeguard rejected order: missing symbol")

        if self.config.allowed_symbols and symbol not in self.config.allowed_symbols:
            self._reject(
                "paper runtime safeguard rejected order: "
                f"symbol {symbol} is not in the allowed paper-runtime symbol list"
            )

        order_type = str(payload.get("type", "")).strip().lower()
        if self.config.require_limit_orders and order_type != "limit":
            self._reject(
                "paper runtime safeguard rejected order: "
                "paper-runtime validation requires limit orders"
            )

        qty = self._optional_decimal(payload.get("qty"), field="qty")
        notional = self._optional_decimal(payload.get("notional"), field="notional")
        limit_price = self._optional_decimal(payload.get("limit_price"), field="limit_price")

        if qty is None and notional is None:
            self._reject("paper runtime safeguard rejected order: qty or notional is required")

        if qty is not None:
            if qty <= 0:
                self._reject("paper runtime safeguard rejected order: qty must be positive")
            if qty > self.config.max_order_qty:
                self._reject(
                    "paper runtime safeguard rejected order: "
                    f"qty {qty} exceeds max {self.config.max_order_qty}"
                )

        if notional is not None:
            if notional <= 0:
                self._reject("paper runtime safeguard rejected order: notional must be positive")
            if notional > self.config.max_order_notional:
                self._reject(
                    "paper runtime safeguard rejected order: "
                    f"notional {notional} exceeds max {self.config.max_order_notional}"
                )

        if limit_price is not None:
            if limit_price <= 0:
                self._reject("paper runtime safeguard rejected order: limit_price must be positive")
            if limit_price > self.config.max_limit_price:
                self._reject(
                    "paper runtime safeguard rejected order: "
                    f"limit_price {limit_price} exceeds max {self.config.max_limit_price}"
                )

        if qty is not None and limit_price is not None:
            estimated_notional = qty * limit_price
            if estimated_notional > self.config.max_order_notional:
                self._reject(
                    "paper runtime safeguard rejected order: "
                    f"estimated notional {estimated_notional} exceeds max "
                    f"{self.config.max_order_notional}"
                )

    def _optional_decimal(self, value: Any, *, field: str) -> Decimal | None:
        if value is None:
            return None

        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise OrderNotAllowedForSubmissionError(
                f"paper runtime safeguard rejected order: {field} is not a valid decimal"
            ) from exc

    def _reject(self, message: str) -> None:
        raise OrderNotAllowedForSubmissionError(message)
