from datetime import UTC, date, datetime, timedelta

from autonomous_trading_platform.contracts.runtime.universe_snapshot import UniverseSnapshot
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.universe_snapshot import (
    UNIVERSE_SNAPSHOT_RULES,
)


def make_universe_snapshot(**overrides) -> UniverseSnapshot:
    start = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    data = {
        "universe_id": "universe_001",
        "snapshot_date": date(2025, 1, 1),
        "effective_start": start,
        "effective_end": start + timedelta(days=1),
        "symbols": ["AAPL", "MSFT", "SPY"],
        "criteria": {"min_price": 5, "min_volume": 100000},
        "version": "v1",
        "source": "manual",
        "built_at": datetime.now(UTC),
        "notes": None,
    }

    data.update(overrides)
    return UniverseSnapshot(**data)


def test_valid_universe_snapshot_passes():
    snapshot = make_universe_snapshot()

    result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)

    assert result.ok
    assert result.violations == []


def test_symbols_must_be_non_empty():
    snapshot = make_universe_snapshot(symbols=[])

    result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "SYMBOLS_NON_EMPTY" for v in result.violations)


def test_symbols_must_be_unique():
    snapshot = make_universe_snapshot(symbols=["AAPL", "MSFT", "AAPL"])

    result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "SYMBOLS_UNIQUE" for v in result.violations)


def test_effective_start_must_be_lte_effective_end():
    start = datetime(2025, 1, 2, 10, 0, tzinfo=UTC)
    end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    snapshot = make_universe_snapshot(
        effective_start=start,
        effective_end=end,
    )

    result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "EFFECTIVE_WINDOW_ORDER_VALID" for v in result.violations)


def test_symbol_format_must_be_valid():
    snapshot = make_universe_snapshot(symbols=["AAPL", "bad symbol", "MSFT"])

    result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "SYMBOL_FORMAT_VALID" for v in result.violations)


def test_effective_end_none_is_valid():
    snapshot = make_universe_snapshot(effective_end=None)

    result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)

    assert result.ok


def test_valid_symbol_formats_pass():
    snapshot = make_universe_snapshot(symbols=["AAPL", "BRK.B", "BTC-USD", "SPY_1"])

    result = run_rules(snapshot, UNIVERSE_SNAPSHOT_RULES)

    assert result.ok
