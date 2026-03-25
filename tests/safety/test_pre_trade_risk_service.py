from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.safety.errors import (
    DailyNotionalLimitExceededError,
    GrossExposureLimitExceededError,
    SymbolExposureLimitExceededError,
)
from autonomous_trading_platform.safety.services.pre_trade_risk_service import (
    PreTradeRiskService,
)
from tests.utilities.factories import make_order_intent


class SettingsStub:
    def __init__(
        self,
        *,
        max_gross_exposure: float = 100_000.0,
        max_symbol_exposure: float = 10_000.0,
        max_daily_notional_traded: float = 25_000.0,
        max_reserved_cash: float = 25_000.0,
    ) -> None:
        self.max_gross_exposure = max_gross_exposure
        self.max_symbol_exposure = max_symbol_exposure
        self.max_daily_notional_traded = max_daily_notional_traded
        self.max_reserved_cash = max_reserved_cash


def _settings(
    *,
    max_gross_exposure: float = 100_000.0,
    max_symbol_exposure: float = 10_000.0,
    max_daily_notional_traded: float = 25_000.0,
    max_reserved_cash: float = 25_000.0,
) -> Settings:
    return cast(
        Settings,
        SettingsStub(
            max_gross_exposure=max_gross_exposure,
            max_symbol_exposure=max_symbol_exposure,
            max_daily_notional_traded=max_daily_notional_traded,
            max_reserved_cash=max_reserved_cash,
        ),
    )


class FakeRiskStateReader:
    def __init__(
        self,
        gross_exposure: float = 0.0,
        symbol_exposure: float = 0.0,
        daily_notional: float = 0.0,
        reserved_cash: float = 0.0,
        position_qty_by_symbol: dict[str, float] | None = None,
    ) -> None:
        self._gross_exposure = gross_exposure
        self._symbol_exposure = symbol_exposure
        self._daily_notional = daily_notional
        self._reserved_cash = reserved_cash
        self._position_qty_by_symbol = position_qty_by_symbol or {}

    def get_gross_exposure(self) -> float:
        return self._gross_exposure

    def get_symbol_exposure(self, _symbol: str) -> float:
        return self._symbol_exposure

    def get_daily_notional_traded(self, _trading_date) -> float:
        return self._daily_notional

    def get_reserved_cash(self) -> float:
        return self._reserved_cash

    def get_position_qty(self, symbol: str) -> float:
        return self._position_qty_by_symbol.get(symbol, 0.0)


def _order_intent(
    *,
    symbol: str = "AAPL",
    qty: float | None = 10,
    limit_price: float | None = 100.0,
    side: Side = Side.BUY,
):
    return make_order_intent(
        symbol=symbol,
        qty=None if qty is None else Decimal(str(qty)),
        limit_price=None if limit_price is None else Decimal(str(limit_price)),
        side=side,
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

    order_intent = _order_intent(qty=10, limit_price=100.0)
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

    order_intent = _order_intent(qty=10, limit_price=100.0)
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

    order_intent = _order_intent(symbol="AAPL", qty=10, limit_price=100.0)
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

    order_intent = _order_intent(qty=10, limit_price=100.0)
    now = datetime.now(UTC)

    with pytest.raises(DailyNotionalLimitExceededError, match="daily notional"):
        service.assert_order_allowed(order_intent=order_intent, now=now)


def test_estimate_order_notional_uses_limit_price_when_present() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(qty=5, limit_price=123.0)

    notional = service._estimate_order_notional(order_intent)

    assert notional == 615.0


def test_estimate_order_notional_raises_when_limit_price_missing() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(qty=5, limit_price=None)

    with pytest.raises(ValueError, match="limit_price"):
        service._estimate_order_notional(order_intent)


def test_estimate_order_notional_uses_absolute_quantity() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(qty=-5, limit_price=100.0)

    notional = service._estimate_order_notional(order_intent)

    assert notional == 500.0


def test_pre_trade_risk_raises_when_no_price_is_available() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(limit_price=None)

    with pytest.raises(ValueError, match="limit_price"):
        service.assert_order_allowed(
            order_intent=order_intent,
            now=datetime.now(UTC),
        )


# -------------------------
# New expansion tests
# -------------------------


def test_pre_trade_risk_allows_order_exactly_at_gross_limit() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=99_000.0,
            symbol_exposure=1_000.0,
            daily_notional=5_000.0,
        ),
    )

    order_intent = _order_intent(qty=10, limit_price=100.0)  # 1,000 notional

    service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_allows_order_exactly_at_symbol_limit() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=9_000.0,
            daily_notional=5_000.0,
        ),
    )

    order_intent = _order_intent(symbol="AAPL", qty=10, limit_price=100.0)  # 1,000

    service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_allows_order_exactly_at_daily_notional_limit() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=2_000.0,
            daily_notional=24_000.0,
        ),
    )

    order_intent = _order_intent(qty=10, limit_price=100.0)  # 1,000

    service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_rejects_order_just_over_gross_limit() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=99_000.01,
            symbol_exposure=1_000.0,
            daily_notional=5_000.0,
        ),
    )

    order_intent = _order_intent(qty=10, limit_price=100.0)

    with pytest.raises(GrossExposureLimitExceededError):
        service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_rejects_order_just_over_symbol_limit() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=9_000.01,
            daily_notional=5_000.0,
        ),
    )

    order_intent = _order_intent(symbol="AAPL", qty=10, limit_price=100.0)

    with pytest.raises(SymbolExposureLimitExceededError):
        service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_rejects_order_just_over_daily_notional_limit() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=2_000.0,
            daily_notional=24_000.01,
        ),
    )

    order_intent = _order_intent(qty=10, limit_price=100.0)

    with pytest.raises(DailyNotionalLimitExceededError):
        service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_combined_state_allows_when_all_projected_values_remain_within_limits() -> (
    None
):
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=99_000.0,
            symbol_exposure=8_500.0,
            daily_notional=24_000.0,
        ),
    )

    order_intent = _order_intent(symbol="AAPL", qty=5, limit_price=100.0)  # 500

    service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_combined_state_rejects_when_symbol_limit_exceeded_even_if_others_fit() -> (
    None
):
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=50_000.0,
            symbol_exposure=9_500.0,
            daily_notional=10_000.0,
        ),
    )

    order_intent = _order_intent(symbol="AAPL", qty=10, limit_price=100.0)  # 1,000

    with pytest.raises(SymbolExposureLimitExceededError):
        service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_combined_state_rejects_when_daily_limit_exceeded_even_if_others_fit() -> (
    None
):
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=50_000.0,
            symbol_exposure=2_000.0,
            daily_notional=24_500.0,
        ),
    )

    order_intent = _order_intent(symbol="AAPL", qty=10, limit_price=100.0)  # 1,000

    with pytest.raises(DailyNotionalLimitExceededError):
        service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_prioritizes_gross_limit_when_multiple_limits_would_fail() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=99_500.0,
            symbol_exposure=9_500.0,
            daily_notional=24_500.0,
        ),
    )

    order_intent = _order_intent(symbol="AAPL", qty=10, limit_price=100.0)  # all 3 exceed

    with pytest.raises(GrossExposureLimitExceededError):
        service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_estimate_order_notional_raises_when_qty_missing() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(),
    )

    order_intent = _order_intent(qty=None, limit_price=100.0)

    with pytest.raises(ValueError, match="qty must be set"):
        service._estimate_order_notional(order_intent)


def test_pre_trade_risk_accounts_for_reserved_cash_when_evaluating_order_capacity() -> None:
    service = PreTradeRiskService(
        settings=_settings(max_reserved_cash=25_000.0),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=2_000.0,
            daily_notional=5_000.0,
            reserved_cash=24_500.0,
        ),
    )

    order_intent = _order_intent(qty=10, limit_price=100.0)

    with pytest.raises(GrossExposureLimitExceededError, match="reserved cash"):
        service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))


def test_pre_trade_risk_reducing_existing_position_should_not_increase_symbol_risk() -> None:
    service = PreTradeRiskService(
        settings=_settings(),
        risk_state_reader=FakeRiskStateReader(
            gross_exposure=20_000.0,
            symbol_exposure=10_000.0,
            daily_notional=5_000.0,
            position_qty_by_symbol={"AAPL": 100.0},
        ),
    )

    order_intent = _order_intent(
        symbol="AAPL",
        qty=50,
        limit_price=100.0,
        side=Side.SELL,
    )

    service.assert_order_allowed(order_intent=order_intent, now=datetime.now(UTC))
