# autonomous_trading_platform/contracts/validators/market_bar.py

from __future__ import annotations

from datetime import timedelta

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.market.market_bar import MarketBar

from .core import Rule, is_aligned_to_minutes, is_non_negative, is_positive

MARKET_BAR_RULES: list[Rule[MarketBar]] = [
    Rule(
        code="TIMESTAMP_ALIGNED_5MIN_UTC_BOUNDARY",
        field="timestamp",
        check=lambda mb, _ctx: is_aligned_to_minutes(mb.timestamp, 5),
        message=lambda mb, _ctx: "timestamp must align to 5-minute UTC boundary",
    ),
    Rule(
        code="END_TIMESTAMP_EQUALS_TIMESTAMP_PLUS_5MIN",
        field="end_timestamp",
        check=lambda mb, _ctx: mb.end_timestamp == mb.timestamp + timedelta(minutes=5),
        message=lambda mb, _ctx: "end_timestamp must equal timestamp + 5 minutes",
    ),
    Rule(
        code="OPEN_NON_NEGATIVE",
        field="open",
        check=lambda mb, _ctx: is_non_negative(mb.open),
        message=lambda mb, _ctx: "open must be >= 0",
    ),
    Rule(
        code="HIGH_NON_NEGATIVE",
        field="high",
        check=lambda mb, _ctx: is_non_negative(mb.high),
        message=lambda mb, _ctx: "high must be >= 0",
    ),
    Rule(
        code="LOW_NON_NEGATIVE",
        field="low",
        check=lambda mb, _ctx: is_non_negative(mb.low),
        message=lambda mb, _ctx: "low must be >= 0",
    ),
    Rule(
        code="CLOSE_NON_NEGATIVE",
        field="close",
        check=lambda mb, _ctx: is_non_negative(mb.close),
        message=lambda mb, _ctx: "close must be >= 0",
    ),
    Rule(
        code="HIGH_GE_LOW",
        field=None,
        check=lambda mb, _ctx: mb.high >= mb.low,
        message=lambda mb, _ctx: f"high must be >= low (high={mb.high}, low={mb.low})",
    ),
    Rule(
        code="HIGH_GE_MAX_OHLC",
        field="high",
        check=lambda mb, _ctx: mb.high >= max(mb.open, mb.close, mb.low),
        message=lambda mb, _ctx: "high must be >= max(open, close, low)",
    ),
    Rule(
        code="LOW_LE_MIN_OHLC",
        field="low",
        check=lambda mb, _ctx: mb.low <= min(mb.open, mb.close, mb.high),
        message=lambda mb, _ctx: "low must be <= min(open, close, high)",
    ),
    Rule(
        code="OPEN_WITHIN_HIGH_LOW_RANGE",
        field="open",
        check=lambda mb, _ctx: mb.low <= mb.open <= mb.high,
        message=lambda mb, _ctx: (
            f"open must be between low and high (open={mb.open}, low={mb.low}, high={mb.high})"
        ),
    ),
    Rule(
        code="CLOSE_WITHIN_HIGH_LOW_RANGE",
        field="close",
        check=lambda mb, _ctx: mb.low <= mb.close <= mb.high,
        message=lambda mb, _ctx: (
            f"close must be between low and high (close={mb.close}, low={mb.low}, high={mb.high})"
        ),
    ),
    Rule(
        code="VOLUME_NON_NEGATIVE",
        field="volume",
        check=lambda mb, _ctx: is_non_negative(mb.volume),
        message=lambda mb, _ctx: "volume must be >= 0",
    ),
    Rule(
        code="TRADE_COUNT_NON_NEGATIVE_WHEN_PRESENT",
        field="trade_count",
        check=lambda mb, _ctx: mb.trade_count is None or is_non_negative(mb.trade_count),
        message=lambda mb, _ctx: "trade_count must be >= 0 when present",
    ),
    Rule(
        code="ADJUSTMENT_FACTOR_POSITIVE",
        field="adjustment_factor",
        check=lambda mb, _ctx: is_positive(mb.adjustment_factor),
        message=lambda mb, _ctx: "adjustment_factor must be > 0",
    ),
    Rule(
        code="RAW_PRICE_BASIS_REQUIRES_ADJUSTMENT_FACTOR_ONE",
        field="adjustment_factor",
        check=lambda mb, _ctx: mb.price_basis != PriceBasis.RAW or mb.adjustment_factor == 1.0,
        message=lambda mb, _ctx: "when price_basis='raw', adjustment_factor must equal 1.0",
    ),
]
