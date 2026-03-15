from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.safety.errors import (
    DailyNotionalLimitExceededError,
    GrossExposureLimitExceededError,
    SymbolExposureLimitExceededError,
)
from autonomous_trading_platform.safety.services.pre_trade_risk_service import (
    PreTradeRiskService,
)


class FakeRiskStateReader:
    def __init__(
        self,
        gross_exposure: float = 0.0,
        symbol_exposure: float = 0.0,
        daily_notional: float = 0.0,
    ) -> None:
        self._gross_exposure = gross_exposure
        self._symbol_exposure = symbol_exposure
        self._daily_notional = daily_notional

    def get_gross_exposure(self) -> float:
        return self._gross_exposure

    def get_symbol_exposure(self, symbol: str) -> float:
        return self._symbol_exposure

    def get_daily_notional_traded(self, trading_date) -> float:
        return self._daily_notional


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            max_gross_exposure=100_000.0,
            max_symbol_exposure=10_000.0,
            max_daily_notional_traded=25_000.0,
        ),
    )


def _order_intent(
    *,
    symbol: str = "AAPL",
    quantity: float = 10,
    limit_price: float | None = 100.0,
    reference_price: float | None = None,
) -> OrderIntent:
    return cast(
        OrderIntent,
        SimpleNamespace(
            symbol=symbol,
            quantity=quantity,
            limit_price=limit_price,
            reference_price=reference_price,
        ),
    )


def test_pre_trade_risk_allows_order_when_within_limits() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=50_000.0,
            symbol_exposure=5_000.0,
            daily_notional=10_000.0,
        ),
    )

    order_intent = _order_intent(quantity=10, limit_price=100.0)
    now = datetime.now(UTC)

    service.assert_order_allowed(order_intent=order_intent, now=now)


def test_pre_trade_risk_blocks_when_gross_exposure_limit_exceeded() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=99_500.0,
            symbol_exposure=1_000.0,
            daily_notional=5_000.0,
        ),
    )

    order_intent = _order_intent(quantity=10, limit_price=100.0)
    now = datetime.now(UTC)

    with pytest.raises(GrossExposureLimitExceededError, match="gross exposure"):
        service.assert_order_allowed(order_intent=order_intent, now=now)


def test_pre_trade_risk_blocks_when_symbol_exposure_limit_exceeded() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=9_500.0,
            daily_notional=5_000.0,
        ),
    )

    order_intent = _order_intent(symbol="AAPL", quantity=10, limit_price=100.0)
    now = datetime.now(UTC)

    with pytest.raises(SymbolExposureLimitExceededError, match="AAPL"):
        service.assert_order_allowed(order_intent=order_intent, now=now)


def test_pre_trade_risk_blocks_when_daily_notional_limit_exceeded() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=2_000.0,
            daily_notional=24_500.0,
        ),
    )

    order_intent = _order_intent(quantity=10, limit_price=100.0)
    now = datetime.now(UTC)

    with pytest.raises(DailyNotionalLimitExceededError, match="daily notional"):
        service.assert_order_allowed(order_intent=order_intent, now=now)


def test_estimate_order_notional_uses_limit_price_when_present() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(quantity=5, limit_price=123.0, reference_price=999.0)

    notional = service._estimate_order_notional(order_intent)

    assert notional == 615.0


def test_estimate_order_notional_uses_reference_price_when_limit_price_missing() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(quantity=5, limit_price=None, reference_price=120.0)

    notional = service._estimate_order_notional(order_intent)

    assert notional == 600.0


def test_estimate_order_notional_uses_absolute_quantity() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(quantity=-5, limit_price=100.0)

    notional = service._estimate_order_notional(order_intent)

    assert notional == 500.0


def test_pre_trade_risk_raises_when_no_price_is_available() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(limit_price=None, reference_price=None)

    with pytest.raises(ValueError, match="limit_price or reference_price"):
        service.assert_order_allowed(
            order_intent=order_intent,
            now=datetime.now(UTC),
        )
