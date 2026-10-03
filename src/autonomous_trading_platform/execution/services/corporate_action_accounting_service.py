"""Apply stored corporate actions to every book the platform keeps.

Runs at the top of the first trading cycle on or after an ex-date (and at the start
of each backtest day), before unowned shares are adopted, so the books already match
the broker's post-split account and nothing is adopted or sold by mistake
(plan 5d, decisions D3/D4).

Books:
- real strategy sleeves and on-deck shadow sleeves — always;
- the account book (position + cash snapshots) — only in backtests
  (``adjust_account_book=True``); in live/paper the broker already applied it.

Every (action, book, scope) application is recorded in ``corporate_action_applications``
so a restart or a replayed tick is a no-op. A position whose last change is on or
after the ex-date is left alone and reported: its quantity may already be post-split
(bought after the split, or the broker applied it before we did), and guessing would
corrupt the book.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from autonomous_trading_platform.accounting.corporate_actions import (
    PositionAdjustment,
    apply_action,
    is_applicable,
)
from autonomous_trading_platform.contracts.accounting.corporate_action_application import (
    ACCOUNT_SCOPE,
    CorporateActionApplication,
    CorporateActionBook,
)
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import SleeveBook
from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.market.corporate_action import (
    CorporateAction as CorporateActionContract,
)
from autonomous_trading_platform.execution.services.strategy_sleeve_ledger_service import (
    StrategySleeveLedgerService,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.cash_snapshots import (
    CashSnapshot as OrmCashSnapshot,
)
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem as OrmPositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_application_repository import (
    application_id_for,
)
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_repository import (
    CorporateActionRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

ZERO = Decimal("0")
_SNAPSHOT_NS = uuid.UUID("c0a1b2c3-d4e5-4f60-8a9b-0c1d2e3f4a5b")

# How far back to look for actions that may still be unapplied (a run missed for a
# few days must still catch its split). Older unapplied actions are ignored.
DEFAULT_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class SkippedApplication:
    action_id: str
    symbol: str
    book: CorporateActionBook
    scope: str
    reason: str


@dataclass
class CorporateActionApplicationReport:
    as_of: date
    applied: list[CorporateActionApplication] = field(default_factory=list)
    skipped: list[SkippedApplication] = field(default_factory=list)
    # Symbols with an applicable action due on the as-of date or the day before, or
    # with a skipped application: the cycle must not adopt unowned shares in them.
    pending_symbols: set[str] = field(default_factory=set)
    # Actions in the window that are stored but not applied automatically.
    manual_review_action_ids: list[str] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return len(self.applied)

    def summary(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "applied": self.applied_count,
            "skipped": len(self.skipped),
            "pending_symbols": sorted(self.pending_symbols),
            "manual_review_action_ids": list(self.manual_review_action_ids),
            "applications": [
                {
                    "action_id": a.action_id,
                    "book": a.book.value,
                    "scope": a.scope,
                    "symbol": a.symbol,
                    "type": a.action_type.value,
                    "quantity_before": str(a.quantity_before),
                    "quantity_after": str(a.quantity_after),
                    "cash_delta": str(a.cash_delta),
                }
                for a in self.applied
            ],
            "skips": [
                {
                    "action_id": s.action_id,
                    "book": s.book.value,
                    "scope": s.scope,
                    "symbol": s.symbol,
                    "reason": s.reason,
                }
                for s in self.skipped
            ],
        }


class CorporateActionAccountingService:
    def __init__(
        self,
        *,
        real_ledger: StrategySleeveLedgerService | None = None,
        shadow_ledger: StrategySleeveLedgerService | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self._real = real_ledger or StrategySleeveLedgerService()
        self._shadow = shadow_ledger or StrategySleeveLedgerService(book=SleeveBook.SHADOW)
        self._lookback_days = lookback_days

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def apply_due_actions(
        self,
        uow: SorUnitOfWork,
        *,
        as_of: date,
        timestamp: datetime,
        prices: Mapping[str, Decimal | float] | None = None,
        price_provider: Callable[[list[str]], Mapping[str, Decimal | float]] | None = None,
        adjust_account_book: bool,
        run_id: UUID | None = None,
        symbols: Collection[str] | None = None,
    ) -> CorporateActionApplicationReport:
        """Apply every applicable action with ``effective_date <= as_of`` (within the
        lookback window) that has not been applied to a book holding the symbol.

        ``prices`` (the ex-date prices) pay fractional shares after a reverse split;
        without a price the remainder is paid at cost. ``price_provider`` is called
        once, only for the symbols with a due action, when ``prices`` has none for
        them. ``symbols`` limits the actions considered; by default every symbol held
        in any book is considered.
        """
        report = CorporateActionApplicationReport(as_of=as_of)
        price_map = {k.upper(): Decimal(str(v)) for k, v in (prices or {}).items()}

        held = self._held_symbols(uow, include_account=adjust_account_book)
        candidate_symbols = {s.upper() for s in symbols} if symbols is not None else set(held)
        if not candidate_symbols:
            return report

        window_start = as_of - timedelta(days=self._lookback_days)
        actions = uow.corporate_actions.get_actions_for_symbols_between(
            symbols=sorted(candidate_symbols), start_date=window_start, end_date=as_of
        )
        contracts = [CorporateActionRepository.to_contract(row) for row in actions]

        if price_provider is not None:
            missing = sorted(
                {a.symbol for a in contracts if is_applicable(a) and a.symbol not in price_map}
            )
            if missing:
                try:
                    fetched = price_provider(missing)
                except Exception as exc:
                    logger.warning(
                        "corporate_actions.price_fetch_failed",
                        extra={"symbols": missing, "error": str(exc)},
                    )
                    fetched = {}
                price_map.update(
                    {k.upper(): Decimal(str(v)) for k, v in fetched.items() if v is not None}
                )

        for action in sorted(contracts, key=lambda a: (a.effective_date, a.symbol, a.action_id)):
            if not is_applicable(action):
                report.manual_review_action_ids.append(action.action_id)
                continue
            if action.effective_date >= as_of - timedelta(days=1):
                report.pending_symbols.add(action.symbol)

            ex_cutoff = datetime.combine(action.effective_date, datetime.min.time(), tzinfo=UTC)
            price = price_map.get(action.symbol)

            self._apply_to_sleeves(
                uow,
                action=action,
                ledger=self._real,
                book=CorporateActionBook.SLEEVE,
                ex_cutoff=ex_cutoff,
                timestamp=timestamp,
                price=price,
                run_id=run_id,
                report=report,
            )
            self._apply_to_sleeves(
                uow,
                action=action,
                ledger=self._shadow,
                book=CorporateActionBook.SHADOW_SLEEVE,
                ex_cutoff=ex_cutoff,
                timestamp=timestamp,
                price=price,
                run_id=run_id,
                report=report,
            )
            if adjust_account_book:
                self._apply_to_account(
                    uow,
                    action=action,
                    ex_cutoff=ex_cutoff,
                    timestamp=timestamp,
                    price=price,
                    run_id=run_id,
                    report=report,
                )

        if report.applied or report.skipped:
            logger.info(
                "corporate_actions.applied",
                extra={
                    "as_of": as_of.isoformat(),
                    "applied": report.applied_count,
                    "skipped": len(report.skipped),
                    "pending_symbols": sorted(report.pending_symbols),
                },
            )
        return report

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------

    def _held_symbols(self, uow: SorUnitOfWork, *, include_account: bool) -> set[str]:
        symbols: set[str] = set()
        for repo in (uow.strategy_sleeves, uow.shadow_sleeves):
            symbols.update(row.symbol.upper() for row in repo.get_all_positions())
        if include_account:
            latest = uow.position_snapshots.get_latest()
            if latest is not None:
                symbols.update(
                    item.symbol.upper()
                    for item in (latest.positions or [])
                    if item.quantity is not None and Decimal(item.quantity) > ZERO
                )
        return symbols

    def _apply_to_sleeves(
        self,
        uow: SorUnitOfWork,
        *,
        action: CorporateActionContract,
        ledger: StrategySleeveLedgerService,
        book: CorporateActionBook,
        ex_cutoff: datetime,
        timestamp: datetime,
        price: Decimal | None,
        run_id: UUID | None,
        report: CorporateActionApplicationReport,
    ) -> None:
        repo = (
            uow.shadow_sleeves
            if book is CorporateActionBook.SHADOW_SLEEVE
            else uow.strategy_sleeves
        )
        rows = [row for row in repo.get_all_positions() if row.symbol.upper() == action.symbol]
        if not rows:
            return
        applied_ids = uow.corporate_action_applications
        for row in rows:
            scope = row.strategy_id
            if applied_ids.is_applied(action_id=action.action_id, book=book, scope=scope):
                continue
            if Decimal(row.quantity) <= ZERO:
                continue
            if _as_utc(row.updated_at) >= ex_cutoff:
                self._skip(
                    report,
                    action,
                    book,
                    scope,
                    "position_changed_on_or_after_ex_date",
                )
                continue
            outcome = ledger.apply_corporate_action(
                uow,
                action=action,
                strategy_id=scope,
                timestamp=timestamp,
                cash_in_lieu_price=price,
                run_id=run_id,
            )
            if outcome is None:
                continue
            _entry, adjustment = outcome
            self._record(uow, action, book, scope, adjustment, timestamp, run_id, report)

    def _apply_to_account(
        self,
        uow: SorUnitOfWork,
        *,
        action: CorporateActionContract,
        ex_cutoff: datetime,
        timestamp: datetime,
        price: Decimal | None,
        run_id: UUID | None,
        report: CorporateActionApplicationReport,
    ) -> None:
        book = CorporateActionBook.ACCOUNT
        if uow.corporate_action_applications.is_applied(
            action_id=action.action_id, book=book, scope=ACCOUNT_SCOPE
        ):
            return
        latest = uow.position_snapshots.get_latest()
        if latest is None:
            return
        items = list(latest.positions or [])
        held = [
            item
            for item in items
            if item.symbol.upper() == action.symbol
            and item.quantity is not None
            and Decimal(item.quantity) > ZERO
        ]
        if not held:
            return
        if _as_utc(latest.timestamp) >= ex_cutoff:
            self._skip(report, action, book, ACCOUNT_SCOPE, "position_changed_on_or_after_ex_date")
            return

        item = held[0]
        adjustment = apply_action(
            action,
            quantity=Decimal(item.quantity),
            avg_cost=Decimal(item.avg_cost) if item.avg_cost is not None else ZERO,
            cash_in_lieu_price=price,
        )
        snapshot_run_id = run_id or latest.run_id

        if adjustment.position_changed:
            mark = (
                price
                if price is not None
                else (
                    Decimal(item.market_price) / _ratio(adjustment)
                    if item.market_price is not None
                    else adjustment.avg_cost_after
                )
            )
            new_items = [
                OrmPositionSnapshotItem(
                    symbol=existing.symbol,
                    quantity=existing.quantity,
                    avg_cost=existing.avg_cost,
                    market_price=existing.market_price,
                    market_value=existing.market_value,
                    unrealized_pnl=existing.unrealized_pnl,
                )
                for existing in items
                if existing.symbol.upper() != action.symbol
            ]
            if adjustment.quantity_after > ZERO:
                market_value = adjustment.quantity_after * mark
                new_items.append(
                    OrmPositionSnapshotItem(
                        symbol=item.symbol,
                        quantity=adjustment.quantity_after,
                        avg_cost=adjustment.avg_cost_after,
                        market_price=mark,
                        market_value=market_value,
                        unrealized_pnl=market_value
                        - adjustment.quantity_after * adjustment.avg_cost_after,
                    )
                )
            header = uow.position_snapshots.get_or_create_header(
                snapshot_id=uuid.uuid5(
                    _SNAPSHOT_NS,
                    f"positions:{snapshot_run_id}:{timestamp.isoformat()}:{OrderSource.LEDGER.value}",
                ),
                run_id=snapshot_run_id,
                timestamp=timestamp,
                source=OrderSource.LEDGER,
            )
            header.positions = new_items
            items = new_items

        if adjustment.cash_delta != ZERO:
            self._credit_cash(
                uow,
                amount=adjustment.cash_delta,
                positions=items,
                timestamp=timestamp,
                run_id=snapshot_run_id,
            )

        self._record(uow, action, book, ACCOUNT_SCOPE, adjustment, timestamp, run_id, report)

    def _credit_cash(
        self,
        uow: SorUnitOfWork,
        *,
        amount: Decimal,
        positions: list[Any],
        timestamp: datetime,
        run_id: UUID,
    ) -> None:
        latest = uow.cash_snapshots.get_latest()
        cash = Decimal(latest.cash) if latest is not None else ZERO
        buying_power = Decimal(latest.buying_power) if latest is not None else ZERO
        reserved = Decimal(latest.reserved_cash) if latest is not None else ZERO
        settled = (
            Decimal(latest.settled_cash)
            if latest is not None and latest.settled_cash is not None
            else None
        )
        new_cash = cash + amount
        equity = new_cash + sum(
            Decimal(p.market_value) for p in positions if p.market_value is not None
        )
        uow.cash_snapshots.upsert(
            OrmCashSnapshot(
                snapshot_id=uuid.uuid5(
                    _SNAPSHOT_NS,
                    f"cash:{run_id}:{timestamp.isoformat()}:{OrderSource.LEDGER.value}",
                ),
                run_id=run_id,
                timestamp=timestamp,
                currency=latest.currency if latest is not None else "USD",
                cash=new_cash,
                buying_power=buying_power + amount,
                reserved_cash=reserved,
                equity=equity,
                source=OrderSource.LEDGER,
                capital_bucket=latest.capital_bucket if latest is not None else None,
                settled_cash=settled + amount if settled is not None else None,
                unsettled_cash=latest.unsettled_cash if latest is not None else None,
            )
        )

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def _record(
        self,
        uow: SorUnitOfWork,
        action: CorporateActionContract,
        book: CorporateActionBook,
        scope: str,
        adjustment: PositionAdjustment,
        timestamp: datetime,
        run_id: UUID | None,
        report: CorporateActionApplicationReport,
    ) -> None:
        application = CorporateActionApplication(
            application_id=application_id_for(action.action_id, book, scope),
            action_id=action.action_id,
            book=book,
            scope=scope,
            symbol=action.symbol,
            action_type=action.action_type,
            effective_date=action.effective_date,
            applied_at=timestamp,
            quantity_before=adjustment.quantity_before,
            quantity_after=adjustment.quantity_after,
            avg_cost_before=adjustment.avg_cost_before,
            avg_cost_after=adjustment.avg_cost_after,
            cash_delta=adjustment.cash_delta,
            realized_pnl=adjustment.realized_pnl,
            run_id=run_id,
            details={
                "fractional_shares": str(adjustment.fractional_shares),
                "split_ratio": str(action.split_ratio) if action.split_ratio is not None else None,
                "cash_amount": str(action.cash_amount) if action.cash_amount is not None else None,
            },
        )
        uow.corporate_action_applications.record(application)
        report.applied.append(application)
        logger.info(
            "corporate_action.applied",
            extra={
                "action_id": action.action_id,
                "symbol": action.symbol,
                "action_type": action.action_type.value,
                "book": book.value,
                "scope": scope,
                "quantity_before": str(adjustment.quantity_before),
                "quantity_after": str(adjustment.quantity_after),
                "cash_delta": str(adjustment.cash_delta),
            },
        )

    @staticmethod
    def _skip(
        report: CorporateActionApplicationReport,
        action: CorporateActionContract,
        book: CorporateActionBook,
        scope: str,
        reason: str,
    ) -> None:
        report.skipped.append(
            SkippedApplication(
                action_id=action.action_id,
                symbol=action.symbol,
                book=book,
                scope=scope,
                reason=reason,
            )
        )
        report.pending_symbols.add(action.symbol)
        logger.warning(
            "corporate_action.skipped",
            extra={
                "action_id": action.action_id,
                "symbol": action.symbol,
                "book": book.value,
                "scope": scope,
                "reason": reason,
            },
        )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ratio(adjustment: PositionAdjustment) -> Decimal:
    """Price factor implied by the adjustment (post ÷ pre quantity), 1 when unchanged."""
    if adjustment.avg_cost_before > ZERO and adjustment.avg_cost_after > ZERO:
        return adjustment.avg_cost_before / adjustment.avg_cost_after
    return Decimal("1")


def held_symbols_by_book(uow: SorUnitOfWork) -> dict[str, set[str]]:
    """Diagnostic helper: symbols held per book."""
    out: dict[str, set[str]] = defaultdict(set)
    for row in uow.strategy_sleeves.get_all_positions():
        out[CorporateActionBook.SLEEVE.value].add(row.symbol)
    for row in uow.shadow_sleeves.get_all_positions():
        out[CorporateActionBook.SHADOW_SLEEVE.value].add(row.symbol)
    return dict(out)
