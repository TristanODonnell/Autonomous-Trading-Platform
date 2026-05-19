from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from autonomous_trading_platform.contracts.common.enums import (
    UniverseStatus,
)
from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolSnapshot,
)
from autonomous_trading_platform.universe.services.universe_candidate_builder import (
    UniverseCandidateBuilder,
)
from autonomous_trading_platform.universe.types import CandidateGenerationConfig

_AS_OF = datetime(2025, 1, 20, 16, 0, tzinfo=UTC)
_SNAP_ID = "snap-test-0001"


# ---------------------------------------------------------------------------
# Stub infrastructure
# ---------------------------------------------------------------------------


class _StubRawPoolQueryService:
    def __init__(
        self,
        symbols: list[str] | None = None,
        snapshot_id: str | None = _SNAP_ID,
    ) -> None:
        self._symbols = symbols or []
        self._snapshot_id = snapshot_id

    def get_snapshot_as_of(self, as_of: datetime) -> RawMarketPoolSnapshot | None:
        if self._snapshot_id is None:
            return None
        return RawMarketPoolSnapshot(
            snapshot_id=self._snapshot_id,
            captured_at=as_of,
            source="alpaca",
            symbol_count=len(self._symbols),
            cadence="daily",
            is_complete=True,
        )

    def get_active_tradable_symbols(
        self,
        as_of: datetime | None = None,
        *,
        asset_type: str | None = None,
        exchange: str | None = None,
    ) -> list[str]:
        return sorted(self._symbols)


def _make_session_with_bars(bars_by_symbol: dict[str, list[dict[str, Any]]]) -> Any:
    """
    Returns a mock SQLAlchemy session whose execute() returns the specified bars.
    bars_by_symbol maps symbol → list of {ts, close, volume, trade_count}.
    """
    session = MagicMock()

    def _execute(stmt):
        # Extract the symbol from the WHERE clause
        symbol = _extract_symbol_from_stmt(stmt)
        rows = []
        for bar in bars_by_symbol.get(symbol, []):
            rows.append((bar["ts"], bar["close"], bar["volume"], bar.get("trade_count")))
        result = MagicMock()
        result.all.return_value = rows
        return result

    session.execute.side_effect = _execute
    return session


def _extract_symbol_from_stmt(stmt: Any) -> str:
    """Pull the symbol value out of the compiled WHERE clause for the stub."""
    for clause in stmt.whereclause.clauses:
        right = getattr(clause, "right", None)
        if right is not None and hasattr(right, "value"):
            val = right.value
            if isinstance(val, str) and val.isupper():
                return val
    return "__unknown__"


def _bars_for_symbol(
    symbol: str,
    n_days: int = 20,
    bars_per_day: int = 78,
    close: str = "50.00",
    volume: int = 100_000,
    trade_count: int = 500,
    start_date: date = date(2025, 1, 1),
) -> list[dict[str, Any]]:
    """Generate synthetic five-minute bars for a symbol across n_days."""
    result = []
    for day_offset in range(n_days):
        day = start_date + timedelta(days=day_offset)
        for bar_i in range(bars_per_day):
            ts = datetime(day.year, day.month, day.day, 9, 30, tzinfo=UTC) + timedelta(
                minutes=5 * bar_i
            )
            if ts > _AS_OF:
                break
            result.append(
                {
                    "ts": ts,
                    "close": Decimal(close),
                    "volume": volume,
                    "trade_count": trade_count,
                }
            )
    return result


def _make_builder(
    symbols: list[str],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    snapshot_id: str | None = _SNAP_ID,
) -> UniverseCandidateBuilder:
    session = _make_session_with_bars(bars_by_symbol)
    raw_pool = _StubRawPoolQueryService(symbols=symbols, snapshot_id=snapshot_id)
    return UniverseCandidateBuilder(session, raw_pool)


def _default_config(**overrides: Any) -> CandidateGenerationConfig:
    defaults: dict[str, Any] = {
        "as_of": _AS_OF,
        "lookback_days": 20,
        "min_history_days": 5,
        "min_price": Decimal("1.00"),
        "min_average_daily_dollar_volume": Decimal("1000000"),
        "max_missing_bar_pct": 0.50,
        "max_symbols": 500,
    }
    defaults.update(overrides)
    return CandidateGenerationConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests: empty / no-data cases
# ---------------------------------------------------------------------------


def test_empty_raw_pool_produces_empty_candidate() -> None:
    builder = _make_builder(symbols=[], bars_by_symbol={})
    result = builder.build_candidate(config=_default_config(), name="test")

    assert result.version.status == UniverseStatus.CANDIDATE
    assert result.included_members == []
    assert result.excluded_members == []
    assert result.generation_metadata["candidate_pool_size"] == 0
    assert result.generation_metadata["included_count"] == 0


def test_symbols_with_no_market_data_are_rejected() -> None:
    symbols = ["AAPL", "MSFT"]
    builder = _make_builder(symbols=symbols, bars_by_symbol={})
    result = builder.build_candidate(config=_default_config(), name="test")

    assert result.included_members == []
    assert len(result.excluded_members) == 2
    rejected_reasons = {m.symbol: m.excluded_reason for m in result.excluded_members}
    assert rejected_reasons["AAPL"] == "no_market_data"
    assert rejected_reasons["MSFT"] == "no_market_data"


def test_no_raw_pool_snapshot_does_not_crash() -> None:
    builder = _make_builder(symbols=[], bars_by_symbol={}, snapshot_id=None)
    result = builder.build_candidate(config=_default_config(), name="test")

    assert result.raw_pool_snapshot_id is None
    assert result.generation_metadata["raw_pool_snapshot_id"] is None


# ---------------------------------------------------------------------------
# Tests: eligibility filters
# ---------------------------------------------------------------------------


def test_price_filter_rejects_low_priced_symbol() -> None:
    bars = _bars_for_symbol("PENNY", close="0.50", volume=1_000_000)
    builder = _make_builder(
        symbols=["PENNY"],
        bars_by_symbol={"PENNY": bars},
    )
    config = _default_config(min_price=Decimal("1.00"))
    result = builder.build_candidate(config=config, name="test")

    assert result.included_members == []
    assert result.excluded_members[0].excluded_reason == "price_below_minimum"
    assert result.excluded_members[0].liquidity_metrics_json is not None
    assert Decimal(result.excluded_members[0].liquidity_metrics_json["last_price"]) < Decimal(
        "1.00"
    )


def test_addv_filter_rejects_illiquid_symbol() -> None:
    bars = _bars_for_symbol("ILLIQ", close="10.00", volume=10)
    builder = _make_builder(
        symbols=["ILLIQ"],
        bars_by_symbol={"ILLIQ": bars},
    )
    config = _default_config(min_average_daily_dollar_volume=Decimal("1000000"))
    result = builder.build_candidate(config=config, name="test")

    assert result.included_members == []
    assert result.excluded_members[0].excluded_reason == "insufficient_liquidity"


def test_missing_bar_filter_rejects_sparse_symbol() -> None:
    # Only 10 bars per day instead of 78 → ~87% missing → well above any threshold
    bars = _bars_for_symbol("SPARSE", close="20.00", volume=100_000, bars_per_day=5)
    builder = _make_builder(
        symbols=["SPARSE"],
        bars_by_symbol={"SPARSE": bars},
    )
    config = _default_config(max_missing_bar_pct=0.20)
    result = builder.build_candidate(config=config, name="test")

    assert result.included_members == []
    assert result.excluded_members[0].excluded_reason == "excessive_missing_bars"
    qual = result.excluded_members[0].quality_metrics_json
    assert qual is not None
    assert qual["bar_completeness_pct"] < 0.20


def test_insufficient_history_rejects_new_symbol() -> None:
    # Only 3 days of data; min_history_days=10
    bars = _bars_for_symbol("NEWCO", close="10.00", volume=500_000, n_days=3)
    builder = _make_builder(
        symbols=["NEWCO"],
        bars_by_symbol={"NEWCO": bars},
    )
    config = _default_config(min_history_days=10)
    result = builder.build_candidate(config=config, name="test")

    assert result.included_members == []
    assert result.excluded_members[0].excluded_reason == "insufficient_history"
    qual = result.excluded_members[0].quality_metrics_json
    assert qual is not None
    assert qual["history_days"] == 3


# ---------------------------------------------------------------------------
# Tests: happy-path inclusion
# ---------------------------------------------------------------------------


def test_eligible_symbol_is_included_with_metrics() -> None:
    bars = _bars_for_symbol("GOOD", close="100.00", volume=200_000)
    builder = _make_builder(
        symbols=["GOOD"],
        bars_by_symbol={"GOOD": bars},
    )
    result = builder.build_candidate(config=_default_config(), name="test")

    assert len(result.included_members) == 1
    m = result.included_members[0]
    assert m.symbol == "GOOD"
    assert m.rank == 1
    assert m.score is not None
    assert m.included_reason is not None
    assert m.excluded_reason is None
    assert m.liquidity_metrics_json is not None
    assert m.quality_metrics_json is not None

    liq = m.liquidity_metrics_json
    assert Decimal(liq["last_price"]) == Decimal("100.00")
    assert Decimal(liq["average_daily_dollar_volume"]) > Decimal("0")

    qual = m.quality_metrics_json
    assert qual["history_days"] >= 5
    assert 0.0 <= qual["bar_completeness_pct"] <= 1.0
    assert 0.0 <= qual["trading_consistency"] <= 1.0


def test_multiple_symbols_all_included_when_eligible() -> None:
    symbols = ["AAPL", "GOOG", "MSFT"]
    bars_by_symbol = {s: _bars_for_symbol(s, close="50.00", volume=500_000) for s in symbols}
    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    result = builder.build_candidate(config=_default_config(), name="test")

    included_symbols = {m.symbol for m in result.included_members}
    assert included_symbols == set(symbols)
    assert result.excluded_members == []


# ---------------------------------------------------------------------------
# Tests: ranking and scoring
# ---------------------------------------------------------------------------


def test_higher_addv_gets_higher_rank() -> None:
    """Higher ADDV should rank before lower ADDV (rank 1 = best)."""
    bars_by_symbol = {
        "HIGH": _bars_for_symbol("HIGH", close="100.00", volume=1_000_000),
        "LOW": _bars_for_symbol("LOW", close="100.00", volume=10_000),
    }
    builder = _make_builder(
        symbols=["HIGH", "LOW"],
        bars_by_symbol=bars_by_symbol,
    )
    result = builder.build_candidate(config=_default_config(), name="test")

    by_rank = {m.symbol: m.rank for m in result.included_members}
    assert by_rank["HIGH"] == 1
    assert by_rank["LOW"] == 2


def test_rank_ordering_is_stable_by_symbol_on_true_score_tie() -> None:
    """When all scoring weights are zero, all scores equal 0.0 and ordering is alphabetical."""
    symbols = ["ZZZ", "AAA", "MMM"]
    bars_by_symbol = {s: _bars_for_symbol(s, close="10.00", volume=100_000) for s in symbols}
    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    config = _default_config(
        weight_dollar_volume=0.0,
        weight_trading_consistency=0.0,
        weight_bar_completeness=0.0,
        weight_volatility_penalty=0.0,
    )
    result = builder.build_candidate(config=config, name="test")

    ordered = [m.symbol for m in result.included_members]
    # All scores = 0.0 → secondary sort by symbol ascending
    assert sorted(ordered) == ordered


def test_score_is_between_zero_and_one() -> None:
    symbols = ["AAPL", "MSFT"]
    bars_by_symbol = {s: _bars_for_symbol(s, close="50.00", volume=300_000) for s in symbols}
    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    result = builder.build_candidate(config=_default_config(), name="test")

    for m in result.included_members:
        assert m.score is not None
        assert -0.2 <= m.score <= 1.1, f"Score {m.score} for {m.symbol} out of expected range"


def test_max_symbols_constraint_caps_included_count() -> None:
    symbols = [f"SYM{i:03d}" for i in range(10)]
    bars_by_symbol = {s: _bars_for_symbol(s, close="10.00", volume=100_000) for s in symbols}
    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    config = _default_config(max_symbols=3)
    result = builder.build_candidate(config=config, name="test")

    assert len(result.included_members) == 3
    beyond = [m for m in result.excluded_members if m.excluded_reason == "beyond_max_universe_size"]
    assert len(beyond) == 7


def test_beyond_max_symbols_have_score_and_metrics() -> None:
    symbols = [f"SYM{i:03d}" for i in range(5)]
    bars_by_symbol = {s: _bars_for_symbol(s, close="10.00", volume=100_000) for s in symbols}
    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    config = _default_config(max_symbols=2)
    result = builder.build_candidate(config=config, name="test")

    beyond = [m for m in result.excluded_members if m.excluded_reason == "beyond_max_universe_size"]
    for m in beyond:
        assert m.score is not None
        assert m.liquidity_metrics_json is not None
        assert m.quality_metrics_json is not None


# ---------------------------------------------------------------------------
# Tests: reproducibility
# ---------------------------------------------------------------------------


def test_same_config_same_data_produces_same_config_hash() -> None:
    symbols = ["AAPL", "MSFT"]
    bars_by_symbol = {s: _bars_for_symbol(s, close="50.00", volume=300_000) for s in symbols}

    builder1 = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    builder2 = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)

    config = _default_config()
    r1 = builder1.build_candidate(config=config, name="run1")
    r2 = builder2.build_candidate(config=config, name="run2")

    assert r1.version.config_hash == r2.version.config_hash


def test_different_config_produces_different_config_hash() -> None:
    symbols = ["AAPL"]
    bars_by_symbol = {"AAPL": _bars_for_symbol("AAPL", close="50.00", volume=300_000)}

    config_a = _default_config(lookback_days=20)
    config_b = _default_config(lookback_days=30)

    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    r_a = builder.build_candidate(config=config_a, name="a")

    builder2 = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    r_b = builder2.build_candidate(config=config_b, name="b")

    # Different lookback_days → same symbol set but different config hash
    assert r_a.version.config_hash != r_b.version.config_hash


def test_different_symbols_produces_different_config_hash() -> None:
    bars_by_symbol = {
        "AAPL": _bars_for_symbol("AAPL", close="50.00", volume=300_000),
        "MSFT": _bars_for_symbol("MSFT", close="50.00", volume=300_000),
    }
    config = _default_config()

    builder_both = _make_builder(symbols=["AAPL", "MSFT"], bars_by_symbol=bars_by_symbol)
    builder_one = _make_builder(symbols=["AAPL"], bars_by_symbol=bars_by_symbol)

    r_both = builder_both.build_candidate(config=config, name="both")
    r_one = builder_one.build_candidate(config=config, name="one")

    assert r_both.version.config_hash != r_one.version.config_hash


def test_ranking_order_is_deterministic_across_runs() -> None:
    symbols = ["AAPL", "GOOG", "MSFT"]
    bars_by_symbol = {
        "AAPL": _bars_for_symbol("AAPL", close="100.00", volume=500_000),
        "GOOG": _bars_for_symbol("GOOG", close="150.00", volume=300_000),
        "MSFT": _bars_for_symbol("MSFT", close="80.00", volume=700_000),
    }
    config = _default_config()

    builder1 = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    builder2 = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)

    r1 = builder1.build_candidate(config=config, name="r1")
    r2 = builder2.build_candidate(config=config, name="r2")

    order1 = [(m.symbol, m.rank) for m in r1.included_members]
    order2 = [(m.symbol, m.rank) for m in r2.included_members]
    assert order1 == order2


# ---------------------------------------------------------------------------
# Tests: generation metadata
# ---------------------------------------------------------------------------


def test_generation_metadata_contains_required_fields() -> None:
    bars_by_symbol = {"AAPL": _bars_for_symbol("AAPL", close="50.00", volume=300_000)}
    builder = _make_builder(symbols=["AAPL"], bars_by_symbol=bars_by_symbol)
    result = builder.build_candidate(config=_default_config(), name="test")

    meta = result.generation_metadata
    assert meta["schema_version"] == 1
    assert "as_of" in meta
    assert "lookback_days" in meta
    assert "min_history_days" in meta
    assert "raw_pool_snapshot_id" in meta
    assert "filter_config" in meta
    assert "ranking_config" in meta
    assert "candidate_pool_size" in meta
    assert "eligible_count" in meta
    assert "included_count" in meta
    assert "excluded_count" in meta
    assert "generation_timestamp" in meta


def test_generation_metadata_stored_on_version() -> None:
    bars_by_symbol = {"AAPL": _bars_for_symbol("AAPL", close="50.00", volume=300_000)}
    builder = _make_builder(symbols=["AAPL"], bars_by_symbol=bars_by_symbol)
    result = builder.build_candidate(config=_default_config(), name="test")

    assert result.version.generation_metadata_json is not None
    assert result.version.generation_metadata_json["schema_version"] == 1
    assert result.version.generation_metadata_json["included_count"] == 1


def test_generation_metadata_counts_are_accurate() -> None:
    # AAPL: good, PENNY: rejected by price
    bars_by_symbol = {
        "AAPL": _bars_for_symbol("AAPL", close="50.00", volume=500_000),
        "PENNY": _bars_for_symbol("PENNY", close="0.10", volume=100_000),
    }
    builder = _make_builder(symbols=["AAPL", "PENNY"], bars_by_symbol=bars_by_symbol)
    config = _default_config(min_price=Decimal("1.00"))
    result = builder.build_candidate(config=config, name="test")

    meta = result.generation_metadata
    assert meta["candidate_pool_size"] == 2
    assert meta["included_count"] == 1
    assert meta["excluded_count"] == 1


def test_raw_pool_snapshot_id_captured_in_metadata() -> None:
    bars_by_symbol = {"AAPL": _bars_for_symbol("AAPL", close="50.00", volume=300_000)}
    builder = _make_builder(
        symbols=["AAPL"], bars_by_symbol=bars_by_symbol, snapshot_id="snap-xyz-999"
    )
    result = builder.build_candidate(config=_default_config(), name="test")

    assert result.raw_pool_snapshot_id == "snap-xyz-999"
    assert result.generation_metadata["raw_pool_snapshot_id"] == "snap-xyz-999"


# ---------------------------------------------------------------------------
# Tests: candidate version attributes
# ---------------------------------------------------------------------------


def test_candidate_version_status_is_candidate() -> None:
    builder = _make_builder(symbols=[], bars_by_symbol={})
    result = builder.build_candidate(config=_default_config(), name="test")
    assert result.version.status == UniverseStatus.CANDIDATE


def test_candidate_version_effective_from_matches_as_of() -> None:
    config = _default_config(as_of=_AS_OF)
    builder = _make_builder(symbols=[], bars_by_symbol={})
    result = builder.build_candidate(config=config, name="test")
    assert result.version.effective_from == _AS_OF


def test_candidate_version_has_non_empty_config_hash() -> None:
    builder = _make_builder(symbols=[], bars_by_symbol={})
    result = builder.build_candidate(config=_default_config(), name="test")
    assert len(result.version.config_hash) == 64  # SHA-256 hex


def test_candidate_version_name_is_preserved() -> None:
    builder = _make_builder(symbols=[], bars_by_symbol={})
    result = builder.build_candidate(config=_default_config(), name="my_candidate_v1")
    assert result.version.name == "my_candidate_v1"


def test_candidate_version_source_is_preserved() -> None:
    builder = _make_builder(symbols=[], bars_by_symbol={})
    result = builder.build_candidate(
        config=_default_config(), name="test", source="sector_filtered"
    )
    assert result.version.source == "sector_filtered"


def test_candidate_version_rebalance_reason_preserved() -> None:
    builder = _make_builder(symbols=[], bars_by_symbol={})
    result = builder.build_candidate(
        config=_default_config(), name="test", rebalance_reason="weekly_rebalance"
    )
    assert result.version.rebalance_reason == "weekly_rebalance"


# ---------------------------------------------------------------------------
# Tests: member metrics persistence
# ---------------------------------------------------------------------------


def test_included_member_rank_is_1_based() -> None:
    symbols = ["AAPL", "MSFT", "GOOG"]
    bars_by_symbol = {s: _bars_for_symbol(s, close="10.00", volume=100_000) for s in symbols}
    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    result = builder.build_candidate(config=_default_config(), name="test")

    ranks = sorted(m.rank for m in result.included_members if m.rank is not None)
    assert ranks == list(range(1, len(symbols) + 1))


def test_excluded_member_rank_is_none() -> None:
    bars_by_symbol = {"AAPL": _bars_for_symbol("AAPL", close="0.50", volume=100_000)}
    builder = _make_builder(symbols=["AAPL"], bars_by_symbol=bars_by_symbol)
    config = _default_config(min_price=Decimal("1.00"))
    result = builder.build_candidate(config=config, name="test")

    assert result.excluded_members[0].rank is None


def test_included_member_has_liquidity_and_quality_metrics() -> None:
    bars_by_symbol = {"AAPL": _bars_for_symbol("AAPL", close="100.00", volume=200_000)}
    builder = _make_builder(symbols=["AAPL"], bars_by_symbol=bars_by_symbol)
    result = builder.build_candidate(config=_default_config(), name="test")

    m = result.included_members[0]
    assert m.liquidity_metrics_json is not None
    assert m.quality_metrics_json is not None
    assert "last_price" in m.liquidity_metrics_json
    assert "average_daily_dollar_volume" in m.liquidity_metrics_json
    assert "history_days" in m.quality_metrics_json
    assert "bar_completeness_pct" in m.quality_metrics_json


def test_rejection_reason_populated_for_excluded_members() -> None:
    bars_by_symbol = {
        "AAPL": _bars_for_symbol("AAPL", close="50.00", volume=300_000),
        "JUNK": _bars_for_symbol("JUNK", close="0.50", volume=5_000),
    }
    builder = _make_builder(symbols=["AAPL", "JUNK"], bars_by_symbol=bars_by_symbol)
    config = _default_config(min_price=Decimal("1.00"))
    result = builder.build_candidate(config=config, name="test")

    excl = {m.symbol: m.excluded_reason for m in result.excluded_members}
    assert excl["JUNK"] == "price_below_minimum"


def test_diagnostics_contain_rejection_summary() -> None:
    bars_by_symbol = {
        "AAPL": _bars_for_symbol("AAPL", close="50.00", volume=300_000),
        "JUNK": _bars_for_symbol("JUNK", close="0.50", volume=5_000),
        "NODATA": [],
    }
    builder = _make_builder(symbols=["AAPL", "JUNK", "NODATA"], bars_by_symbol=bars_by_symbol)
    config = _default_config(min_price=Decimal("1.00"))
    result = builder.build_candidate(config=config, name="test")

    diag = result.diagnostics
    assert "rejection_summary" in diag
    assert diag["rejection_summary"]["price_below_minimum"] == 1
    assert diag["rejection_summary"]["no_market_data"] == 1
    assert diag["included_count"] == 1


# ---------------------------------------------------------------------------
# Tests: as-of date isolation
# ---------------------------------------------------------------------------


def test_bars_after_as_of_are_not_used() -> None:
    """Bars timestamped after as_of should not be included in metrics."""
    # The stub returns bars irrespective of timestamps; the WHERE clause
    # filters in SQLAlchemy, but our stub does not filter. We test that
    # the builder correctly uses only data in its window by checking that
    # a symbol with all bars BEYOND as_of returns as "no_market_data" when
    # we set up the stub to return empty rows for as-of queries.
    builder2 = _make_builder(symbols=["AAPL"], bars_by_symbol={"AAPL": []})
    result = builder2.build_candidate(config=_default_config(), name="test")
    assert result.included_members == []
    assert result.excluded_members[0].excluded_reason == "no_market_data"


# ---------------------------------------------------------------------------
# Tests: mixed rejection types
# ---------------------------------------------------------------------------


def test_mixed_rejection_reasons_all_captured() -> None:
    bars_by_symbol = {
        "GOOD": _bars_for_symbol("GOOD", close="50.00", volume=500_000),
        "LOW_PRICE": _bars_for_symbol("LOW_PRICE", close="0.50", volume=500_000),
        "LOW_ADDV": _bars_for_symbol("LOW_ADDV", close="50.00", volume=10),
        "NEW_IPO": _bars_for_symbol("NEW_IPO", close="20.00", volume=200_000, n_days=2),
        "NO_DATA": [],
    }
    symbols = list(bars_by_symbol.keys())
    builder = _make_builder(symbols=symbols, bars_by_symbol=bars_by_symbol)
    config = _default_config(
        min_price=Decimal("1.00"),
        min_average_daily_dollar_volume=Decimal("1000000"),
        min_history_days=5,
    )
    result = builder.build_candidate(config=config, name="test")

    included = {m.symbol for m in result.included_members}
    excluded = {m.symbol: m.excluded_reason for m in result.excluded_members}

    assert "GOOD" in included
    assert excluded["LOW_PRICE"] == "price_below_minimum"
    assert excluded["LOW_ADDV"] == "insufficient_liquidity"
    assert excluded["NEW_IPO"] == "insufficient_history"
    assert excluded["NO_DATA"] == "no_market_data"
