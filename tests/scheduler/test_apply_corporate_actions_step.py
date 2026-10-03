"""The trading cycle applies due corporate actions before adopting unowned shares
(plan 5d-C): after a split the sleeves match the broker, nothing is adopted, nothing
is sold, net P&L is unchanged; reverse splits and dividends behave; a failed step
withholds adoption for the cycle."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.accounting.corporate_action_application import (
    CorporateActionBook,
)
from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.common.enums import (
    CorporateActionType,
    OrderSource,
    Side,
)
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.clients.simulated_broker_client import (
    SimulatedBrokerClient,
)
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    StrategySleeveLedgerService,
)
from autonomous_trading_platform.scheduler.jobs.apply_corporate_actions_step import (
    apply_due_corporate_actions,
)
from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
    _fetch_positions,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import (
    CashSnapshot as OrmCashSnapshot,
)
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem as OrmPositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

_RUN = UUID("00000000-0000-0000-0000-00000000aaaa")
_BEFORE = datetime(2024, 6, 7, 19, 50, tzinfo=UTC)
_EX_DATE = date(2024, 6, 10)
_TICK = datetime(2024, 6, 10, 13, 35, tzinfo=UTC)


class _FakeAlpaca:
    """A real broker that has already applied the split to the account."""

    def __init__(self, positions: list[dict[str, str]]) -> None:
        self._positions = positions
        self.price_calls: list[list[str]] = []

    def get_positions(self) -> list[dict[str, Any]]:
        return list(self._positions)

    def get_latest_trades(self, symbols: list[str]) -> dict[str, Any]:
        self.price_calls.append(list(symbols))
        return {s: {"p": 120.48} for s in symbols}


class _FakeSimulatedBroker(SimulatedBrokerClient):
    """isinstance-compatible stand-in: the backtest account lives in our snapshots."""

    def __init__(self) -> None:  # noqa: D107 - skip the real constructor
        self.price_calls: list[list[str]] = []

    def get_latest_trades(self, symbols: list[str]) -> dict[str, Any]:
        self.price_calls.append(list(symbols))
        return {s: {"p": 120.48} for s in symbols}


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _fill(fill_id: str, symbol: str, qty: str, price: str, *, at: datetime = _BEFORE) -> Fill:
    return Fill(
        fill_id=fill_id,
        broker_order_id=f"o-{fill_id}",
        intent_id=UUID("00000000-0000-0000-0000-00000000bbbb"),
        run_id=_RUN,
        timestamp=at,
        symbol=symbol,
        side=Side.BUY,
        quantity=Decimal(qty),
        price=Decimal(price),
    )


def _store(uow: SorUnitOfWork, action_id: str, action_type: CorporateActionType, **kw: Any) -> None:
    uow.corporate_actions.upsert(
        CorporateAction(
            action_id=action_id,
            symbol=kw.get("symbol", "NVDA"),
            action_type=action_type,
            effective_date=kw.get("effective_date", _EX_DATE),
            split_ratio=Decimal(kw["ratio"]) if "ratio" in kw else None,
            cash_amount=Decimal(kw["cash"]) if "cash" in kw else None,
            currency="USD",
            new_symbol="",
            source="alpaca",
            ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
    )
    uow.session.flush()


def _seed_account(uow: SorUnitOfWork, symbol: str, qty: str, avg_cost: str, cash: str) -> None:
    header = uow.position_snapshots.get_or_create_header(
        snapshot_id=uuid.uuid4(), run_id=_RUN, timestamp=_BEFORE, source=OrderSource.LEDGER
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
            timestamp=_BEFORE,
            currency="USD",
            cash=Decimal(cash),
            buying_power=Decimal(cash),
            reserved_cash=Decimal("0"),
            equity=Decimal(cash) + Decimal(qty) * Decimal(avg_cost),
            source=OrderSource.LEDGER,
        )
    )
    uow.session.flush()


@pytest.fixture
def uow(db_session: Session) -> SorUnitOfWork:
    return SorUnitOfWork(db_session)


@pytest.fixture
def ledger() -> StrategySleeveLedgerService:
    return StrategySleeveLedgerService()


def _sleeve_net_pnl(uow: SorUnitOfWork, ledger, strategy_id: str, price: Decimal) -> Decimal:
    realized, fees = uow.strategy_sleeves.realized_totals(strategy_id)
    unrealized = sum(
        (p.quantity * (price - p.avg_cost) for p in ledger.positions(uow, strategy_id).values()),
        Decimal("0"),
    )
    return realized + unrealized - fees


class TestLiveSplit:
    def test_split_overnight_sleeves_match_broker_and_nothing_is_adopted(
        self, db_session, uow, ledger
    ) -> None:
        # Friday: two sleeves hold 3 NVDA each at ~$1,207. Monday pre-market Alpaca applies
        # the 10:1 split: the account shows 60 shares at $120.7.
        ledger.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        ledger.apply_fill(uow, fill=_fill("f2", "NVDA", "3", "1206.4"), strategy_id="mom")
        _store(uow, "s1", CorporateActionType.SPLIT_FORWARD, ratio="10")
        pnl_before = _sleeve_net_pnl(uow, ledger, "macd", Decimal("1207.185"))
        broker = _FakeAlpaca(
            [
                {
                    "symbol": "NVDA",
                    "qty": "60",
                    "avg_entry_price": "120.67925",
                    "current_price": "120.48",
                    "market_value": "7228.8",
                    "unrealized_pl": "-11.955",
                }
            ]
        )
        audit = _Audit()

        outcome = apply_due_corporate_actions(
            session=db_session,
            now_utc=_TICK,
            broker_client=broker,
            run_id=_RUN,
            price_provider=lambda symbols: {
                s: float(t["p"]) for s, t in broker.get_latest_trades(symbols).items()
            },
            audit_logger=audit,
        )

        assert outcome.failed is False and outcome.adoption_allowed is True
        assert outcome.report is not None and outcome.report.applied_count == 2
        assert {a.book for a in outcome.report.applied} == {CorporateActionBook.SLEEVE}
        assert outcome.skip_adoption_symbols == {"NVDA"}
        assert ledger.positions(uow, "macd")["NVDA"].quantity == Decimal("30")
        assert ledger.positions(uow, "mom")["NVDA"].quantity == Decimal("30")
        assert ledger.aggregate_quantities(uow)["NVDA"] == Decimal("60")
        # no P&L from the split itself (marked at the equivalent post-split price)
        assert _sleeve_net_pnl(uow, ledger, "macd", Decimal("120.7185")) == pnl_before
        assert [e["event_type"] for e in audit.events] == ["CORPORATE_ACTIONS_APPLIED"]

        # The cycle's adoption step: broker 60 == sleeves 60 → nothing unowned.
        report = ledger.reconcile(
            uow,
            account_positions=_fetch_positions(broker),
            timestamp=_TICK,
            adopt_unowned=True,
            skip_adoption_symbols=outcome.skip_adoption_symbols,
        )
        assert report.adopted_symbols == [] and report.mismatches == []
        assert "__unattributed__" not in ledger.all_positions(uow)

        # Same cycle again (restart): nothing more to apply.
        again = apply_due_corporate_actions(
            session=db_session, now_utc=_TICK, broker_client=broker, run_id=_RUN
        )
        assert again.report is not None and again.report.applied_count == 0

    def test_without_the_step_the_old_path_would_have_adopted_and_sold(self, uow, ledger) -> None:
        """Documents the bug: broker 30 vs sleeve 3 → 27 adopted into the orphan sleeve."""
        ledger.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        broker = {
            "NVDA": Position(symbol="NVDA", quantity=Decimal("30"), avg_cost=Decimal("120.7"))
        }
        report = ledger.reconcile(
            uow, account_positions=broker, timestamp=_TICK, adopt_unowned=True
        )
        assert report.adopted_symbols == ["NVDA"]
        assert ledger.positions(uow, "__unattributed__")["NVDA"].quantity == Decimal("27")

    def test_broker_not_yet_adjusted_withholds_adoption_for_the_symbol(
        self, db_session, uow, ledger
    ) -> None:
        ledger.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        _store(uow, "s1", CorporateActionType.SPLIT_FORWARD, ratio="10")
        # Broker still reports the pre-split quantity (late processing).
        broker = _FakeAlpaca(
            [
                {
                    "symbol": "NVDA",
                    "qty": "3",
                    "avg_entry_price": "1207.185",
                    "current_price": "1204.8",
                    "market_value": "3614.4",
                    "unrealized_pl": "-7.155",
                }
            ]
        )
        outcome = apply_due_corporate_actions(
            session=db_session, now_utc=_TICK, broker_client=broker, run_id=_RUN
        )
        assert ledger.positions(uow, "macd")["NVDA"].quantity == Decimal("30")
        report = ledger.reconcile(
            uow,
            account_positions=_fetch_positions(broker),
            timestamp=_TICK,
            adopt_unowned=True,
            skip_adoption_symbols=outcome.skip_adoption_symbols,
        )
        # reported, never "fixed" by adoption
        assert report.adopted_symbols == []
        assert [(m.symbol, m.account_quantity, m.sleeve_quantity) for m in report.mismatches] == [
            ("NVDA", Decimal("3"), Decimal("30"))
        ]

    def test_reverse_split_in_live(self, db_session, uow, ledger) -> None:
        ledger.apply_fill(uow, fill=_fill("f1", "ATRA", "110", "2"), strategy_id="mr")
        _store(uow, "r1", CorporateActionType.SPLIT_REVERSE, symbol="ATRA", ratio="0.04")
        broker = _FakeAlpaca(
            [
                {
                    "symbol": "ATRA",
                    "qty": "4",
                    "avg_entry_price": "50",
                    "current_price": "55",
                    "market_value": "220",
                    "unrealized_pl": "20",
                }
            ]
        )
        outcome = apply_due_corporate_actions(
            session=db_session,
            now_utc=_TICK,
            broker_client=broker,
            run_id=_RUN,
            price_provider=lambda symbols: {s: 55.0 for s in symbols},
        )
        assert outcome.report is not None and outcome.report.applied_count == 1
        position = ledger.positions(uow, "mr")["ATRA"]
        assert position.quantity == Decimal("4") and position.avg_cost == Decimal("50")
        report = ledger.reconcile(
            uow,
            account_positions=_fetch_positions(broker),
            timestamp=_TICK,
            adopt_unowned=True,
            skip_adoption_symbols=outcome.skip_adoption_symbols,
        )
        assert report.mismatches == [] and report.adopted_symbols == []

    def test_dividend_in_live_books_sleeve_income_only(self, db_session, uow, ledger) -> None:
        ledger.apply_fill(uow, fill=_fill("f1", "AAPL", "41", "123.7"), strategy_id="mr")
        _store(uow, "d1", CorporateActionType.CASH_DIVIDEND, symbol="AAPL", cash="0.24")
        broker = _FakeAlpaca([])
        outcome = apply_due_corporate_actions(
            session=db_session, now_utc=_TICK, broker_client=broker, run_id=_RUN
        )
        assert outcome.report is not None and outcome.report.applied_count == 1
        realized, _ = uow.strategy_sleeves.realized_totals("mr")
        assert realized == Decimal("9.84")
        assert ledger.positions(uow, "mr")["AAPL"].quantity == Decimal("41")
        # dividends need no price
        assert broker.price_calls == []


class TestBacktestSplit:
    def test_simulated_broker_also_adjusts_the_account_book(self, db_session, uow, ledger) -> None:
        ledger.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        _seed_account(uow, "NVDA", "3", "1207.185", "50000")
        _store(uow, "s1", CorporateActionType.SPLIT_FORWARD, ratio="10")
        broker = _FakeSimulatedBroker()

        outcome = apply_due_corporate_actions(
            session=db_session,
            now_utc=_TICK,
            broker_client=broker,
            run_id=_RUN,
            price_provider=lambda symbols: {
                s: float(t["p"]) for s, t in broker.get_latest_trades(symbols).items()
            },
        )

        assert outcome.report is not None
        assert {a.book for a in outcome.report.applied} == {
            CorporateActionBook.SLEEVE,
            CorporateActionBook.ACCOUNT,
        }
        assert broker.price_calls == [["NVDA"]]
        latest = uow.position_snapshots.get_latest()
        assert latest is not None
        assert latest.positions[0].quantity == Decimal("30")
        assert latest.positions[0].avg_cost == Decimal("120.7185")
        assert latest.positions[0].market_price == Decimal("120.48")
        assert ledger.positions(uow, "macd")["NVDA"].quantity == Decimal("30")


class TestFailure:
    def test_failed_step_withholds_all_adoption_and_audits(self, db_session, uow, ledger) -> None:
        # Committed state, as in production: the step's rollback must not undo it.
        with SorUnitOfWork(db_session) as committed:
            ledger.apply_fill(
                committed, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd"
            )
            _store(committed, "s1", CorporateActionType.SPLIT_FORWARD, ratio="10")

        class _Broken:
            def apply_due_actions(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("db exploded")

        audit = _Audit()
        outcome = apply_due_corporate_actions(
            session=db_session,
            now_utc=_TICK,
            broker_client=_FakeAlpaca([]),
            run_id=_RUN,
            audit_logger=audit,
            service=_Broken(),  # type: ignore[arg-type]
        )

        assert outcome.failed is True
        assert outcome.adoption_allowed is False
        assert outcome.report is None
        assert [e["event_type"] for e in audit.events] == ["CORPORATE_ACTIONS_STEP_FAILED"]
        # sleeves untouched
        assert ledger.positions(uow, "macd")["NVDA"].quantity == Decimal("3")

    def test_nothing_due_is_quiet(self, db_session, uow, ledger) -> None:
        ledger.apply_fill(uow, fill=_fill("f1", "NVDA", "3", "1207.185"), strategy_id="macd")
        _store(
            uow,
            "later",
            CorporateActionType.SPLIT_FORWARD,
            ratio="10",
            effective_date=_EX_DATE + timedelta(days=3),
        )
        audit = _Audit()
        outcome = apply_due_corporate_actions(
            session=db_session,
            now_utc=_TICK,
            broker_client=_FakeAlpaca([]),
            run_id=_RUN,
            audit_logger=audit,
        )
        assert outcome.report is not None and outcome.report.applied_count == 0
        assert outcome.skip_adoption_symbols == set()
        assert audit.events == []
