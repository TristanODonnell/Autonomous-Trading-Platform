"""Fixture initial_state.promotion_rules seeding (portfolio rotation step 4E)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.platform_replay.initial_state_hooks import (
    apply_initial_state,
)
from autonomous_trading_platform.platform.replay.platform_replay_config import (
    InitialStateConfig,
)
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules

_NOW = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
_RULE = {
    "rule_id": "fixture_candidate_to_paper",
    "from_status": "candidate",
    "to_status": "approved_paper",
    "min_sharpe": 0.5,
    "max_drawdown": 0.25,
    "min_trade_count": 10,
}


def test_promotion_rules_are_seeded(db_session: Session) -> None:
    summary = apply_initial_state(
        session=db_session,
        initial_state=InitialStateConfig(promotion_rules=[_RULE]),
        timestamp=_NOW,
    )

    row = db_session.get(PromotionRules, "fixture_candidate_to_paper")
    assert summary["errors"] == []
    assert summary["promotion_rules_upserted"] == 1
    assert row is not None
    assert (row.from_status, row.to_status, row.is_active) == ("candidate", "approved_paper", True)
    assert (row.min_sharpe, row.max_drawdown, row.min_trade_count) == (0.5, 0.25, 10)
    assert row.min_days_tested is None


def test_promotion_rules_are_upserted_by_rule_id(db_session: Session) -> None:
    apply_initial_state(
        session=db_session,
        initial_state=InitialStateConfig(promotion_rules=[_RULE]),
        timestamp=_NOW,
    )

    apply_initial_state(
        session=db_session,
        initial_state=InitialStateConfig(promotion_rules=[{**_RULE, "min_sharpe": 1.0}]),
        timestamp=_NOW,
    )

    rows = db_session.query(PromotionRules).all()
    assert [(r.rule_id, r.min_sharpe) for r in rows] == [("fixture_candidate_to_paper", 1.0)]


def test_invalid_promotion_rule_is_reported_not_raised(db_session: Session) -> None:
    summary = apply_initial_state(
        session=db_session,
        initial_state=InitialStateConfig(promotion_rules=[{"rule_id": "broken"}]),
        timestamp=_NOW,
    )

    assert any("promotion_rule[broken]" in e for e in summary["errors"])
    assert db_session.get(PromotionRules, "broken") is None
