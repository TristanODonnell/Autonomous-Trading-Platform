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


# --- Position-aware reader (portfolio rotation 1F finding) -----------------------


def _position_aware_reader(symbol: str, qty: str, market_value: str):
    from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
        PortfolioRiskStateReader,
    )
    from autonomous_trading_platform.safety.readers.risk_state_reader import (
        PositionAwareRiskStateReader,
    )

    return PositionAwareRiskStateReader(
        PortfolioRiskStateReader(
            {symbol: Decimal(market_value)},
            Decimal("250000"),
            symbol_quantities={symbol: Decimal(qty)},
        )
    )


def test_selling_an_appreciated_position_above_the_symbol_cap_is_allowed() -> None:
    # Bought under the cap, price ran up: exiting is a 30.7k order against a 25k cap.
    service = PreTradeRiskService(
        settings=_settings(max_symbol_exposure=25_000.0, max_daily_notional_traded=1e9),
        risk_state_reader=_position_aware_reader("NVDA", "34", "30707.78"),
    )

    service.assert_order_allowed(
        order_intent=_order_intent(symbol="NVDA", qty=34, limit_price=903.17, side=Side.SELL),
        now=datetime.now(UTC),
    )


def test_buying_more_of_a_symbol_already_at_the_cap_is_blocked() -> None:
    service = PreTradeRiskService(
        settings=_settings(max_symbol_exposure=25_000.0, max_daily_notional_traded=1e9),
        risk_state_reader=_position_aware_reader("NVDA", "34", "30707.78"),
    )

    with pytest.raises(SymbolExposureLimitExceededError):
        service.assert_order_allowed(
            order_intent=_order_intent(symbol="NVDA", qty=1, limit_price=903.17),
            now=datetime.now(UTC),
        )


def test_stub_reader_treated_the_same_exit_as_new_exposure() -> None:
    # Documents the bug the position-aware reader fixes.
    from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader

    service = PreTradeRiskService(
        settings=_settings(max_symbol_exposure=25_000.0, max_daily_notional_traded=1e9),
        risk_state_reader=StubRiskStateReader(),
    )

    with pytest.raises(SymbolExposureLimitExceededError):
        service.assert_order_allowed(
            order_intent=_order_intent(symbol="NVDA", qty=34, limit_price=903.17, side=Side.SELL),
            now=datetime.now(UTC),
        )


# --- Reducing an over-cap position (portfolio rotation step 5 finding) ------------


def test_partial_sell_of_an_over_cap_position_is_allowed() -> None:
    # Account holds 300 JPM ($59.6k) against a $25k cap; one sleeve sells 125.
    # Before the fix: projected 34.3k > cap -> rejected, and the position froze.
    service = PreTradeRiskService(
        settings=_settings(max_symbol_exposure=25_000.0, max_daily_notional_traded=1e9),
        risk_state_reader=_position_aware_reader("JPM", "300", "59577.03"),
    )

    service.assert_order_allowed(
        order_intent=_order_intent(symbol="JPM", qty=125, limit_price=202.32, side=Side.SELL),
        now=datetime.now(UTC),
    )


def test_sell_that_flips_into_an_over_cap_short_is_blocked() -> None:
    # Holds 10 NVDA ($9k); selling 60 leaves a 50-share short (~$45k) above the cap.
    service = PreTradeRiskService(
        settings=_settings(max_symbol_exposure=25_000.0, max_daily_notional_traded=1e9),
        risk_state_reader=_position_aware_reader("NVDA", "10", "9031.70"),
    )

    with pytest.raises(SymbolExposureLimitExceededError):
        service.assert_order_allowed(
            order_intent=_order_intent(symbol="NVDA", qty=60, limit_price=903.17, side=Side.SELL),
            now=datetime.now(UTC),
        )


def test_symbol_exposure_cap_is_the_tightest_configured_limit() -> None:
    service = PreTradeRiskService(
        settings=_settings(max_symbol_exposure=25_000.0),
        risk_state_reader=FakeRiskStateReader(),
        max_portfolio_symbol_pct=0.05,
    )

    assert service.symbol_exposure_cap_usd(total_equity=250_000.0) == 12_500.0
    assert service.symbol_exposure_cap_usd(total_equity=None) == 25_000.0


# --- Position-aware reader: account-level state (soak finding 9) -------------------


def _account_reader(positions: dict[str, tuple[str, str]], *, booked_today: float = 0.0):
    from autonomous_trading_platform.safety.readers.portfolio_risk_state_reader import (
        PortfolioRiskStateReader,
    )
    from autonomous_trading_platform.safety.readers.risk_state_reader import (
        PositionAwareRiskStateReader,
    )

    return PositionAwareRiskStateReader(
        PortfolioRiskStateReader(
            {symbol: Decimal(value) for symbol, (_qty, value) in positions.items()},
            Decimal("100000"),
            symbol_quantities={symbol: Decimal(qty) for symbol, (qty, _value) in positions.items()},
        ),
        daily_notional_traded=lambda _date: booked_today,
    )


def test_position_aware_reader_reports_real_gross_exposure_and_todays_fills() -> None:
    reader = _account_reader(
        {"NVDA": ("114", "24726.60"), "SPY": ("32", "24551.04")}, booked_today=704_000.0
    )

    assert reader.get_gross_exposure() == pytest.approx(49_277.64)
    assert reader.get_symbol_exposure("nvda") == pytest.approx(24_726.60)
    assert reader.get_daily_notional_traded(datetime.now(UTC).date()) == pytest.approx(704_000.0)
    assert reader.get_reserved_cash() == 0.0


def test_gross_cap_blocks_a_buy_when_the_ledger_is_already_at_the_cap() -> None:
    # Soak day 1: 12 positions of ~$25k on a $100k cap were all allowed because the
    # live path never looked at gross exposure.
    service = PreTradeRiskService(
        settings=_settings(
            max_gross_exposure=100_000.0,
            max_symbol_exposure=25_000.0,
            max_daily_notional_traded=1e9,
            max_reserved_cash=1e9,
        ),
        risk_state_reader=_account_reader(
            {sym: ("100", "24000") for sym in ("A", "B", "C", "D")}  # 96k gross
        ),
    )

    with pytest.raises(GrossExposureLimitExceededError):
        service.assert_order_allowed(
            order_intent=_order_intent(symbol="IWM", qty=92, limit_price=271.38),
            now=datetime.now(UTC),
        )
    # Selling one of the held names is risk-reducing and still passes.
    service.assert_order_allowed(
        order_intent=_order_intent(symbol="A", qty=100, limit_price=240.0, side=Side.SELL),
        now=datetime.now(UTC),
    )


def test_submitted_orders_count_for_the_rest_of_the_tick() -> None:
    reader = _account_reader({"MSFT": ("46", "24550.20")})

    reader.record_submitted_order(symbol="AMZN", side=Side.BUY, notional=24_756.78)
    assert reader.get_gross_exposure() == pytest.approx(24_550.20 + 24_756.78)
    assert reader.get_symbol_exposure("AMZN") == pytest.approx(24_756.78)
    assert reader.get_daily_notional_traded(datetime.now(UTC).date()) == pytest.approx(24_756.78)

    # A sell of the whole MSFT position takes it out of exposure but still counts as traded.
    reader.record_submitted_order(symbol="MSFT", side=Side.SELL, notional=24_550.20)
    assert reader.get_gross_exposure() == pytest.approx(24_756.78)
    assert reader.get_symbol_exposure("MSFT") == pytest.approx(0.0)
    assert reader.get_daily_notional_traded(datetime.now(UTC).date()) == pytest.approx(
        24_756.78 + 24_550.20
    )

    # Two buys in one bar: the second is checked against a cap the first already used.
    service = PreTradeRiskService(
        settings=_settings(
            max_gross_exposure=40_000.0,
            max_symbol_exposure=25_000.0,
            max_daily_notional_traded=1e9,
            max_reserved_cash=1e9,
        ),
        risk_state_reader=reader,
    )
    with pytest.raises(GrossExposureLimitExceededError):
        service.assert_order_allowed(
            order_intent=_order_intent(symbol="QQQ", qty=34, limit_price=720.26),
            now=datetime.now(UTC),
        )


# --- Position-aware reader: broker refresh (soak finding 12) -----------------------


class _BrokerWithPositions:
    def __init__(self, rows):
        self.rows = rows

    def get_positions(self):
        return self.rows


class _BrokerThatFails:
    def get_positions(self):
        raise RuntimeError("alpaca 503")


def _alpaca_row(symbol: str, qty: str, price: str, **overrides):
    row = {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": price,
        "current_price": price,
        "market_value": str(Decimal(qty) * Decimal(price)),
        "unrealized_pl": "0",
    }
    row.update(overrides)
    return row


def test_refresh_from_broker_replaces_the_stale_ledger_view() -> None:
    # Soak day 2, 16:10 UTC: the ledger still showed AAPL/META/V ($62.8k) because the
    # IWM buy from 16:05 had not been booked yet; the broker already held all four.
    reader = _account_reader(
        {"AAPL": ("65", "22418.50"), "META": ("28", "18549.45"), "V": ("53", "21787.87")}
    )
    assert reader.get_gross_exposure() == pytest.approx(62_755.82)

    refreshed = reader.refresh_from_broker(
        _BrokerWithPositions(
            [
                _alpaca_row("AAPL", "65", "344.90"),
                _alpaca_row("META", "28", "662.48"),
                _alpaca_row("V", "53", "411.09"),
                _alpaca_row("IWM", "90", "276.19"),
            ]
        )
    )

    assert refreshed is True
    assert reader.get_gross_exposure() == pytest.approx(87_612.81)
    assert reader.get_symbol_exposure("IWM") == pytest.approx(24_857.10)
    assert reader.get_position_qty("IWM") == 90.0

    # The GOOGL buy that went through against the stale view is now rejected.
    service = PreTradeRiskService(
        settings=_settings(
            max_gross_exposure=100_000.0,
            max_symbol_exposure=25_000.0,
            max_daily_notional_traded=1e9,
            max_reserved_cash=1e9,
        ),
        risk_state_reader=reader,
    )
    with pytest.raises(GrossExposureLimitExceededError):
        service.assert_order_allowed(
            order_intent=_order_intent(symbol="GOOGL", qty=76, limit_price=327.75),
            now=datetime.now(UTC),
        )


def test_refresh_from_broker_keeps_same_tick_orders_and_equity() -> None:
    reader = _account_reader({"MSFT": ("46", "24550.20")})
    reader.record_submitted_order(symbol="AMZN", side=Side.BUY, notional=24_756.78)

    assert reader.refresh_from_broker(_BrokerWithPositions([_alpaca_row("MSFT", "46", "533.70")]))

    assert reader.get_gross_exposure() == pytest.approx(24_550.20 + 24_756.78)
    assert reader.get_symbol_exposure("AMZN") == pytest.approx(24_756.78)
    assert reader._portfolio_reader.total_equity == Decimal("100000")


def test_refresh_from_broker_falls_back_to_the_ledger_when_the_broker_cannot_answer() -> None:
    reader = _account_reader({"MSFT": ("46", "24550.20")})

    assert reader.refresh_from_broker(_BrokerThatFails()) is False
    assert reader.refresh_from_broker(object()) is False
    assert reader.refresh_from_broker(_BrokerWithPositions([{"symbol": "X"}])) is False

    assert reader.get_gross_exposure() == pytest.approx(24_550.20)
    assert reader.get_position_qty("MSFT") == 46.0


def test_refresh_from_broker_derives_market_value_when_the_broker_omits_it() -> None:
    reader = _account_reader({})

    assert reader.refresh_from_broker(
        _BrokerWithPositions(
            [
                _alpaca_row("NVDA", "10", "180.00", market_value=None),
                {"symbol": "SPY", "qty": "2", "avg_entry_price": "600"},
                {"symbol": "FLAT", "qty": "0"},
            ]
        )
    )

    assert reader.get_symbol_exposure("NVDA") == pytest.approx(1_800.0)
    assert reader.get_symbol_exposure("SPY") == pytest.approx(1_200.0)
    assert reader.get_gross_exposure() == pytest.approx(3_000.0)
    assert reader.get_symbol_exposure("FLAT") == 0.0
