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
from autonomous_trading_platform.universe.providers.alpaca_intraday_coverage_source import (
    AlpacaIntradayCoverageSource,
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


def _patch_pool_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.services.raw_market_pool_refresh_service.RawMarketPoolRefreshService",
        lambda *a, **k: SimpleNamespace(refresh=lambda **kw: None),
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.providers.point_in_time_index_provider.build_universe_screener",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        universe_hooks,
        "UniverseVersionRepository",
        lambda s: SimpleNamespace(get_included_members=lambda v: []),
    )


def _run_hook(**kwargs: Any) -> Any:
    return universe_hooks.run_universe_at_timestamp(
        session=SimpleNamespace(commit=lambda: None),  # type: ignore[arg-type]
        timestamp=_TS,
        replay_context=PlatformReplayContext.create(symbols=[], timestamp=_TS),
        **kwargs,
    )


def test_the_rotation_uses_the_candidate_it_just_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not "the latest stored candidate": another one may carry the same or a later date.
    rotation: dict[str, Any] = {}
    _patch_pool_refresh(monkeypatch)
    monkeypatch.setattr(cand_mod, "run_candidate_generation", lambda **k: _candidate({}))
    monkeypatch.setattr(
        universe_hooks, "run_universe_rotation", lambda **k: rotation.update(k) or _rotation()
    )

    result = _run_hook()

    assert result.status == "ok"
    assert rotation["candidate_version_id"] == "cand-1"


def test_no_coverage_check_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_pool_refresh(monkeypatch)
    monkeypatch.setattr(
        cand_mod, "run_candidate_generation", lambda **k: captured.update(k) or _candidate({})
    )
    monkeypatch.setattr(universe_hooks, "run_universe_rotation", lambda **k: _rotation())

    result = _run_hook()

    assert captured["coverage_source"] is None
    assert captured["config"].min_intraday_bar_coverage is None
    assert "min_intraday_bar_coverage" not in result.summary


def test_a_coverage_minimum_reaches_the_scorer_with_a_minute_bar_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _patch_pool_refresh(monkeypatch)
    monkeypatch.setattr(
        cand_mod,
        "run_candidate_generation",
        lambda **k: captured.update(k) or _candidate({"COST": "insufficient_intraday_coverage"}),
    )
    monkeypatch.setattr(universe_hooks, "run_universe_rotation", lambda **k: _rotation())

    result = _run_hook(min_intraday_bar_coverage=0.70)

    assert isinstance(captured["coverage_source"], AlpacaIntradayCoverageSource)
    assert captured["config"].min_intraday_bar_coverage == 0.70
    assert result.summary["min_intraday_bar_coverage"] == 0.70
    assert result.summary["rejections_by_reason"] == {"insufficient_intraday_coverage": 1}


def _failing_candidate_generation(**kwargs: Any) -> Any:
    raise RuntimeError("alpaca 5xx")


@pytest.mark.parametrize("options", [{}, {"min_intraday_bar_coverage": 0.70}])
def test_a_failed_scoring_run_does_not_rotate(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    # Rotating on an older stored candidate would look like a normal rotation while
    # using stale scores (and, with a coverage minimum, unchecked names).
    rotated: list[Any] = []
    _patch_pool_refresh(monkeypatch)
    monkeypatch.setattr(cand_mod, "run_candidate_generation", _failing_candidate_generation)

    def _rotate(**kwargs: Any) -> Any:
        rotated.append(kwargs)
        return _rotation()

    monkeypatch.setattr(universe_hooks, "run_universe_rotation", _rotate)

    result = _run_hook(**options)

    assert result.status == "failed"
    assert result.errors == ["candidate_generation_failed: alpaca 5xx"]
    assert rotated == []


def _raise_membership_unavailable(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("membership unavailable")


@pytest.mark.parametrize("failing_part", ["building the screener", "fetching the pool"])
def test_a_failed_pool_refresh_does_not_score_or_rotate(
    monkeypatch: pytest.MonkeyPatch, failing_part: str
) -> None:
    # Scoring reads the newest stored pool at or before the timestamp, so carrying on
    # would score another date's pool and report a normal rotation.
    later_steps: list[str] = []
    _patch_pool_refresh(monkeypatch)
    if failing_part == "building the screener":
        monkeypatch.setattr(
            "autonomous_trading_platform.universe.providers.point_in_time_index_provider.build_universe_screener",
            _raise_membership_unavailable,
        )
    else:
        monkeypatch.setattr(
            "autonomous_trading_platform.universe.services.raw_market_pool_refresh_service.RawMarketPoolRefreshService",
            lambda *a, **k: SimpleNamespace(refresh=_raise_membership_unavailable),
        )

    def _score(**kwargs: Any) -> Any:
        later_steps.append("score")
        return _candidate({})

    def _rotate(**kwargs: Any) -> Any:
        later_steps.append("rotate")
        return _rotation()

    monkeypatch.setattr(cand_mod, "run_candidate_generation", _score)
    monkeypatch.setattr(universe_hooks, "run_universe_rotation", _rotate)

    result = _run_hook()

    assert result.status == "failed"
    assert result.errors == ["screener_refresh_failed: membership unavailable"]
    assert later_steps == []
