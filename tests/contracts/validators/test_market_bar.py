from datetime import UTC, datetime, timedelta
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    MarketSession,
    PriceBasis,
)
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.market_bar import MARKET_BAR_RULES


def make_market_bar(**overrides) -> MarketBar:
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    data = {
        "bar_id": "bar_123",
        "timestamp": ts,
        "end_timestamp": ts + timedelta(minutes=5),
        "interval": BarInterval.FIVE_MIN,
        "symbol": "AAPL",
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("95"),
        "close": Decimal("105"),
        "volume": 1000,
        "vwap": Decimal("103"),
        "trade_count": 25,
        "price_basis": PriceBasis.RAW,
        "adjustment_factor": Decimal("1"),
        "source": "alpaca",
        "ingested_at": datetime.now(UTC),
        "quality_flags": [],
        "session": MarketSession.REGULAR,
    }

    data.update(overrides)
    return MarketBar(**data)


def test_valid_market_bar_passes():
    bar = make_market_bar()

    result = run_rules(bar, MARKET_BAR_RULES)

    assert result.ok
    assert result.violations == []


def test_timestamp_must_align_to_5_min_boundary():
    bar = make_market_bar(timestamp=datetime(2025, 1, 1, 10, 1, tzinfo=UTC))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "TIMESTAMP_ALIGNED_5MIN_UTC_BOUNDARY" for v in result.violations)


def test_end_timestamp_must_equal_timestamp_plus_5_min():
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    bar = make_market_bar(
        timestamp=ts,
        end_timestamp=ts + timedelta(minutes=4),
    )

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "END_TIMESTAMP_EQUALS_TIMESTAMP_PLUS_5MIN" for v in result.violations)


def test_open_must_be_non_negative():
    bar = make_market_bar(open=Decimal("-1"))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "OPEN_NON_NEGATIVE" for v in result.violations)


def test_high_must_be_non_negative():
    bar = make_market_bar(high=Decimal("-1"))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "HIGH_NON_NEGATIVE" for v in result.violations)


def test_low_must_be_non_negative():
    bar = make_market_bar(low=Decimal("-1"))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "LOW_NON_NEGATIVE" for v in result.violations)


def test_close_must_be_non_negative():
    bar = make_market_bar(close=Decimal("-1"))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "CLOSE_NON_NEGATIVE" for v in result.violations)


def test_high_must_be_greater_than_or_equal_to_low():
    bar = make_market_bar(high=Decimal("90"), low=Decimal("95"))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "HIGH_GE_LOW" for v in result.violations)


def test_high_must_be_greater_than_or_equal_to_max_ohlc():
    bar = make_market_bar(
        open=Decimal("100"), high=Decimal("104"), low=Decimal("95"), close=Decimal("105")
    )

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "HIGH_GE_MAX_OHLC" for v in result.violations)


def test_low_must_be_less_than_or_equal_to_min_ohlc():
    bar = make_market_bar(
        open=Decimal("100"), high=Decimal("110"), low=Decimal("101"), close=Decimal("105")
    )

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "LOW_LE_MIN_OHLC" for v in result.violations)


def test_open_must_be_within_high_low_range():
    bar = make_market_bar(open=Decimal("120"), high=Decimal("110"), low=Decimal("95"))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "OPEN_WITHIN_HIGH_LOW_RANGE" for v in result.violations)


def test_close_must_be_within_high_low_range():
    bar = make_market_bar(close=Decimal("120"), high=Decimal("110"), low=Decimal("95"))

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "CLOSE_WITHIN_HIGH_LOW_RANGE" for v in result.violations)


def test_volume_must_be_non_negative():
    bar = make_market_bar(volume=-1)

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "VOLUME_NON_NEGATIVE" for v in result.violations)


def test_trade_count_must_be_non_negative_when_present():
    bar = make_market_bar(trade_count=-1)

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "TRADE_COUNT_NON_NEGATIVE_WHEN_PRESENT" for v in result.violations)


def test_adjustment_factor_must_be_positive():
    bar = make_market_bar(adjustment_factor=0.0)

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(v.code == "ADJUSTMENT_FACTOR_POSITIVE" for v in result.violations)


def test_raw_price_basis_requires_adjustment_factor_one():
    bar = make_market_bar(
        price_basis=PriceBasis.RAW,
        adjustment_factor=1.5,
    )

    result = run_rules(bar, MARKET_BAR_RULES)

    assert not result.ok
    assert any(
        v.code == "RAW_PRICE_BASIS_REQUIRES_ADJUSTMENT_FACTOR_ONE" for v in result.violations
    )
