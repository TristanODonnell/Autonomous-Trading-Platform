"""Research never re-seeds a strategy that already has a governance record."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
    _seed_research_governance,
    _seed_research_governance_from_intelligence,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance

_T0 = datetime(2024, 1, 2, tzinfo=UTC)
_T1 = datetime(2024, 2, 1, 21, tzinfo=UTC)


def _governance(session: Session, strategy_id: str, state: str, config_hash: str) -> None:
    session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=config_hash,
            current_state=state,
            experiment_id="seed",
            source_run_id=None,
            submitted_at=_T0,
            updated_at=_T0,
            submitted_by="test",
        )
    )
    session.flush()


def _rows(session: Session, strategy_id: str) -> list[tuple[str, str]]:
    return [
        (row.config_hash, row.current_state)
        for row in session.query(StrategyGovernance).filter_by(strategy_id=strategy_id).all()
    ]


def _seed_from_intelligence(session: Session, *strategy_ids: str) -> None:
    _seed_research_governance_from_intelligence(
        session=session,
        summaries=[SimpleNamespace(strategy_id=sid) for sid in strategy_ids],
        config_by_id={},
        sim_by_id={},
        experiment_id="replay_research_202402",
        now_utc=_T1,
    )


@pytest.mark.parametrize("state", ["retired", "candidate", "approved_for_paper_trading"])
def test_existing_strategy_is_not_reseeded_under_another_config_hash(
    db_session: Session, state: str
) -> None:
    _governance(db_session, "mr__abc", state, config_hash="seeded_elsewhere")

    _seed_from_intelligence(db_session, "mr__abc")
    _seed_research_governance(
        session=db_session,
        survivors=[SimpleNamespace(strategy_id="mr__abc")],
        experiment_id="replay_research_202402",
        now_utc=_T1,
    )

    assert _rows(db_session, "mr__abc") == [("seeded_elsewhere", state)]


def test_new_strategy_is_seeded_as_candidate(db_session: Session) -> None:
    _seed_from_intelligence(db_session, "brand_new")

    assert [state for _, state in _rows(db_session, "brand_new")] == ["candidate"]


def test_retired_row_is_never_backfilled(db_session: Session) -> None:
    _governance(db_session, "old", "retired", config_hash="h")

    _seed_research_governance_from_intelligence(
        session=db_session,
        summaries=[SimpleNamespace(strategy_id="old")],
        config_by_id={},
        sim_by_id={"old": SimpleNamespace(run_id="run_new")},
        experiment_id="replay_research_202402",
        now_utc=_T1,
    )

    row = db_session.query(StrategyGovernance).filter_by(strategy_id="old").one()
    assert row.source_run_id is None
    assert row.updated_at == _T0
