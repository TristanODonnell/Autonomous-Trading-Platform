"""The universe timeline builder, run against the real month-end rotation.

Only the two steps that call a data provider are replaced: the pool refresh is a no-op
and the scorer stores a candidate with the ranking the test gives for that date. The
rotation, its keep-and-add ranks, its churn guard and the repositories are the real ones.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.universe_timeline_service import (
    UniverseTimelineError,
    build_universe_timeline,
    timeline_schedule,
)
from autonomous_trading_platform.contracts.common.enums import UniverseStatus
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    TIMELINE_ROTATION_REASON,
    TIMELINE_SUMMARY_KEY,
    UniverseTimelineBuildResult,
    UniverseTimelineRules,
)
from autonomous_trading_platform.runtime.clock import RealMarketCalendar
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
    UniverseRotationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.jobs import run_candidate_generation as cand_mod
from tests.utilities.universe_seeding import seed_universe_version

_START = date(2024, 11, 18)
_END = date(2025, 2, 3)
_T_START = datetime(2024, 11, 18, 0, 0, tzinfo=UTC)
_T_NOV = datetime(2024, 11, 29, 23, 0, tzinfo=UTC)
_T_DEC = datetime(2024, 12, 31, 23, 0, tzinfo=UTC)
_T_JAN = datetime(2025, 1, 31, 23, 0, tzinfo=UTC)
_RULES = UniverseTimelineRules(
    screener_source="sp500_point_in_time", candidate_market_data="dataset"
)


def _names(*numbers: int) -> list[str]:
    return [f"S{n:02d}" for n in numbers]


_BASE = _names(*range(1, 41))
_TOP_20 = _names(*range(1, 21))
# S21 rises to rank 5 and S22 to rank 20; S20 slips to 25 (inside the keep rank of 30);
# S19 falls to 35 (outside it).
_SHIFTED = (
    _names(1, 2, 3, 4, 21)
    + _names(*range(5, 19))
    + _names(22, 23, 24, 25, 26, 20)
    + _names(*range(27, 36))
    + _names(19)
    + _names(*range(36, 41))
)
_AFTER_SHIFT = sorted(_names(*range(1, 19)) + _names(20, 21))


class _Market:
    """The ranking the scorer would produce on each step date."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.rankings: dict[datetime, list[str]] = {}
        self.unavailable: set[datetime] = set()
        self.scored: list[datetime] = []
        self.configs: list[Any] = []
        # Held names the scorer would keep only because of the lower coverage line.
        self.held_below_entry: dict[datetime, list[str]] = {}

    def run_candidate_generation(self, *, as_of: datetime, config: Any, **_: Any) -> Any:
        if as_of in self.unavailable:
            raise RuntimeError("bars unavailable")
        self.scored.append(as_of)
        self.configs.append(config)
        ranking = self.rankings[as_of]
        metadata = {
            "candidate_pool_size": len(ranking),
            "included_count": len(ranking),
            "filter_config": {"held_below_entry_coverage": self.held_below_entry.get(as_of, [])},
        }
        version = UniverseVersion(
            universe_version_id=f"cand-{as_of.isoformat()}-{len(self.scored)}",
            name=f"candidate_{as_of.date().isoformat()}",
            source="custom",
            created_at=as_of,
            effective_from=as_of,
            effective_to=None,
            status=UniverseStatus.CANDIDATE,
            config_hash=f"cand-{as_of.isoformat()}",
            generation_metadata_json=metadata,
        )
        members = [
            UniverseMember(
                universe_version_id=version.universe_version_id,
                symbol=symbol,
                rank=rank,
                score=1.0 / rank,
                included_reason="test",
            )
            for rank, symbol in enumerate(ranking, start=1)
        ]
        repo = UniverseVersionRepository(self._session)
        repo.insert_version(version)
        repo.insert_members(members)
        self._session.flush()
        return SimpleNamespace(
            version=version,
            included_members=members,
            excluded_members=[],
            generation_metadata=metadata,
        )


@pytest.fixture()
def market(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> _Market:
    fake = _Market(db_session)
    # The rotation job builds Settings(), which wants broker keys in paper mode. No
    # broker is called; without these the tests would need a developer's local .env.
    monkeypatch.setenv("PAPER_BROKER_API_KEY", "test-key")
    monkeypatch.setenv("PAPER_BROKER_API_SECRET", "test-secret")
    monkeypatch.setattr(cand_mod, "run_candidate_generation", fake.run_candidate_generation)
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.services.raw_market_pool_refresh_service.RawMarketPoolRefreshService",
        lambda *a, **k: SimpleNamespace(refresh=lambda **kw: None),
    )
    monkeypatch.setattr(
        "autonomous_trading_platform.universe.providers.point_in_time_index_provider.build_universe_screener",
        lambda *a, **k: object(),
    )
    fake.rankings = {_T_START: _BASE, _T_NOV: _BASE, _T_DEC: _SHIFTED, _T_JAN: _SHIFTED}
    return fake


@pytest.fixture()
def build(db_session: Session) -> Callable[..., UniverseTimelineBuildResult]:
    def _build(**overrides: Any) -> UniverseTimelineBuildResult:
        kwargs: dict[str, Any] = {
            "session": db_session,
            "start": _START,
            "end": _END,
            "rules": _RULES,
            "calendar": RealMarketCalendar(),
        }
        kwargs.update(overrides)
        return build_universe_timeline(**kwargs)

    return _build


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


def test_schedule_is_the_first_day_then_each_month_end_at_the_live_eod_time() -> None:
    schedule = timeline_schedule(_START, _END, RealMarketCalendar())

    assert schedule == [
        (_T_START, "start"),
        (_T_NOV, "month_end"),
        (_T_DEC, "month_end"),
        (_T_JAN, "month_end"),
    ]


def test_schedule_uses_the_last_session_when_the_month_ends_on_a_holiday() -> None:
    # 2024-03-29 was Good Friday: March's last session was Thursday the 28th.
    schedule = timeline_schedule(date(2024, 3, 1), date(2024, 4, 30), RealMarketCalendar())

    assert [ts for ts, _ in schedule] == [
        datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
        datetime(2024, 3, 28, 22, 0, tzinfo=UTC),
    ]


def test_schedule_refuses_an_empty_window() -> None:
    with pytest.raises(ValueError, match="must be after"):
        timeline_schedule(_START, _START, RealMarketCalendar())


def test_plan_lists_the_steps_and_touches_nothing(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult], db_session: Session
) -> None:
    result = build(plan_only=True)

    assert result.status == "planned"
    assert [step.outcome for step in result.steps] == ["planned"] * 4
    assert market.scored == []
    assert UniverseRotationRepository(db_session).get_for_reason(TIMELINE_ROTATION_REASON) == []


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def test_each_month_is_picked_from_the_month_before(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult], db_session: Session
) -> None:
    result = build()

    assert result.status == "complete" and result.errors == []
    assert [step.outcome for step in result.steps] == [
        "rotated",
        "unchanged",
        "rotated",
        "unchanged",
    ]
    start, november, december, january = result.steps
    assert start.symbols == _TOP_20
    assert november.symbols == _TOP_20
    assert november.universe_version_id == start.universe_version_id
    # A fresh top 20 for December would drop S20 (rank 25) and take S22 (rank 20). The
    # rotation keeps S20, which is inside rank 30, and swaps only S19 for S21.
    assert december.symbols == _AFTER_SHIFT
    assert (december.added, december.removed) == (["S21"], ["S19"])
    assert december.churn_pct == pytest.approx(0.1)
    assert january.universe_version_id == december.universe_version_id
    assert january.added == [] and january.removed == []
    assert result.symbols_union == sorted({*_TOP_20, "S21"})
    assert [step.eligible_count for step in result.steps] == [40] * 4

    # The stored versions answer "which list on this date" for any day of the window.
    repo = UniverseVersionRepository(db_session)

    def members_at(moment: datetime) -> list[str]:
        version = repo.get_version_effective_at(moment)
        assert version is not None
        return sorted(repo.get_included_symbols(version.universe_version_id))

    assert repo.get_version_effective_at(datetime(2024, 11, 17, 23, 59, tzinfo=UTC)) is None
    assert members_at(_T_START) == _TOP_20
    assert members_at(datetime(2024, 12, 31, 20, 0, tzinfo=UTC)) == _TOP_20
    assert members_at(_T_DEC) == _AFTER_SHIFT
    assert members_at(datetime(2025, 6, 2, 14, 30, tzinfo=UTC)) == _AFTER_SHIFT

    # One record per step, each stamped with the build's first day and rules.
    records = UniverseRotationRepository(db_session).get_for_reason(TIMELINE_ROTATION_REASON)
    assert [(r.created_at, r.status) for r in records] == [
        (_T_START, "completed"),
        (_T_NOV, "skipped"),
        (_T_DEC, "completed"),
        (_T_JAN, "skipped"),
    ]
    assert {repr(r.summary_json[TIMELINE_SUMMARY_KEY]) for r in records if r.summary_json} == {
        repr(
            {
                "window_start": "2024-11-18",
                "screener_source": "sp500_point_in_time",
                "candidate_market_data": "dataset",
            }
        )
    }


def test_a_second_run_reuses_every_stored_month(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult]
) -> None:
    first = build()
    scored_once = list(market.scored)

    second = build()

    assert market.scored == scored_once
    assert second.status == "complete"
    assert all(step.reused for step in second.steps)
    assert [(s.outcome, s.symbols, s.added, s.removed) for s in second.steps] == [
        (s.outcome, s.symbols, s.added if s.outcome == "rotated" else [], s.removed)
        for s in first.steps
    ]
    assert second.symbols_union == first.symbols_union


def test_a_failed_month_stops_the_build_and_the_next_run_continues(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult]
) -> None:
    market.unavailable.add(_T_DEC)

    stopped = build()

    assert stopped.status == "stopped"
    assert [step.outcome for step in stopped.steps] == ["rotated", "unchanged", "failed"]
    assert stopped.errors == [
        f"{_T_DEC.isoformat()}: candidate_generation_failed: bars unavailable"
    ]

    market.unavailable.clear()
    resumed = build()

    assert resumed.status == "complete"
    assert [(step.outcome, step.reused) for step in resumed.steps] == [
        ("rotated", True),
        ("unchanged", True),
        ("rotated", False),
        ("unchanged", False),
    ]
    assert resumed.steps[2].symbols == _AFTER_SHIFT


def test_a_month_the_churn_guard_refuses_keeps_the_list_in_force(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult], db_session: Session
) -> None:
    # Seven of the twenty names are gone from December's pool: 35% churn at the least,
    # over the 30% guard. On the live platform the step fails and the universe stays.
    market.rankings[_T_DEC] = _names(*range(1, 14)) + _names(*range(21, 41))
    market.rankings[_T_JAN] = _BASE

    result = build()

    assert result.status == "complete"
    assert [step.outcome for step in result.steps] == [
        "rotated",
        "unchanged",
        "refused",
        "unchanged",
    ]
    december = result.steps[2]
    assert december.symbols == _TOP_20
    assert december.universe_version_id == result.steps[0].universe_version_id
    assert december.detail is not None and "exceeds max_churn_pct" in december.detail
    assert december.eligible_count == 33

    records = UniverseRotationRepository(db_session).get_for_reason(TIMELINE_ROTATION_REASON)
    assert [r.status for r in records] == ["completed", "skipped", "rejected", "skipped"]

    again = build()
    assert [(step.outcome, step.reused) for step in again.steps][2] == ("refused", True)
    assert again.steps[2].symbols == _TOP_20


def test_the_lower_coverage_line_reaches_the_scorer_and_the_stamp(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult], db_session: Session
) -> None:
    rules = _RULES.model_copy(
        update={"min_intraday_bar_coverage": 0.7, "held_min_intraday_bar_coverage": 0.6}
    )
    market.held_below_entry[_T_DEC] = ["S07"]
    # The coverage source is a provider call; the scorer here is the test's.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "autonomous_trading_platform.application.services.platform_replay.universe_hooks"
            ".build_candidate_coverage_source",
            lambda minimum: None,
        )
        result = build(rules=rules)
        again = build(rules=rules)

    assert {
        (c.min_intraday_bar_coverage, c.held_min_intraday_bar_coverage) for c in market.configs
    } == {(0.7, 0.6)}
    assert [step.held_below_entry_coverage for step in result.steps] == [[], [], ["S07"], []]
    # A reused step reads the same from its stored candidate.
    assert all(step.reused for step in again.steps)
    assert [step.held_below_entry_coverage for step in again.steps] == [[], [], ["S07"], []]
    assert [step.eligible_count for step in again.steps] == [40] * 4

    records = UniverseRotationRepository(db_session).get_for_reason(TIMELINE_ROTATION_REASON)
    assert records[0].summary_json is not None
    assert records[0].summary_json[TIMELINE_SUMMARY_KEY] == {
        "window_start": "2024-11-18",
        "screener_source": "sp500_point_in_time",
        "candidate_market_data": "dataset",
        "min_intraday_bar_coverage": 0.7,
        "held_min_intraday_bar_coverage": 0.6,
    }

    # One line or two is part of the rules: the one-line build is another timeline.
    with pytest.raises(UniverseTimelineError, match="reused only under the first day and rules"):
        build(rules=_RULES.model_copy(update={"min_intraday_bar_coverage": 0.7}))


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_other_rules_are_refused(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult]
) -> None:
    build()

    with pytest.raises(UniverseTimelineError, match="reused only under the first day and rules"):
        build(rules=_RULES.model_copy(update={"min_intraday_bar_coverage": 0.7}))


def test_another_first_day_is_refused(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult]
) -> None:
    build()

    with pytest.raises(UniverseTimelineError, match="window_start"):
        build(start=date(2024, 12, 2))


def test_a_universe_this_timeline_did_not_make_is_refused(
    market: _Market, build: Callable[..., UniverseTimelineBuildResult], db_session: Session
) -> None:
    seed_universe_version(
        db_session, symbols=["AAPL", "MSFT"], effective_from=datetime(2024, 6, 3, tzinfo=UTC)
    )

    with pytest.raises(UniverseTimelineError, match="were not made by this timeline"):
        build()
    assert market.scored == []
