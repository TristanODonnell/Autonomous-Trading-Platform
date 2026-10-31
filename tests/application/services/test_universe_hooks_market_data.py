"""run_universe_at_timestamp: candidate market-data switch and result summary (Finding 16)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from autonomous_trading_platform.application.services.platform_replay import universe_hooks
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.universe.jobs import run_candidate_generation as cand_mod
from autonomous_trading_platform.universe.providers.alpaca_daily_bar_source import (
    AlpacaDailyBarSource,
)

_TS = datetime(2026, 10, 30, 22, 0, tzinfo=UTC)


def test_build_candidate_bar_source() -> None:
    assert universe_hooks.build_candidate_bar_source("dataset") is None
    assert isinstance(
        universe_hooks.build_candidate_bar_source("alpaca_daily"), AlpacaDailyBarSource
    )
    with pytest.raises(ValueError, match="candidate_market_data"):
        universe_hooks.build_candidate_bar_source("parquet")


def _candidate(excluded: dict[str, str]) -> Any:
    return SimpleNamespace(
        version=SimpleNamespace(universe_version_id="cand-1"),
        included_members=[SimpleNamespace(symbol="AAPL")],
        excluded_members=[
            SimpleNamespace(symbol=s, excluded_reason=r) for s, r in excluded.items()
        ],
        generation_metadata={
            "candidate_pool_size": 1 + len(excluded),
            "market_data_source": "bar_source:alpaca_daily",
        },
    )


def _rotation() -> Any:
    return SimpleNamespace(
        rotation_record=SimpleNamespace(
            churn_pct=0.1, rotation_id="rot-1", previous_universe_version_id="uv-old"
        ),
        new_version=SimpleNamespace(universe_version_id="uv-new"),
        rebalance_result=SimpleNamespace(added=["NVDA"], removed=["IBM"], retained=["AAPL"] * 19),
        skipped=False,
        skip_reason=None,
    )


def test_live_rotation_fetches_candidate_bars_and_reports_the_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_candidate_generation(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _candidate(
            {"IBM": "price_below_minimum", "XYZ": "no_market_data", "ABC": "no_market_data"}
        )

    class _Refresh:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def refresh(self, **kwargs: Any) -> None:
            captured["refresh"] = kwargs

    monkeypatch.setattr(cand_mod, "run_candidate_generation", fake_candidate_generation)
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.services.raw_market_pool_refresh_service.RawMarketPoolRefreshService",
        _Refresh,
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.providers.point_in_time_index_provider.build_universe_screener",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(universe_hooks, "run_universe_rotation", lambda **k: _rotation())

    class _VersionRepo:
        def __init__(self, session: Any) -> None:
            pass

        def get_included_members(self, version_id: str) -> list[str]:
            return ["x"] * 20

    monkeypatch.setattr(universe_hooks, "UniverseVersionRepository", _VersionRepo)

    session = SimpleNamespace(commit=lambda: None)
    context = PlatformReplayContext.create(symbols=[], timestamp=_TS, actor="scheduler")
    context.dataset_version_id = "raw_bars_one_session"

    result = universe_hooks.run_universe_at_timestamp(
        session=session,  # type: ignore[arg-type]
        timestamp=_TS,
        replay_context=context,
        force_rotation=False,
        rotation_reason="scheduler_monthly",
        candidate_market_data="alpaca_daily",
    )

    assert isinstance(captured["bar_source"], AlpacaDailyBarSource)
    assert captured["dataset_version_id"] == "raw_bars_one_session"
    assert captured["rebalance_reason"] == "scheduler_monthly_monthly"
    assert result.status == "ok" and result.rotation_applied and result.churn_pct == 0.1
    assert result.summary["added"] == ["NVDA"] and result.summary["removed"] == ["IBM"]
    assert result.summary["retained_count"] == 19
    assert result.summary["candidate_version_id"] == "cand-1"
    assert result.summary["market_data_source"] == "bar_source:alpaca_daily"
    assert result.summary["rejections_by_reason"] == {"no_market_data": 2, "price_below_minimum": 1}
    assert result.summary["previous_universe_version_id"] == "uv-old"


def test_replays_keep_the_stored_dataset_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        cand_mod,
        "run_candidate_generation",
        lambda **k: captured.update(k) or _candidate({}),
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.services.raw_market_pool_refresh_service.RawMarketPoolRefreshService",
        lambda *a, **k: SimpleNamespace(refresh=lambda **kw: None),
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.providers.point_in_time_index_provider.build_universe_screener",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(universe_hooks, "run_universe_rotation", lambda **k: _rotation())
    monkeypatch.setattr(
        universe_hooks,
        "UniverseVersionRepository",
        lambda s: SimpleNamespace(get_included_members=lambda v: []),
    )
    context = PlatformReplayContext.create(symbols=[], timestamp=_TS)
    context.dataset_version_id = "raw_bars_cumulative"

    universe_hooks.run_universe_at_timestamp(
        session=SimpleNamespace(commit=lambda: None),  # type: ignore[arg-type]
        timestamp=_TS,
        replay_context=context,
    )

    assert captured["bar_source"] is None
    assert captured["dataset_version_id"] == "raw_bars_cumulative"
