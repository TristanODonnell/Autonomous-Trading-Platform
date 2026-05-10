from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.execution.errors import BrokerRuntimeSyncError
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper
from autonomous_trading_platform.storage.sor.models.broker_account_snapshots import (
    BrokerAccountSnapshot,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class BrokerRuntimeClient(Protocol):
    def get_account(self) -> dict[str, Any]: ...

    def get_positions(self) -> list[dict[str, Any]]: ...

    def list_open_orders(self) -> list[dict[str, Any]]: ...

    def get_order_by_id(self, order_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class BrokerRuntimeSyncResult:
    account_snapshot_id: UUID
    cash_snapshot_id: UUID | None
    broker_account_id: str
    observed_at: datetime
    position_snapshot_id: UUID | None = None
    synced_broker_order_ids: tuple[str, ...] = ()
    synced_fill_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BrokerRuntimeConsistencyResult:
    ok: bool
    mismatches: list[str] = field(default_factory=list)
    account_snapshot_id: UUID | None = None
    cash_snapshot_id: UUID | None = None


class BrokerRuntimeSyncService:
    """
    Orchestrates broker state synchronization into persisted runtime state.

    The current implementation intentionally prioritizes account and cash sync.
    Positions, open orders, order status, and fill reconciliation are left as
    narrow stubs until their repository/service integration points are clarified.
    """

    def __init__(
        self,
        *,
        session: Session,
        broker_client: BrokerRuntimeClient,
        settings: Settings,
    ) -> None:
        self.session = session
        self.broker_client = broker_client
        self.settings = settings
        self.broker_order_mapper = BrokerOrderMapper()

    def sync_account_snapshot(
        self,
        *,
        observed_at: datetime | None = None,
    ) -> BrokerAccountSnapshot:
        account = self._fetch_account()
        snapshot = self._map_account_snapshot(
            account=account,
            observed_at=observed_at or datetime.now(UTC),
        )

        with SorUnitOfWork(self.session) as uow:
            uow.broker_account_snapshots.upsert(snapshot)

        return snapshot

    def sync_cash_snapshot_from_account(
        self,
        *,
        run_id: UUID,
        account: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
        capital_bucket: Decimal | None = None,
    ) -> CashSnapshot:
        resolved_account = account or self._fetch_account()
        snapshot = CashSnapshot(
            snapshot_id=uuid4(),
            run_id=run_id,
            timestamp=observed_at or datetime.now(UTC),
            currency=str(resolved_account.get("currency") or "USD"),
            cash=_required_decimal(resolved_account, "cash"),
            buying_power=_required_decimal(resolved_account, "buying_power"),
            reserved_cash=Decimal("0"),
            equity=_optional_decimal(resolved_account, "equity"),
            source=OrderSource.BROKER_RECONCILED,
            capital_bucket=capital_bucket,
        )

        with SorUnitOfWork(self.session) as uow:
            uow.cash_snapshots.upsert(snapshot)

        return snapshot

    def sync_broker_runtime_state(
        self,
        *,
        run_id: UUID | None = None,
        observed_at: datetime | None = None,
    ) -> BrokerRuntimeSyncResult:
        resolved_observed_at = observed_at or datetime.now(UTC)
        account = self._fetch_account()
        account_snapshot = self._map_account_snapshot(
            account=account,
            observed_at=resolved_observed_at,
        )

        cash_snapshot = None
        with SorUnitOfWork(self.session) as uow:
            uow.broker_account_snapshots.upsert(account_snapshot)
            if run_id is not None:
                cash_snapshot = CashSnapshot(
                    snapshot_id=uuid4(),
                    run_id=run_id,
                    timestamp=resolved_observed_at,
                    currency=str(account.get("currency") or "USD"),
                    cash=_required_decimal(account, "cash"),
                    buying_power=_required_decimal(account, "buying_power"),
                    reserved_cash=Decimal("0"),
                    equity=_optional_decimal(account, "equity"),
                    source=OrderSource.BROKER_RECONCILED,
                    capital_bucket=_optional_decimal(account, "portfolio_value"),
                )
                uow.cash_snapshots.upsert(cash_snapshot)

        return BrokerRuntimeSyncResult(
            account_snapshot_id=account_snapshot.snapshot_id,
            cash_snapshot_id=cash_snapshot.snapshot_id if cash_snapshot else None,
            broker_account_id=account_snapshot.broker_account_id,
            observed_at=resolved_observed_at,
        )

    def validate_broker_runtime_consistency(
        self,
        *,
        tolerance: Decimal = Decimal("0.01"),
    ) -> BrokerRuntimeConsistencyResult:
        account = self._fetch_account()
        mismatches: list[str] = []

        with SorUnitOfWork(self.session) as uow:
            account_snapshot = uow.broker_account_snapshots.get_latest()
            cash_snapshot = uow.cash_snapshots.get_latest()

        if account_snapshot is None:
            mismatches.append("missing_broker_account_snapshot")
        else:
            if account_snapshot.broker_account_id != str(account.get("id") or ""):
                mismatches.append("broker_account_id")
            _compare_optional_decimal(
                mismatches,
                "account.cash",
                account_snapshot.cash,
                _optional_decimal(account, "cash"),
                tolerance,
            )
            _compare_optional_decimal(
                mismatches,
                "account.buying_power",
                account_snapshot.buying_power,
                _optional_decimal(account, "buying_power"),
                tolerance,
            )
            _compare_optional_decimal(
                mismatches,
                "account.equity",
                account_snapshot.equity,
                _optional_decimal(account, "equity"),
                tolerance,
            )
            _compare_optional_decimal(
                mismatches,
                "account.portfolio_value",
                account_snapshot.portfolio_value,
                _optional_decimal(account, "portfolio_value"),
                tolerance,
            )

        if cash_snapshot is None:
            mismatches.append("missing_cash_snapshot")
        else:
            _compare_required_decimal(
                mismatches,
                "cash.cash",
                cash_snapshot.cash,
                _required_decimal(account, "cash"),
                tolerance,
            )
            _compare_required_decimal(
                mismatches,
                "cash.buying_power",
                cash_snapshot.buying_power,
                _required_decimal(account, "buying_power"),
                tolerance,
            )
            _compare_optional_decimal(
                mismatches,
                "cash.equity",
                cash_snapshot.equity,
                _optional_decimal(account, "equity"),
                tolerance,
            )

        return BrokerRuntimeConsistencyResult(
            ok=not mismatches,
            mismatches=mismatches,
            account_snapshot_id=(
                account_snapshot.snapshot_id if account_snapshot is not None else None
            ),
            cash_snapshot_id=cash_snapshot.snapshot_id if cash_snapshot is not None else None,
        )

    def sync_positions_from_broker(
        self,
        *,
        run_id: UUID,
        observed_at: datetime | None = None,
    ) -> PositionSnapshot:
        try:
            positions = self.broker_client.get_positions()
        except Exception as exc:
            raise BrokerRuntimeSyncError(f"Broker positions fetch failed: {exc}") from exc

        snapshot = PositionSnapshot(
            snapshot_id=uuid4(),
            run_id=run_id,
            timestamp=observed_at or datetime.now(UTC),
            source=OrderSource.BROKER_RECONCILED,
        )
        snapshot.positions = [
            PositionSnapshotItem(
                snapshot_id=snapshot.snapshot_id,
                symbol=str(position["symbol"]),
                quantity=_required_decimal(position, "qty"),
                avg_cost=_optional_decimal(position, "avg_entry_price"),
                market_price=_optional_decimal(position, "current_price"),
                market_value=_optional_decimal(position, "market_value"),
                unrealized_pnl=_optional_decimal(position, "unrealized_pl"),
            )
            for position in positions
        ]

        with SorUnitOfWork(self.session) as uow:
            uow.position_snapshots.upsert(snapshot)

        return snapshot

    def sync_open_orders_from_broker(
        self,
        *,
        run_id: UUID,
        account_id: str,
    ) -> tuple[str, ...]:
        try:
            open_orders = self.broker_client.list_open_orders()
        except Exception as exc:
            raise BrokerRuntimeSyncError(f"Broker open orders fetch failed: {exc}") from exc

        broker_order_ids = []
        with SorUnitOfWork(self.session) as uow:
            for payload in open_orders:
                broker_order = self.broker_order_mapper.to_broker_order(
                    payload=payload,
                    intent_id=_runtime_sync_intent_id(payload),
                    run_id=run_id,
                    account_id=account_id,
                )
                uow.broker_orders.upsert(self.broker_order_mapper.to_orm_row(broker_order))
                broker_order_ids.append(broker_order.broker_order_id)

        return tuple(broker_order_ids)

    def sync_order_status(
        self,
        *,
        order_id: str,
        run_id: UUID,
        account_id: str,
    ) -> str:
        try:
            payload = self.broker_client.get_order_by_id(order_id)
        except Exception as exc:
            raise BrokerRuntimeSyncError(f"Broker order status fetch failed: {exc}") from exc

        broker_order = self.broker_order_mapper.to_broker_order(
            payload=payload,
            intent_id=_runtime_sync_intent_id(payload),
            run_id=run_id,
            account_id=account_id,
        )

        with SorUnitOfWork(self.session) as uow:
            uow.broker_orders.upsert(self.broker_order_mapper.to_orm_row(broker_order))

        return broker_order.broker_order_id

    def reconcile_order_fills(
        self,
        *,
        order_id: str,
        run_id: UUID,
        account_id: str,
    ) -> tuple[str, ...]:
        try:
            payload = self.broker_client.get_order_by_id(order_id)
        except Exception as exc:
            raise BrokerRuntimeSyncError(f"Broker fill reconciliation fetch failed: {exc}") from exc

        broker_order = self.broker_order_mapper.to_broker_order(
            payload=payload,
            intent_id=_runtime_sync_intent_id(payload),
            run_id=run_id,
            account_id=account_id,
        )
        fill_ids: list[str] = []

        with SorUnitOfWork(self.session) as uow:
            previous = uow.broker_orders.get_by_broker_order_id(broker_order.broker_order_id)
            previous_filled_qty = previous.filled_qty if previous is not None else Decimal("0")
            previous_avg_fill_price = previous.avg_fill_price if previous is not None else None

            fill_result = self.broker_order_mapper.extract_incremental_fill(
                broker_order=broker_order,
                previous_filled_qty=previous_filled_qty,
                previous_avg_fill_price=previous_avg_fill_price,
            )
            uow.broker_orders.upsert(self.broker_order_mapper.to_orm_row(broker_order))

            if fill_result.fill is not None:
                fill_row = self.broker_order_mapper.to_fill_orm_row(fill_result.fill)
                uow.fills.upsert(fill_row)
                fill_ids.append(fill_row.fill_id)

        return tuple(fill_ids)

    def _fetch_account(self) -> dict[str, Any]:
        try:
            return self.broker_client.get_account()
        except Exception as exc:
            raise BrokerRuntimeSyncError(f"Broker account fetch failed: {exc}") from exc

    def _map_account_snapshot(
        self,
        *,
        account: dict[str, Any],
        observed_at: datetime,
    ) -> BrokerAccountSnapshot:
        account_id = account.get("id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("Broker account response is missing account id.")

        return BrokerAccountSnapshot(
            snapshot_id=uuid4(),
            broker="alpaca",
            trading_environment=self.settings.trading_environment.value,
            broker_account_id=account_id,
            account_status=(str(account["status"]) if account.get("status") is not None else None),
            cash=_optional_decimal(account, "cash"),
            buying_power=_optional_decimal(account, "buying_power"),
            equity=_optional_decimal(account, "equity"),
            portfolio_value=_optional_decimal(account, "portfolio_value"),
            observed_at=observed_at,
        )


def _optional_decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Broker account field {key} is not decimal-safe: {value!r}") from exc


def _required_decimal(payload: dict[str, Any], key: str) -> Decimal:
    value = _optional_decimal(payload, key)
    if value is None:
        raise ValueError(f"Broker account response is missing required decimal field {key}.")
    return value


def _compare_required_decimal(
    mismatches: list[str],
    field: str,
    persisted: Decimal,
    broker_value: Decimal,
    tolerance: Decimal,
) -> None:
    if abs(persisted - broker_value) > tolerance:
        mismatches.append(field)


def _compare_optional_decimal(
    mismatches: list[str],
    field: str,
    persisted: Decimal | None,
    broker_value: Decimal | None,
    tolerance: Decimal,
) -> None:
    if persisted is None and broker_value is None:
        return
    if persisted is None or broker_value is None:
        mismatches.append(field)
        return
    _compare_required_decimal(mismatches, field, persisted, broker_value, tolerance)


def _runtime_sync_intent_id(payload: dict[str, Any]) -> UUID:
    broker_order_id = str(payload.get("id") or payload.get("client_order_id") or uuid4())
    return uuid5(NAMESPACE_URL, f"broker-runtime-sync:intent:{broker_order_id}")
