"""Applying stored corporate actions to the sleeves and the backtest account book
(plan 5d-B): splits rewrite quantity and cost with no P&L, dividends are income, every
application is recorded once, and positions changed on/after the ex-date are skipped."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.accounting.corporate_action_application import (
    ACCOUNT_SCOPE,
    CorporateActionBook,
)
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import (
    SleeveBook,
    SleeveEntrySource,
)
from autonomous_trading_platform.contracts.common.enums import (
    CorporateActionType,
    OrderSource,
    Side,
)
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.services.corporate_action_accounting_service import (
    CorporateActionAccountingService,
)
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    StrategySleeveLedgerService,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import (
    CashSnapshot as OrmCashSnapshot,
)
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem as OrmPositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

_RUN = UUID("00000000-0000-0000-0000-00000000aaaa")
_BEFORE = datetime(2024, 6, 7, 19, 50, tzinfo=UTC)  # last pre-split fill
_EX_DATE = date(2024, 6, 10)
_TICK = datetime(2024, 6, 10, 13, 35, tzinfo=UTC)  # first cycle on the ex-date


def _fill(fill_id: str, symbol: str, qty: str, price: str, *, at: datetime = _BEFORE) -> Fill:
    return Fill(
        fill_id=fill_id,
        broker_order_id=f"order-{fill_id}",
        intent_id=UUID("00000000-0000-0000-0000-00000000bbbb"),
        run_id=_RUN,
        timestamp=at,
        symbol=symbol,
        side=Side.BUY,
        quantity=Decimal(qty),
        price=Decimal(price),
    )


def _store_action(
    uow: SorUnitOfWork,
    *,
    action_id: str,
    action_type: CorporateActionType,
    symbol: str = "NVDA",
    effective_date: date = _EX_DATE,
    ratio: str | None = None,
    cash: str | None = None,
) -> CorporateAction:
    contract = CorporateAction(
        action_id=action_id,
        symbol=symbol,
        action_type=action_type,
        effective_date=effective_date,
        split_ratio=Decimal(ratio) if ratio is not None else None,
        cash_amount=Decimal(cash) if cash is not None else None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    uow.corporate_actions.upsert(contract)
    uow.session.flush()
    return contract


def _seed_account(
    uow: SorUnitOfWork, *, symbol: str, qty: str, avg_cost: str, cash: str, at: datetime = _BEFORE
) -> None:
    header = uow.position_snapshots.get_or_create_header(
        snapshot_id=uuid.uuid4(), run_id=_RUN, timestamp=at, source=OrderSource.LEDGER
    )
    header.positions = [
        OrmPositionSnapshotItem(
            symbol=symbol,
            quantity=Decimal(qty),
            avg_cost=Decimal(avg_cost),
            market_price=Decimal(avg_cost),
            market_value=Decimal(qty) * Decimal(avg_cost),
            unrealized_pnl=Decimal("0"),
        )
    ]
    uow.cash_snapshots.upsert(
        OrmCashSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=_RUN,
            timestamp=at,
            currency="USD",
            cash=Decimal(cash),
            buying_power=Decimal(cash),
            reserved_cash=Decimal("0"),
            equity=Decimal(cash) + Decimal(qty) * Decimal(avg_cost),
            source=OrderSource.LEDGER,
            settled_cash=Decimal(cash),
            unsettled_cash=Decimal("0"),
        )
    )
    uow.session.flush()


@pytest.fixture
def uow(db_session: Session) -> SorUnitOfWork:
    return SorUnitOfWork(db_session)


@pytest.fixture
def real() -> StrategySleeveLedgerService:
    return StrategySleeveLedgerService()


@pytest.fixture
def shadow() -> StrategySleeveLedgerService:
    return StrategySleeveLedgerService(book=SleeveBook.SHADOW)


@pytest.fixture
def service() -> CorporateActionAccountingService:
    return CorporateActionAccountingService()


class TestSleeveLedgerRule:
    def test_forward_split_rewrites_the_sleeve_position_without_pnl(self, uow, real) -> None:
        real.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        action = _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )

        outcome = real.apply_corporate_action(
            uow, action=action, strategy_id="macd", timestamp=_TICK, run_id=_RUN
        )

        assert outcome is not None
        entry, adjustment = outcome
        position = real.positions(uow, "macd")["NVDA"]
        assert position.quantity == Decimal("30")
        assert position.avg_cost == Decimal("120.7185")
        assert entry.source is SleeveEntrySource.CORPORATE_ACTION
        assert entry.side is Side.BUY and entry.quantity == Decimal("27")
        assert entry.price == Decimal("0")
        assert entry.realized_pnl == Decimal("0")
        realized, fees = uow.strategy_sleeves.realized_totals("macd")
        assert realized == Decimal("0") and fees == Decimal("0")
        # idempotent
        assert (
            real.apply_corporate_action(
                uow, action=action, strategy_id="macd", timestamp=_TICK, run_id=_RUN
            )
            is None
        )
        assert real.positions(uow, "macd")["NVDA"].quantity == Decimal("30")

    def test_selling_post_split_shares_books_the_right_pnl(self, uow, real) -> None:
        real.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        action = _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )
        real.apply_corporate_action(uow, action=action, strategy_id="macd", timestamp=_TICK)

        sell = Fill(
            fill_id="f2",
            broker_order_id="o2",
            intent_id=UUID("00000000-0000-0000-0000-00000000bbbb"),
            run_id=_RUN,
            timestamp=_TICK + timedelta(minutes=5),
            symbol="NVDA",
            side=Side.SELL,
            quantity=Decimal("30"),
            price=Decimal("120.75"),
        )
        entry = real.apply_fill(uow, fill=sell, strategy_id="macd")
        assert entry is not None
        # (120.75 − 120.7185) × 30 = 0.945, not the −3,259 the 6-month run booked
        assert entry.realized_pnl == Decimal("0.945")
        assert "NVDA" not in real.positions(uow, "macd")

    def test_reverse_split_pays_the_fraction_in_cash(self, uow, real) -> None:
        real.apply_fill(uow, fill=_fill("f1", "ATRA", "110", "2"), strategy_id="mr")
        action = _store_action(
            uow,
            action_id="r1",
            action_type=CorporateActionType.SPLIT_REVERSE,
            symbol="ATRA",
            ratio="0.04",
        )

        outcome = real.apply_corporate_action(
            uow,
            action=action,
            strategy_id="mr",
            timestamp=_TICK,
            cash_in_lieu_price=Decimal("55"),
        )

        assert outcome is not None
        entry, adjustment = outcome
        position = real.positions(uow, "mr")["ATRA"]
        assert position.quantity == Decimal("4")
        assert position.avg_cost == Decimal("50")
        assert entry.side is Side.SELL and entry.quantity == Decimal("106")
        assert adjustment.cash_delta == Decimal("22")
        assert entry.realized_pnl == Decimal("2")

    def test_dividend_is_realized_income_without_touching_the_position(self, uow, real) -> None:
        real.apply_fill(uow, fill=_fill("f1", "AAPL", "41", "123.7"), strategy_id="mr")
        action = _store_action(
            uow,
            action_id="d1",
            action_type=CorporateActionType.CASH_DIVIDEND,
            symbol="AAPL",
            cash="0.24",
        )

        outcome = real.apply_corporate_action(uow, action=action, strategy_id="mr", timestamp=_TICK)

        assert outcome is not None
        entry, adjustment = outcome
        position = real.positions(uow, "mr")["AAPL"]
        assert position.quantity == Decimal("41") and position.avg_cost == Decimal("123.7")
        assert entry.quantity == Decimal("41") and entry.price == Decimal("0.24")
        assert entry.realized_pnl == Decimal("9.84")
        realized, _ = uow.strategy_sleeves.realized_totals("mr")
        assert realized == Decimal("9.84")

    def test_no_position_means_nothing_to_apply(self, uow, real) -> None:
        action = _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )
        assert (
            real.apply_corporate_action(uow, action=action, strategy_id="macd", timestamp=_TICK)
            is None
        )

    def test_shadow_book_applies_the_same_rule(self, uow, shadow) -> None:
        shadow.apply_fill(uow, fill=_fill("sf1", "NVDA", "2", "1200"), strategy_id="deck")
        action = _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )
        outcome = shadow.apply_corporate_action(
            uow, action=action, strategy_id="deck", timestamp=_TICK
        )
        assert outcome is not None
        assert shadow.positions(uow, "deck")["NVDA"].quantity == Decimal("20")
        assert uow.shadow_sleeves.realized_totals("deck") == (Decimal("0"), Decimal("0"))


class TestAccountingService:
    def test_applies_split_to_every_book_once_and_records_it(
        self, uow, real, shadow, service
    ) -> None:
        real.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        real.apply_fill(uow, fill=_fill("f2", "NVDA", "3", "1206.4"), strategy_id="mom")
        real.apply_fill(uow, fill=_fill("f3", "AAPL", "10", "190"), strategy_id="macd")
        shadow.apply_fill(uow, fill=_fill("sf1", "NVDA", "2", "1200"), strategy_id="deck")
        _seed_account(uow, symbol="NVDA", qty="6", avg_cost="1206.7925", cash="50000")
        _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )

        report = service.apply_due_actions(
            uow,
            as_of=_EX_DATE,
            timestamp=_TICK,
            prices={"NVDA": 120.48},
            adjust_account_book=True,
            run_id=_RUN,
        )

        assert report.applied_count == 4
        books = {(a.book, a.scope) for a in report.applied}
        assert books == {
            (CorporateActionBook.SLEEVE, "macd"),
            (CorporateActionBook.SLEEVE, "mom"),
            (CorporateActionBook.SHADOW_SLEEVE, "deck"),
            (CorporateActionBook.ACCOUNT, ACCOUNT_SCOPE),
        }
        assert report.skipped == []
        assert report.pending_symbols == {"NVDA"}  # due today: no adoption on NVDA
        assert real.positions(uow, "macd")["NVDA"].quantity == Decimal("30")
        assert real.positions(uow, "mom")["NVDA"].quantity == Decimal("30")
        assert real.positions(uow, "macd")["AAPL"].quantity == Decimal("10")
        assert shadow.positions(uow, "deck")["NVDA"].quantity == Decimal("20")
        assert real.aggregate_quantities(uow)["NVDA"] == Decimal("60")

        latest = uow.position_snapshots.get_latest()
        assert latest is not None
        by_symbol = {item.symbol: item for item in latest.positions}
        assert by_symbol["NVDA"].quantity == Decimal("60")
        assert by_symbol["NVDA"].avg_cost == Decimal("120.67925")
        assert by_symbol["NVDA"].market_price == Decimal("120.48")
        cash = uow.cash_snapshots.get_latest()
        assert cash is not None and cash.cash == Decimal("50000")  # no cash from a clean split

        # second run: everything already recorded → no-op
        again = service.apply_due_actions(
            uow,
            as_of=_EX_DATE,
            timestamp=_TICK + timedelta(minutes=5),
            prices={"NVDA": 120.48},
            adjust_account_book=True,
            run_id=_RUN,
        )
        assert again.applied_count == 0
        assert real.positions(uow, "macd")["NVDA"].quantity == Decimal("30")
        assert len(uow.corporate_action_applications.list_all()) == 4

    def test_dividend_credits_backtest_cash_and_sleeve_income(self, uow, real, service) -> None:
        real.apply_fill(uow, fill=_fill("f1", "AAPL", "41", "123.7"), strategy_id="mr")
        _seed_account(uow, symbol="AAPL", qty="41", avg_cost="123.7", cash="1000")
        _store_action(
            uow,
            action_id="d1",
            action_type=CorporateActionType.CASH_DIVIDEND,
            symbol="AAPL",
            cash="0.24",
        )

        report = service.apply_due_actions(
            uow, as_of=_EX_DATE, timestamp=_TICK, adjust_account_book=True, run_id=_RUN
        )

        assert report.applied_count == 2
        cash = uow.cash_snapshots.get_latest()
        assert cash is not None
        assert cash.cash == Decimal("1009.84")
        assert cash.buying_power == Decimal("1009.84")
        assert cash.settled_cash == Decimal("1009.84")
        realized, _ = uow.strategy_sleeves.realized_totals("mr")
        assert realized == Decimal("9.84")
        account = next(a for a in report.applied if a.book is CorporateActionBook.ACCOUNT)
        assert account.cash_delta == Decimal("9.84")
        assert account.quantity_before == account.quantity_after == Decimal("41")

    def test_live_mode_leaves_the_account_book_to_the_broker(self, uow, real, service) -> None:
        real.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        _seed_account(uow, symbol="NVDA", qty="3", avg_cost="1207.185", cash="100")
        _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )

        report = service.apply_due_actions(
            uow, as_of=_EX_DATE, timestamp=_TICK, adjust_account_book=False, run_id=_RUN
        )

        assert {a.book for a in report.applied} == {CorporateActionBook.SLEEVE}
        latest = uow.position_snapshots.get_latest()
        assert latest is not None and latest.positions[0].quantity == Decimal("3")

    def test_position_changed_on_or_after_ex_date_is_skipped_and_flagged(
        self, uow, real, service
    ) -> None:
        # Bought on the ex-date morning: the quantity is already post-split.
        real.apply_fill(uow, fill=_fill("f1", "NVDA", "28", "120.48", at=_TICK), strategy_id="mom")
        _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )

        report = service.apply_due_actions(
            uow,
            as_of=_EX_DATE,
            timestamp=_TICK + timedelta(minutes=5),
            adjust_account_book=False,
        )

        assert report.applied_count == 0
        assert [s.reason for s in report.skipped] == ["position_changed_on_or_after_ex_date"]
        assert report.pending_symbols == {"NVDA"}
        assert real.positions(uow, "mom")["NVDA"].quantity == Decimal("28")

    def test_actions_after_as_of_or_outside_the_lookback_are_ignored(
        self, uow, real, service
    ) -> None:
        real.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        _store_action(
            uow,
            action_id="future",
            action_type=CorporateActionType.SPLIT_FORWARD,
            ratio="10",
            effective_date=_EX_DATE + timedelta(days=1),
        )
        _store_action(
            uow,
            action_id="ancient",
            action_type=CorporateActionType.SPLIT_FORWARD,
            ratio="4",
            effective_date=_EX_DATE - timedelta(days=90),
        )

        report = service.apply_due_actions(
            uow, as_of=_EX_DATE, timestamp=_TICK, adjust_account_book=False
        )

        assert report.applied_count == 0
        assert report.skipped == []
        assert real.positions(uow, "macd")["NVDA"].quantity == Decimal("3")

    def test_non_applied_types_are_reported_for_manual_review(self, uow, real, service) -> None:
        real.apply_fill(uow, fill=_fill("f1", "PXD", "5", "250"), strategy_id="macd")
        _store_action(
            uow,
            action_id="m1",
            action_type=CorporateActionType.MERGER_STOCK,
            symbol="PXD",
            effective_date=_EX_DATE,
        )

        report = service.apply_due_actions(
            uow, as_of=_EX_DATE, timestamp=_TICK, adjust_account_book=False
        )

        assert report.applied_count == 0
        assert report.manual_review_action_ids == ["m1"]
        assert real.positions(uow, "macd")["PXD"].quantity == Decimal("5")

    def test_symbols_not_held_anywhere_are_not_queried(self, uow, service) -> None:
        _store_action(
            uow, action_id="s1", action_type=CorporateActionType.SPLIT_FORWARD, ratio="10"
        )
        report = service.apply_due_actions(
            uow, as_of=_EX_DATE, timestamp=_TICK, adjust_account_book=True
        )
        assert report.applied_count == 0 and report.pending_symbols == set()


class TestAdoptionGuard:
    def test_reconcile_skips_adoption_for_pending_symbols(self, uow, real) -> None:
        real.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        from autonomous_trading_platform.contracts.accounting.position_snapshot import Position

        broker = {
            "NVDA": Position(symbol="NVDA", quantity=Decimal("30"), avg_cost=Decimal("120.7185"))
        }

        report = real.reconcile(
            uow,
            account_positions=broker,
            timestamp=_TICK,
            adopt_unowned=True,
            skip_adoption_symbols={"NVDA"},
        )

        assert report.adopted_symbols == []
        assert [m.symbol for m in report.mismatches] == ["NVDA"]
        assert "__unattributed__" not in real.all_positions(uow)
