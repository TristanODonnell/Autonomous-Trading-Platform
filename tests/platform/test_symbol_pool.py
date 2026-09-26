"""Fixture symbol_pool: replay symbols derived point-in-time instead of hand-picked."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from autonomous_trading_platform.platform.replay.platform_replay_config import (
    PlatformReplayFixture,
    merge_fixture_with_cli,
    resolve_symbol_pool,
    validate_plan,
)


def _fixture(**block: object) -> PlatformReplayFixture:
    fixture: PlatformReplayFixture = PlatformReplayFixture.model_validate(
        {"platform_replay": {"name": "t", "start": "2023-01-03", "end": "2023-06-30", **block}}
    )
    return fixture


def test_pool_replaces_required_symbols() -> None:
    params = merge_fixture_with_cli(fixture=_fixture(symbol_pool={"top_n": 50}))
    assert params.symbols == []
    assert params.symbol_pool is not None and params.symbol_pool.top_n == 50
    assert params.screener_source == "sp500_point_in_time"
    plan = validate_plan(params)
    assert plan["valid"], plan["issues"]
    assert plan["symbol_pool"]["source"] == "sp500_point_in_time"


def test_no_symbols_and_no_pool_is_an_error() -> None:
    with pytest.raises(ValueError, match="symbol_pool"):
        merge_fixture_with_cli(fixture=_fixture())


def test_cli_symbols_override_the_pool() -> None:
    params = merge_fixture_with_cli(
        fixture=_fixture(symbol_pool={"top_n": 50}), cli_symbols=["SPY"]
    )
    assert params.symbol_pool is None
    assert params.screener_source == "alpaca_active"


def test_invalid_pool_rejected() -> None:
    with pytest.raises(ValueError):
        _fixture(symbol_pool={"top_n": 0})
    with pytest.raises(ValueError):
        _fixture(symbol_pool={"source": "nasdaq_magic"})


def test_resolve_ranks_as_of_start_and_keeps_explicit_extras(monkeypatch) -> None:
    calls: dict = {}

    def fake_screener(source, *, as_of, top_n):
        calls.update(source=source, as_of=as_of, top_n=top_n)
        return SimpleNamespace(
            fetch_symbols=lambda: [SimpleNamespace(symbol=s) for s in ("SIVB", "AAPL")]
        )

    import autonomous_trading_platform.universe.providers.point_in_time_index_provider as pit

    monkeypatch.setattr(pit, "build_universe_screener", fake_screener)
    params = merge_fixture_with_cli(
        fixture=_fixture(symbol_pool={"top_n": 2}, symbols=["SPY", "AAPL"])
    )

    assert resolve_symbol_pool(params) == ["AAPL", "SIVB", "SPY"]
    assert calls == {"source": "sp500_point_in_time", "as_of": date(2023, 1, 3), "top_n": 2}


def test_resolve_fails_loudly_when_pool_is_empty(monkeypatch) -> None:
    import autonomous_trading_platform.universe.providers.point_in_time_index_provider as pit

    monkeypatch.setattr(
        pit,
        "build_universe_screener",
        lambda *a, **k: SimpleNamespace(fetch_symbols=lambda: []),
    )
    with pytest.raises(ValueError, match="resolved to no symbols"):
        resolve_symbol_pool(merge_fixture_with_cli(fixture=_fixture(symbol_pool={})))


def test_without_pool_resolution_is_identity() -> None:
    params = merge_fixture_with_cli(fixture=_fixture(symbols=["spy"]))
    assert resolve_symbol_pool(params) == ["SPY"]
