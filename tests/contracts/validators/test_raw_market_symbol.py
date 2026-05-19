from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.contracts.runtime.raw_market_symbol import (
    RawMarketPoolSnapshot,
    RawMarketSymbol,
)
from autonomous_trading_platform.contracts.validators.raw_market_symbol import (
    validate_raw_market_pool_snapshot,
    validate_raw_market_symbol,
)

_NOW = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
_EARLIER = datetime(2025, 1, 14, 14, 30, tzinfo=UTC)


def _valid_symbol(**overrides) -> RawMarketSymbol:
    defaults = dict(
        symbol="AAPL",
        exchange="NASDAQ",
        asset_type="us_equity",
        status="active",
        is_tradable=True,
        source="alpaca",
        first_seen=_EARLIER,
        last_seen=_NOW,
        created_at=_EARLIER,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return RawMarketSymbol(**defaults)


def _valid_snapshot(**overrides) -> RawMarketPoolSnapshot:
    defaults = dict(
        snapshot_id="snap-001",
        captured_at=_NOW,
        source="alpaca",
        symbol_count=100,
        cadence="daily",
        is_complete=True,
    )
    defaults.update(overrides)
    return RawMarketPoolSnapshot(**defaults)


class TestValidateRawMarketSymbol:
    def test_valid_symbol_passes(self) -> None:
        result = validate_raw_market_symbol(_valid_symbol())
        assert result.ok is True
        assert result.errors == []

    def test_empty_symbol_fails(self) -> None:
        sym = _valid_symbol(symbol="")
        result = validate_raw_market_symbol(sym)
        assert result.ok is False
        assert any("symbol" in e for e in result.errors)

    def test_whitespace_symbol_fails(self) -> None:
        sym = _valid_symbol(symbol="  ")
        result = validate_raw_market_symbol(sym)
        assert result.ok is False

    def test_lowercase_symbol_fails(self) -> None:
        sym = _valid_symbol(symbol="aapl")
        result = validate_raw_market_symbol(sym)
        assert result.ok is False
        assert any("normalized" in e for e in result.errors)

    def test_empty_asset_type_fails(self) -> None:
        sym = _valid_symbol(asset_type="")
        result = validate_raw_market_symbol(sym)
        assert result.ok is False

    def test_empty_status_fails(self) -> None:
        sym = _valid_symbol(status="")
        result = validate_raw_market_symbol(sym)
        assert result.ok is False

    def test_empty_source_fails(self) -> None:
        sym = _valid_symbol(source="")
        result = validate_raw_market_symbol(sym)
        assert result.ok is False

    def test_first_seen_after_last_seen_fails(self) -> None:
        sym = _valid_symbol(first_seen=_NOW, last_seen=_EARLIER)
        result = validate_raw_market_symbol(sym)
        assert result.ok is False
        assert any("first_seen" in e for e in result.errors)

    def test_first_seen_equals_last_seen_passes(self) -> None:
        sym = _valid_symbol(first_seen=_NOW, last_seen=_NOW)
        result = validate_raw_market_symbol(sym)
        assert result.ok is True

    def test_multiple_errors_accumulate(self) -> None:
        sym = _valid_symbol(symbol="", asset_type="", source="")
        result = validate_raw_market_symbol(sym)
        assert result.ok is False
        assert len(result.errors) >= 2


class TestValidateRawMarketPoolSnapshot:
    def test_valid_snapshot_passes(self) -> None:
        result = validate_raw_market_pool_snapshot(_valid_snapshot())
        assert result.ok is True
        assert result.errors == []

    def test_empty_snapshot_id_fails(self) -> None:
        snap = _valid_snapshot(snapshot_id="")
        result = validate_raw_market_pool_snapshot(snap)
        assert result.ok is False

    def test_empty_source_fails(self) -> None:
        snap = _valid_snapshot(source="")
        result = validate_raw_market_pool_snapshot(snap)
        assert result.ok is False

    def test_negative_symbol_count_fails(self) -> None:
        snap = _valid_snapshot(symbol_count=-1)
        result = validate_raw_market_pool_snapshot(snap)
        assert result.ok is False

    def test_zero_symbol_count_passes(self) -> None:
        snap = _valid_snapshot(symbol_count=0)
        result = validate_raw_market_pool_snapshot(snap)
        assert result.ok is True

    def test_empty_cadence_fails(self) -> None:
        snap = _valid_snapshot(cadence="")
        result = validate_raw_market_pool_snapshot(snap)
        assert result.ok is False
