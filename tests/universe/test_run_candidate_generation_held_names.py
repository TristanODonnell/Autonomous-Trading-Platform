"""run_candidate_generation: which names it tells the scorer are held.

The lower coverage line applies to the members of the universe in force on the
scoring date. The job reads them; the scorer only receives the list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.universe.jobs import run_candidate_generation as cand_mod
from autonomous_trading_platform.universe.types import CandidateGenerationConfig
from tests.utilities.universe_seeding import seed_universe_version

_SEEDED_AT = datetime(2024, 11, 18, tzinfo=UTC)
_AS_OF = datetime(2024, 12, 31, 23, 0, tzinfo=UTC)


class _KeepOpen:
    """The test's session, with ``close`` turned off: the job closes its session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def close(self) -> None:
        return None


@pytest.fixture()
def held_seen(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    class _Builder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_candidate(self, *, held_symbols: list[str], **kwargs: Any) -> Any:
            seen.append(sorted(held_symbols))
            return SimpleNamespace()

    monkeypatch.setattr(cand_mod, "get_session", lambda: _KeepOpen(db_session))
    monkeypatch.setattr(cand_mod, "UniverseCandidateBuilder", _Builder)
    seed_universe_version(db_session, symbols=["MSFT", "AAPL"], effective_from=_SEEDED_AT)
    return seen


def _config(as_of: datetime, **overrides: Any) -> CandidateGenerationConfig:
    return CandidateGenerationConfig(as_of=as_of, min_intraday_bar_coverage=0.70, **overrides)


def test_the_members_in_force_are_passed_when_the_lower_line_is_set(
    held_seen: list[list[str]],
) -> None:
    cand_mod.run_candidate_generation(
        as_of=_AS_OF, config=_config(_AS_OF, held_min_intraday_bar_coverage=0.60), dry_run=True
    )

    assert held_seen == [["AAPL", "MSFT"]]


def test_no_names_are_held_before_the_first_universe(held_seen: list[list[str]]) -> None:
    before = datetime(2024, 11, 1, tzinfo=UTC)

    cand_mod.run_candidate_generation(
        as_of=before, config=_config(before, held_min_intraday_bar_coverage=0.60), dry_run=True
    )

    assert held_seen == [[]]


def test_nothing_is_looked_up_without_the_lower_line(held_seen: list[list[str]]) -> None:
    cand_mod.run_candidate_generation(as_of=_AS_OF, config=_config(_AS_OF), dry_run=True)

    assert held_seen == [[]]
