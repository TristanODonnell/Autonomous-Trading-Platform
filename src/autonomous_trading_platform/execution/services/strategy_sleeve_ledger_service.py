"""
Strategy sleeve ledger — per-strategy positions and P&L inside the shared account.

Each strategy's orders are sized against its own sleeve, and every broker order
belongs to exactly one strategy, so fills attribute via the order's strategy_id.
When two strategies trade the same symbol in opposite directions in one cycle,
the overlap is moved between sleeves with an internal cross instead of two
broker orders.

Average-cost and realized-P&L math is delegated to PositionLedgerService so the
sleeve books and the account book use identical accounting.

The same service runs the shadow book (on-deck strategies, simulated fills) over
separate tables. Crossing, adoption and reconciliation are real-book only: shadow
sleeves have no account to reconcile against.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from autonomous_trading_platform.accounting.corporate_actions import (
    PositionAdjustment,
    apply_action,
)
from autonomous_trading_platform.contracts.accounting.position_snapshot import Position
from autonomous_trading_platform.contracts.accounting.strategy_sleeve import (
    UNATTRIBUTED_SLEEVE_ID,
    SleeveBook,
    SleeveEntrySource,
    SleeveLedgerEntry,
    SleeveMismatch,
    SleevePosition,
    SleeveReconciliationReport,
    SleeveSnapshot,
)
from autonomous_trading_platform.contracts.common.enums import CorporateActionType, Side
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.services.position_ledger_service import (
    PositionLedgerResult,
    PositionLedgerService,
)
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    ShadowSleeveSnapshotRow,
    SleeveLedgerBase,
    SleevePositionBase,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_sleeve_repository import (
    StrategySleeveRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

ZERO = Decimal("0")
# Placeholder identifiers for synthetic fills used only to drive the ledger math.
_SYNTHETIC_UUID = UUID(int=0)


class SleeveAccountingError(ValueError):
    """A sleeve event could not be applied without breaking the long-only book."""


class StrategySleeveLedgerService:
    def __init__(
        self,
        position_ledger_service: PositionLedgerService | None = None,
        *,
        book: SleeveBook = SleeveBook.REAL,
    ) -> None:
        self._ledger = position_ledger_service or PositionLedgerService()
        self.book = book

    def _repo(self, uow: SorUnitOfWork) -> StrategySleeveRepository:
        return uow.shadow_sleeves if self.book is SleeveBook.SHADOW else uow.strategy_sleeves

    def _require_real(self, operation: str) -> None:
        if self.book is not SleeveBook.REAL:
            raise SleeveAccountingError(f"{operation} is only valid for the real sleeve book")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def apply_fill(
        self,
        uow: SorUnitOfWork,
        *,
        fill: Fill,
        strategy_id: str,
    ) -> SleeveLedgerEntry | None:
        """Apply a fill to the owning strategy's sleeve.

        Real book: a broker fill. Shadow book: a simulated fill for an on-deck order.
        Returns None when this fill was already applied (idempotent replay).
        Raises SleeveAccountingError when the fill sells more than the sleeve holds.
        """
        entry_id = _entry_id("fill", fill.fill_id, strategy_id)
        repo = self._repo(uow)
        if repo.has_entry(entry_id):
            return None

        result = self._compute(uow, strategy_id=strategy_id, fill=fill)
        self._persist_position(
            uow, strategy_id=strategy_id, symbol=fill.symbol, result=result, at=fill.timestamp
        )
        entry = SleeveLedgerEntry(
            entry_id=entry_id,
            strategy_id=strategy_id,
            symbol=fill.symbol,
            side=fill.side,
            quantity=Decimal(fill.quantity),
            price=Decimal(fill.price),
            fees=Decimal(fill.fees) if fill.fees is not None else ZERO,
            realized_pnl=result.realized_pnl,
            source=(
                SleeveEntrySource.SHADOW_FILL
                if self.book is SleeveBook.SHADOW
                else SleeveEntrySource.BROKER_FILL
            ),
            fill_id=fill.fill_id,
            intent_id=fill.intent_id,
            run_id=fill.run_id,
            timestamp=fill.timestamp,
        )
        repo.insert_entry(_entry_row(repo, entry))
        return entry

    def apply_internal_cross(
        self,
        uow: SorUnitOfWork,
        *,
        cross_id: str,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        buyer_strategy_id: str,
        seller_strategy_id: str,
        timestamp: datetime,
        run_id: UUID | None = None,
    ) -> tuple[SleeveLedgerEntry, SleeveLedgerEntry] | None:
        """Move quantity from the seller's sleeve to the buyer's at price.

        Both legs are validated before either is written. Returns None when the
        cross was already applied.
        """
        self._require_real("internal cross")
        if buyer_strategy_id == seller_strategy_id:
            raise SleeveAccountingError("internal cross requires two different strategies")

        sell_id = _entry_id("cross", cross_id, seller_strategy_id)
        buy_id = _entry_id("cross", cross_id, buyer_strategy_id)
        if uow.strategy_sleeves.has_entry(sell_id) and uow.strategy_sleeves.has_entry(buy_id):
            return None

        sell_fill = _synthetic_fill(symbol, Side.SELL, quantity, price, timestamp)
        buy_fill = _synthetic_fill(symbol, Side.BUY, quantity, price, timestamp)
        sell_result = self._compute(uow, strategy_id=seller_strategy_id, fill=sell_fill)
        buy_result = self._compute(uow, strategy_id=buyer_strategy_id, fill=buy_fill)

        self._persist_position(
            uow, strategy_id=seller_strategy_id, symbol=symbol, result=sell_result, at=timestamp
        )
        self._persist_position(
            uow, strategy_id=buyer_strategy_id, symbol=symbol, result=buy_result, at=timestamp
        )

        legs = []
        for entry_id, strategy_id, side, result in (
            (sell_id, seller_strategy_id, Side.SELL, sell_result),
            (buy_id, buyer_strategy_id, Side.BUY, buy_result),
        ):
            entry = SleeveLedgerEntry(
                entry_id=entry_id,
                strategy_id=strategy_id,
                symbol=symbol,
                side=side,
                quantity=Decimal(quantity),
                price=Decimal(price),
                fees=ZERO,
                realized_pnl=result.realized_pnl,
                source=SleeveEntrySource.INTERNAL_CROSS,
                cross_id=cross_id,
                run_id=run_id,
                timestamp=timestamp,
            )
            uow.strategy_sleeves.insert_entry(_entry_row(uow.strategy_sleeves, entry))
            legs.append(entry)
        return legs[0], legs[1]

    def adopt(
        self,
        uow: SorUnitOfWork,
        *,
        strategy_id: str,
        symbol: str,
        quantity: Decimal,
        avg_cost: Decimal,
        timestamp: datetime,
    ) -> SleeveLedgerEntry:
        """Assign account holdings to a sleeve without a fill (cutover / reconciliation)."""
        self._require_real("adoption")
        fill = _synthetic_fill(symbol, Side.BUY, quantity, avg_cost, timestamp)
        result = self._compute(uow, strategy_id=strategy_id, fill=fill)
        self._persist_position(
            uow, strategy_id=strategy_id, symbol=symbol, result=result, at=timestamp
        )
        entry = SleeveLedgerEntry(
            entry_id=_entry_id("adopt", f"{symbol}:{timestamp.isoformat()}", strategy_id),
            strategy_id=strategy_id,
            symbol=symbol,
            side=Side.BUY,
            quantity=Decimal(quantity),
            price=Decimal(avg_cost),
            fees=ZERO,
            realized_pnl=ZERO,
            source=SleeveEntrySource.ADOPTION,
            timestamp=timestamp,
        )
        uow.strategy_sleeves.insert_entry(_entry_row(uow.strategy_sleeves, entry))
        return entry

    def apply_corporate_action(
        self,
        uow: SorUnitOfWork,
        *,
        action: CorporateAction,
        strategy_id: str,
        timestamp: datetime,
        cash_in_lieu_price: Decimal | None = None,
        run_id: UUID | None = None,
    ) -> tuple[SleeveLedgerEntry, PositionAdjustment] | None:
        """Apply a split or cash dividend to one sleeve through the shared rule.

        Split: the position's quantity and average cost are rewritten (no P&L) and
        a ledger entry records the share change; a fractional remainder is paid in
        cash and its realized difference booked. Dividend: the position is untouched
        and a ledger entry books the cash as realized income, so the sleeve's P&L
        includes it. Both books. Returns None when the sleeve holds no position in
        the symbol or the action was already applied to this sleeve (idempotent).
        """
        repo = self._repo(uow)
        entry_id = _entry_id("corporate_action", action.action_id, strategy_id)
        if repo.has_entry(entry_id):
            return None
        row = repo.get_position(strategy_id, action.symbol)
        if row is None or Decimal(row.quantity) <= ZERO:
            return None

        adjustment = apply_action(
            action,
            quantity=Decimal(row.quantity),
            avg_cost=Decimal(row.avg_cost),
            cash_in_lieu_price=cash_in_lieu_price,
        )

        if adjustment.position_changed:
            if adjustment.quantity_after <= ZERO:
                repo.delete_position(strategy_id, action.symbol)
            else:
                repo.save_position(
                    repo.position_model(
                        strategy_id=strategy_id,
                        symbol=action.symbol,
                        quantity=adjustment.quantity_after,
                        avg_cost=adjustment.avg_cost_after,
                        updated_at=timestamp,
                    )
                )

        if action.action_type is CorporateActionType.CASH_DIVIDEND:
            # Income on the shares held: quantity held × rate, no position change.
            side, quantity, price = (
                Side.SELL,
                adjustment.quantity_before,
                Decimal(str(action.cash_amount)),
            )
        else:
            delta = adjustment.quantity_delta
            side = Side.BUY if delta >= ZERO else Side.SELL
            quantity, price = abs(delta), ZERO

        entry = SleeveLedgerEntry(
            entry_id=entry_id,
            strategy_id=strategy_id,
            symbol=action.symbol,
            side=side,
            quantity=quantity,
            price=price,
            fees=ZERO,
            realized_pnl=adjustment.realized_pnl,
            source=SleeveEntrySource.CORPORATE_ACTION,
            run_id=run_id,
            timestamp=timestamp,
        )
        repo.insert_entry(_entry_row(repo, entry))
        return entry, adjustment

    def liquidate(
        self,
        uow: SorUnitOfWork,
        *,
        strategy_id: str,
        prices: Mapping[str, Decimal | float],
        timestamp: datetime,
        run_id: UUID | None = None,
    ) -> list[SleeveLedgerEntry]:
        """Close every shadow position at the mark (avg cost when unpriced).

        Used when a strategy leaves on-deck, so a later return starts flat rather
        than with stale positions. Shadow book only: real positions leave through
        broker orders (wind-down).
        """
        if self.book is not SleeveBook.SHADOW:
            raise SleeveAccountingError("liquidate is only valid for the shadow sleeve book")
        repo = self._repo(uow)
        entries: list[SleeveLedgerEntry] = []
        for row in repo.get_positions(strategy_id):
            symbol = row.symbol
            quantity = Decimal(row.quantity)
            price = prices.get(symbol)
            mark = Decimal(str(price)) if price is not None else Decimal(row.avg_cost)
            fill = _synthetic_fill(symbol, Side.SELL, quantity, mark, timestamp)
            result = self._compute(uow, strategy_id=strategy_id, fill=fill)
            self._persist_position(
                uow, strategy_id=strategy_id, symbol=symbol, result=result, at=timestamp
            )
            entry = SleeveLedgerEntry(
                entry_id=_entry_id("tier_exit", f"{symbol}:{timestamp.isoformat()}", strategy_id),
                strategy_id=strategy_id,
                symbol=symbol,
                side=Side.SELL,
                quantity=quantity,
                price=mark,
                fees=ZERO,
                realized_pnl=result.realized_pnl,
                source=SleeveEntrySource.TIER_EXIT,
                run_id=run_id,
                timestamp=timestamp,
            )
            repo.insert_entry(_entry_row(repo, entry))
            entries.append(entry)
        return entries

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def positions(self, uow: SorUnitOfWork, strategy_id: str) -> dict[str, SleevePosition]:
        return {
            row.symbol: _position_contract(row)
            for row in self._repo(uow).get_positions(strategy_id)
        }

    def all_positions(self, uow: SorUnitOfWork) -> dict[str, dict[str, SleevePosition]]:
        by_strategy: dict[str, dict[str, SleevePosition]] = defaultdict(dict)
        for row in self._repo(uow).get_all_positions():
            by_strategy[row.strategy_id][row.symbol] = _position_contract(row)
        return dict(by_strategy)

    def aggregate_quantities(self, uow: SorUnitOfWork) -> dict[str, Decimal]:
        """Sleeve quantities summed across all strategies, per symbol."""
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for row in self._repo(uow).get_all_positions():
            totals[row.symbol] += Decimal(row.quantity)
        return dict(totals)

    # ------------------------------------------------------------------
    # Valuation and reconciliation
    # ------------------------------------------------------------------

    def snapshot(
        self,
        uow: SorUnitOfWork,
        *,
        strategy_id: str,
        prices: Mapping[str, Decimal | float],
        timestamp: datetime,
        run_id: UUID | None = None,
        allocated_capital: Decimal | None = None,
        blocked_order_count: int = 0,
    ) -> SleeveSnapshot:
        """Value a sleeve at the given prices and persist the snapshot.

        Symbols without a price are valued at cost and listed in unpriced_symbols.
        blocked_order_count is recorded on shadow snapshots only.
        """
        repo = self._repo(uow)
        market_value = ZERO
        cost_basis = ZERO
        unpriced: list[str] = []
        positions = repo.get_positions(strategy_id)
        for row in positions:
            quantity = Decimal(row.quantity)
            avg_cost = Decimal(row.avg_cost)
            cost_basis += quantity * avg_cost
            price = prices.get(row.symbol)
            if price is None:
                unpriced.append(row.symbol)
                market_value += quantity * avg_cost
            else:
                market_value += quantity * Decimal(str(price))

        realized, fees = repo.realized_totals(strategy_id)
        unrealized = market_value - cost_basis
        snapshot = SleeveSnapshot(
            snapshot_id=uuid4(),
            strategy_id=strategy_id,
            run_id=run_id,
            timestamp=timestamp,
            allocated_capital=allocated_capital,
            market_value=market_value,
            cost_basis=cost_basis,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            fees=fees,
            net_pnl=realized + unrealized - fees,
            position_count=len(positions),
            unpriced_symbols=unpriced,
            blocked_order_count=blocked_order_count if self.book is SleeveBook.SHADOW else 0,
        )
        shadow_fields = (
            {"blocked_order_count": snapshot.blocked_order_count}
            if repo.snapshot_model is ShadowSleeveSnapshotRow
            else {}
        )
        repo.insert_snapshot(
            repo.snapshot_model(
                snapshot_id=snapshot.snapshot_id,
                strategy_id=strategy_id,
                run_id=run_id,
                timestamp=timestamp,
                allocated_capital=allocated_capital,
                market_value=snapshot.market_value,
                cost_basis=snapshot.cost_basis,
                realized_pnl=snapshot.realized_pnl,
                unrealized_pnl=snapshot.unrealized_pnl,
                fees=snapshot.fees,
                net_pnl=snapshot.net_pnl,
                position_count=snapshot.position_count,
                unpriced_symbols=unpriced or None,
                **shadow_fields,
            )
        )
        return snapshot

    def reconcile(
        self,
        uow: SorUnitOfWork,
        *,
        account_positions: Mapping[str, Decimal | Position],
        timestamp: datetime,
        adopt_unowned: bool = False,
        skip_adoption_symbols: Collection[str] = (),
    ) -> SleeveReconciliationReport:
        """Compare summed sleeve quantities with the broker account.

        With adopt_unowned, account shares no sleeve owns are assigned to the
        unattributed sleeve at the account's average cost. Over-claims (sleeves
        holding more than the account) are always reported, never auto-fixed.
        Symbols in ``skip_adoption_symbols`` (a corporate action is pending on them)
        are reported as mismatches instead of adopted.
        """
        self._require_real("reconciliation")
        sleeve_totals = self.aggregate_quantities(uow)
        mismatches: list[SleeveMismatch] = []
        adopted: list[str] = []
        skip = {s.upper() for s in skip_adoption_symbols}

        for symbol in sorted(set(sleeve_totals) | set(account_positions)):
            raw = account_positions.get(symbol, ZERO)
            account_qty = Decimal(raw.quantity) if isinstance(raw, Position) else Decimal(raw)
            sleeve_qty = sleeve_totals.get(symbol, ZERO)
            unowned = account_qty - sleeve_qty
            if unowned == ZERO:
                continue

            avg_cost = raw.avg_cost if isinstance(raw, Position) else None
            if (
                adopt_unowned
                and symbol.upper() not in skip
                and unowned > ZERO
                and avg_cost is not None
                and avg_cost > ZERO
            ):
                self.adopt(
                    uow,
                    strategy_id=UNATTRIBUTED_SLEEVE_ID,
                    symbol=symbol,
                    quantity=unowned,
                    avg_cost=Decimal(avg_cost),
                    timestamp=timestamp,
                )
                adopted.append(symbol)
                continue

            mismatches.append(
                SleeveMismatch(
                    symbol=symbol, account_quantity=account_qty, sleeve_quantity=sleeve_qty
                )
            )

        return SleeveReconciliationReport(
            timestamp=timestamp, mismatches=mismatches, adopted_symbols=adopted
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute(self, uow: SorUnitOfWork, *, strategy_id: str, fill: Fill) -> PositionLedgerResult:
        row = self._repo(uow).get_position(strategy_id, fill.symbol)
        existing = (
            Position(symbol=row.symbol, quantity=row.quantity, avg_cost=row.avg_cost)
            if row is not None
            else None
        )
        held = Decimal(row.quantity) if row is not None else ZERO
        if fill.side == Side.SELL and Decimal(fill.quantity) > held:
            raise SleeveAccountingError(
                f"sleeve {strategy_id!r} holds {held} {fill.symbol}; cannot sell {fill.quantity}"
            )
        return self._ledger.apply_fill(existing, fill)

    def _persist_position(
        self,
        uow: SorUnitOfWork,
        *,
        strategy_id: str,
        symbol: str,
        result: PositionLedgerResult,
        at: datetime,
    ) -> None:
        repo = self._repo(uow)
        updated = result.updated_position
        if updated is None or Decimal(updated.quantity) == ZERO:
            repo.delete_position(strategy_id, symbol)
            return
        assert updated.avg_cost is not None
        repo.save_position(
            repo.position_model(
                strategy_id=strategy_id,
                symbol=symbol,
                quantity=Decimal(updated.quantity),
                avg_cost=Decimal(updated.avg_cost),
                updated_at=at,
            )
        )


def _entry_id(kind: str, source_id: str, strategy_id: str) -> str:
    return uuid5(NAMESPACE_URL, f"sleeve:{kind}:{source_id}:{strategy_id}").hex


def _synthetic_fill(
    symbol: str, side: Side, quantity: Decimal, price: Decimal, timestamp: datetime
) -> Fill:
    return Fill(
        fill_id="synthetic",
        broker_order_id="synthetic",
        intent_id=_SYNTHETIC_UUID,
        run_id=_SYNTHETIC_UUID,
        timestamp=timestamp,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
    )


def _position_contract(row: SleevePositionBase) -> SleevePosition:
    return SleevePosition(
        strategy_id=row.strategy_id,
        symbol=row.symbol,
        quantity=Decimal(row.quantity),
        avg_cost=Decimal(row.avg_cost),
        updated_at=row.updated_at,
    )


def _entry_row(repo: StrategySleeveRepository, entry: SleeveLedgerEntry) -> SleeveLedgerBase:
    return repo.ledger_model(
        entry_id=entry.entry_id,
        strategy_id=entry.strategy_id,
        symbol=entry.symbol,
        side=entry.side.value,
        quantity=entry.quantity,
        price=entry.price,
        fees=entry.fees,
        realized_pnl=entry.realized_pnl,
        source=entry.source.value,
        fill_id=entry.fill_id,
        cross_id=entry.cross_id,
        intent_id=entry.intent_id,
        run_id=entry.run_id,
        timestamp=entry.timestamp,
    )
