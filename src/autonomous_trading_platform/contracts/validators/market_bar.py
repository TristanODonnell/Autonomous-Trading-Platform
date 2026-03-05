# autonomous_trading_platform/contracts/validators/market_bar.py

from __future__ import annotations

from datetime import timedelta

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.market.market_bar import MarketBar

from .core import Rule, is_aligned_to_minutes, is_non_negative

MARKET_BAR_RULES: list[Rule[MarketBar]] = [
    # Alignment: timestamp must be aligned to 5-minute boundaries in UTC:
    # minute ∈ {00,05,10,...,55}, second=0, microsecond=0.
    Rule(
        code="TIMESTAMP_ALIGNED_5MIN_UTC_BOUNDARY",
        field="timestamp",
        check=lambda mb, _ctx: is_aligned_to_minutes(mb.timestamp, 5),
        message=lambda mb, _ctx: "timestamp is not aligned to 5 minute UTC boundary.",
    ),
    # Duration: end_timestamp = timestamp + 5 minutes.
    Rule(
        code="END_TIMESTAMP_DURATION",
        field="end_timestamp",
        check=lambda mb, _ctx: mb.end_timestamp == mb.timestamp + timedelta(minutes=5),
        message=lambda mb, _ctx: "end_timestamp is not aligned to 5 minute duration.",
    ),
    # Price sanity: high >= max(open, close, low)
    Rule(
        code="HIGH_GRE_MAX",
        field="high",
        check=lambda mb, _ctx: mb.high >= max(mb.open, mb.close, mb.low),
        message=lambda mb, _ctx: "high must be greater than or equal to max of open, close and low",
    ),
    # and low <= min(open, close, high).
    Rule(
        code="LOW_LE_MIN",
        field="low",
        check=lambda mb, _ctx: mb.low >= min(mb.open, mb.close, mb.high),
        message=lambda mb, _ctx: "low must be less than or equal to max of open, close and high",
    ),
    # Non-negatives: volume >= 0
    Rule(
        code="VOLUME_NONNEG",
        field="volume",
        check=lambda mb, _ctx: is_non_negative(mb.volume),
        message=lambda mb, _ctx: "volume must be non-negative",
    ),
    # trade_count >= 0 when present.
    Rule(
        code="TRADE_COUNT_NONNEG_WHEN_PRESENT",
        field="trade_count",
        check=lambda mb, _ctx: (mb.trade_count is None) or (is_non_negative(mb.trade_count)),
        message=lambda mb, _ctx: "trade_Count must be non-negative when present",
    ),
    # Raw vs adjusted: if price_basis="raw" then adjustment_factor == 1.0.
    Rule(
        code="PRICE_BASIS_RAW_ADJUSTMENT_FACTOR_EQUALS_1.0",
        field="price_basis",
        check=lambda mb, _ctx: (
            (mb.price_basis != PriceBasis.ADJUSTED) or (mb.adjustment_factor == 1.0)
        ),
        message=lambda mb, _ctx: "when price_basis is raw, adjustment_factor is not 1.0",
    ),
]
