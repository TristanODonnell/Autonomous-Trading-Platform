from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_repository import (
    CorporateActionRepository,
)


def _split(action_id: str, *, ratio: str = "10") -> CorporateAction:
    return CorporateAction(
        action_id=action_id,
        symbol="NVDA",
        action_type=CorporateActionType.SPLIT_FORWARD,
        effective_date=date(2024, 6, 10),
        split_ratio=float(Decimal(ratio)),
        cash_amount=None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
        meta={"old_rate": 1, "new_rate": int(ratio)},
    )


def test_upsert_creates_then_updates_by_action_id(db_session) -> None:
    repo = CorporateActionRepository(db_session)

    first = repo.upsert(_split("id-1"))
    db_session.flush()
    second = repo.upsert(_split("id-1", ratio="4"))
    db_session.flush()

    assert first.created is True
    assert second.created is False
    assert db_session.query(CorporateAction).count() == 1
    stored = repo.get_by_action_id("id-1")
    assert stored is not None
    assert stored.split_ratio == 4.0


def test_upsert_falls_back_to_the_natural_key_when_the_provider_reissues_an_id(
    db_session,
) -> None:
    """Same symbol/type/ex-date/source under a new provider id must update the stored
    row, not violate the unique constraint."""
    repo = CorporateActionRepository(db_session)
    repo.upsert(_split("id-old"))
    db_session.flush()

    result = repo.upsert(_split("id-new", ratio="4"))
    db_session.flush()

    assert result.created is False
    rows = db_session.query(CorporateAction).all()
    assert len(rows) == 1
    assert rows[0].action_id == "id-old"
    assert rows[0].split_ratio == 4.0
    assert (
        repo.get_by_natural_key(
            symbol="NVDA",
            action_type=CorporateActionType.SPLIT_FORWARD,
            effective_date=date(2024, 6, 10),
            source="alpaca",
        )
        is rows[0]
    )


def test_get_actions_for_symbols_between_filters_by_window(db_session) -> None:
    repo = CorporateActionRepository(db_session)
    repo.upsert(_split("id-1"))
    db_session.flush()

    assert (
        repo.get_actions_for_symbols_between(
            symbols=["NVDA"], start_date=date(2024, 6, 1), end_date=date(2024, 6, 30)
        )
        != []
    )
    assert (
        repo.get_actions_for_symbols_between(
            symbols=["NVDA"], start_date=date(2024, 7, 1), end_date=date(2024, 7, 31)
        )
        == []
    )
    assert (
        repo.get_actions_for_symbols_between(
            symbols=["AAPL"], start_date=date(2024, 6, 1), end_date=date(2024, 6, 30)
        )
        == []
    )


def test_upsert_accepts_the_pydantic_contract(db_session) -> None:
    from autonomous_trading_platform.contracts.market.corporate_action import (
        CorporateAction as CorporateActionContract,
    )

    repo = CorporateActionRepository(db_session)
    contract = CorporateActionContract(
        action_id="50199fac",
        symbol="NVDA",
        action_type=CorporateActionType.SPLIT_FORWARD,
        effective_date=date(2024, 6, 10),
        record_date=date(2024, 6, 7),
        split_ratio=Decimal("10"),
        cash_amount=None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
        metadata={"provider_type": "forward_split", "old_rate": 1, "new_rate": 10},
    )

    result = repo.upsert(contract)
    db_session.flush()

    assert result.created is True
    stored = repo.get_by_action_id("50199fac")
    assert stored is not None
    assert stored.split_ratio == 10.0
    assert stored.meta == {"provider_type": "forward_split", "old_rate": 1, "new_rate": 10}
    back = CorporateActionRepository.to_contract(stored)
    assert back.split_ratio == Decimal("10")
    assert back.symbol == "NVDA"
