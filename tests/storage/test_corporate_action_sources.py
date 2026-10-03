"""The SOR-backed action sources: research loads every applicable action (splits and
cash dividends) for its symbols; the context builder's split source sees splits only."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_repository import (
    CorporateActionRepository,
)
from autonomous_trading_platform.storage.sor.services.corporate_action_split_source import (
    SorCorporateActionSource,
    SorSplitSource,
)


def _row(action_id: str, symbol: str, action_type: CorporateActionType, ex_date: date, **kw):
    return CorporateAction(
        action_id=action_id,
        symbol=symbol,
        action_type=action_type,
        effective_date=ex_date,
        split_ratio=kw.get("split_ratio"),
        cash_amount=kw.get("cash_amount"),
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
        meta={},
    )


def _seed(db_session) -> None:
    repo = CorporateActionRepository(db_session)
    for row in (
        _row(
            "nvda-split",
            "NVDA",
            CorporateActionType.SPLIT_FORWARD,
            date(2024, 6, 10),
            split_ratio=10.0,
        ),
        _row(
            "nvda-div",
            "NVDA",
            CorporateActionType.CASH_DIVIDEND,
            date(2024, 6, 11),
            cash_amount=Decimal("0.01"),
        ),
        # ratio 1 = nothing to apply
        _row(
            "nvda-noop",
            "NVDA",
            CorporateActionType.SPLIT_FORWARD,
            date(2024, 6, 12),
            split_ratio=1.0,
        ),
        _row(
            "aapl-div",
            "AAPL",
            CorporateActionType.CASH_DIVIDEND,
            date(2024, 5, 10),
            cash_amount=Decimal("0.25"),
        ),
        _row(
            "msft-div",
            "MSFT",
            CorporateActionType.CASH_DIVIDEND,
            date(2024, 6, 11),
            cash_amount=Decimal("0.75"),
        ),
    ):
        repo.insert(row)
    db_session.flush()


def test_research_source_returns_applicable_actions_for_the_symbols(db_session) -> None:
    _seed(db_session)
    source = SorCorporateActionSource(db_session)

    actions = source.actions_for(
        symbols=["nvda", "AAPL"], start_date=date(2024, 5, 1), end_date=date(2024, 6, 30)
    )

    assert [a.action_id for a in actions] == ["aapl-div", "nvda-split", "nvda-div"]
    assert actions[1].split_ratio == 10
    assert actions[2].cash_amount == Decimal("0.01")


def test_research_source_respects_the_window(db_session) -> None:
    _seed(db_session)
    source = SorCorporateActionSource(db_session)

    actions = source.actions_for(
        symbols=["NVDA", "AAPL"], start_date=date(2024, 6, 1), end_date=date(2024, 6, 10)
    )

    assert [a.action_id for a in actions] == ["nvda-split"]


def test_split_source_returns_splits_only(db_session) -> None:
    _seed(db_session)

    splits = SorSplitSource(db_session).splits_for("NVDA", date(2024, 5, 1), date(2024, 6, 30))

    assert [a.action_id for a in splits] == ["nvda-split"]
