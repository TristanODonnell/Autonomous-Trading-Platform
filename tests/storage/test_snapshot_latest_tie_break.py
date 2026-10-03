"""`get_latest` on cash and position snapshots picks the row written last when several
share a timestamp (one trading cycle writes one cash snapshot per fill); before
`recorded_at` the tie fell to the random snapshot id, so the daily portfolio snapshot
could pair a pre-fill cash balance with post-fill positions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.repositories.core.cash_snapshot_repository import (
    CashSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.position_snapshot_repository import (
    PositionSnapshotRepository,
)

_TICK = datetime(2024, 6, 11, 20, 0, tzinfo=UTC)
_RUN = UUID("00000000-0000-0000-0000-00000000c0de")
# Written in this order; the ids are chosen so the old random-id tie-break would pick
# the FIRST one (highest id) instead of the last.
_IDS = [
    UUID("ffffffff-0000-0000-0000-000000000001"),
    UUID("aaaaaaaa-0000-0000-0000-000000000002"),
    UUID("00000000-0000-0000-0000-000000000003"),
]
_WRITTEN = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)


def _cash(snapshot_id: UUID, cash: str, recorded_at: datetime | None) -> CashSnapshot:
    row = CashSnapshot(
        snapshot_id=snapshot_id,
        run_id=_RUN,
        timestamp=_TICK,
        currency="USD",
        cash=Decimal(cash),
        buying_power=Decimal(cash),
        reserved_cash=Decimal("0"),
        equity=Decimal(cash),
        source=OrderSource.SIMULATION,
    )
    if recorded_at is not None:
        row.recorded_at = recorded_at
    return row


def test_latest_cash_snapshot_is_the_one_written_last(db_session) -> None:
    repo = CashSnapshotRepository(db_session)
    cashes = ("218129.59", "205000.00", "191188.45")
    for i, (sid, cash) in enumerate(zip(_IDS, cashes, strict=True)):
        repo.insert(_cash(sid, cash, _WRITTEN + timedelta(microseconds=i)))
    db_session.flush()

    latest = repo.get_latest()

    assert latest is not None
    assert latest.snapshot_id == _IDS[-1]
    assert latest.cash == Decimal("191188.45")
    assert [r.snapshot_id for r in repo.list_recent(limit=3)] == list(reversed(_IDS))


def test_recorded_at_is_set_on_insert_by_default(db_session) -> None:
    repo = CashSnapshotRepository(db_session)
    repo.insert(_cash(_IDS[0], "1", None))
    db_session.flush()
    row = repo.get_by_snapshot_id(_IDS[0])  # type: ignore[arg-type]
    assert row is not None
    assert row.recorded_at is not None


def test_rows_without_recorded_at_still_order_by_timestamp(db_session) -> None:
    """Pre-migration rows (recorded_at NULL) lose to a later write at the same tick and
    win when their own timestamp is newer."""
    repo = CashSnapshotRepository(db_session)
    legacy = _cash(_IDS[0], "100", None)
    repo.insert(legacy)
    repo.insert(_cash(_IDS[2], "200", datetime(2026, 10, 2, tzinfo=UTC)))
    newer_legacy = _cash(_IDS[1], "300", None)
    newer_legacy.timestamp = _TICK + timedelta(minutes=5)
    repo.insert(newer_legacy)
    db_session.flush()
    for row in (legacy, newer_legacy):
        row.recorded_at = None
    db_session.flush()

    latest = repo.get_latest()
    assert latest is not None
    assert latest.cash == Decimal("300")
    db_session.delete(newer_legacy)
    db_session.flush()
    latest = repo.get_latest()
    assert latest is not None
    assert latest.cash == Decimal("200")


def test_latest_position_snapshot_is_the_one_written_last(db_session) -> None:
    repo = PositionSnapshotRepository(db_session)
    # the unique constraint is (run_id, timestamp, source): vary the source
    sources = (OrderSource.SIMULATION, OrderSource.BROKER_RECONCILED, OrderSource.LEDGER)
    for i, (sid, source) in enumerate(zip(_IDS, sources, strict=True)):
        row = PositionSnapshot(snapshot_id=sid, run_id=_RUN, timestamp=_TICK, source=source)
        row.recorded_at = _WRITTEN + timedelta(microseconds=i)
        db_session.add(row)
    db_session.flush()

    latest = repo.get_latest()

    assert latest is not None
    assert latest.snapshot_id == _IDS[-1]
