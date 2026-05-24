"""
Unit tests for settlement-aware cash accounting in CashLedgerService (TASK F-06).

Covers:
- Legacy behavior preserved: settlement_days=0 keeps SELL proceeds immediately settled
- SELL with settlement_days>0 adds proceeds to unsettled_cash, not settled_cash
- BUY always deducts from settled_cash regardless of settlement_days
- buying_power = settled_cash - reserved_cash (not total cash - reserved)
- reserve_order uses settled_cash for buying power (unsettled proceeds cannot be reserved)
- release_reservation preserves settled/unsettled split
- settled_cash + unsettled_cash == cash invariant holds for all operations
- Backward compat: snapshots without settled_cash/unsettled_cash treated as fully settled
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.common.enums import OrderSource, Side
from autonomous_trading_platform.execution.services.cash_ledger_service import (
    CashLedgerService,
)
from tests.utilities.factories import make_fill

_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000011")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000012")
_TS = datetime(2025, 6, 1, 15, 30, tzinfo=UTC)


def _snap(
    *,
    cash: str,
    reserved: str = "0",
    settled: str | None = None,
    unsettled: str = "0",
) -> CashSnapshot:
    c = Decimal(cash)
    r = Decimal(reserved)
    s = Decimal(settled) if settled is not None else None
    u = Decimal(unsettled)
    return CashSnapshot(
        snapshot_id=_SNAPSHOT_ID,
        run_id=_RUN_ID,
        timestamp=_TS,
        currency="USD",
        cash=c,
        buying_power=c - r,
        reserved_cash=r,
        equity=None,
        source=OrderSource.SIMULATION,
        capital_bucket=None,
        settled_cash=s,
        unsettled_cash=u,
    )


@pytest.fixture
def svc() -> CashLedgerService:
    return CashLedgerService()


# ---------------------------------------------------------------------------
# Legacy behavior: settlement_days=0 (default)
# ---------------------------------------------------------------------------


class TestLegacyImmediateSettlement:
    def test_sell_proceeds_immediately_settled_when_days_zero(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="5000", settled="5000", unsettled="0")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=0)

        assert result.settled_cash == Decimal("6000")
        assert result.unsettled_cash == Decimal("0")
        assert result.cash == Decimal("6000")

    def test_sell_buying_power_increases_immediately_when_days_zero(
        self, svc: CashLedgerService
    ) -> None:
        snap = _snap(cash="5000", reserved="0", settled="5000")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=0)

        assert result.buying_power == Decimal("6000")

    def test_buy_deducts_from_settled_cash_when_days_zero(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="8000", settled="10000")
        fill = make_fill(side=Side.BUY, quantity="100", price="80")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=0)

        assert result.settled_cash == Decimal("2000")
        assert result.unsettled_cash == Decimal("0")
        assert result.cash == Decimal("2000")

    def test_cash_invariant_buy_days_zero(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="0", settled="10000")
        fill = make_fill(side=Side.BUY, quantity="50", price="100")
        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=0)

        assert result.cash == result.settled_cash + result.unsettled_cash

    def test_cash_invariant_sell_days_zero(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="5000", reserved="0", settled="5000")
        fill = make_fill(side=Side.SELL, quantity="10", price="200")
        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=0)

        assert result.cash == result.settled_cash + result.unsettled_cash


# ---------------------------------------------------------------------------
# Pending settlement: settlement_days > 0
# ---------------------------------------------------------------------------


class TestPendingSettlement:
    def test_sell_proceeds_go_to_unsettled_when_days_positive(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="5000", settled="5000", unsettled="0")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=1)

        assert result.unsettled_cash == Decimal("1000")
        assert result.settled_cash == Decimal("5000")

    def test_sell_settled_cash_unchanged_when_days_positive(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="5000", settled="5000")
        fill = make_fill(side=Side.SELL, quantity="20", price="50")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=2)

        assert result.settled_cash == Decimal("5000")

    def test_sell_total_cash_increases_by_proceeds_when_days_positive(
        self, svc: CashLedgerService
    ) -> None:
        snap = _snap(cash="5000", settled="5000")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=1)

        # Total cash = 5000 + 1000 = 6000 (economic value unchanged by settlement delay)
        assert result.cash == Decimal("6000")

    def test_sell_buying_power_unchanged_when_days_positive(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="5000", reserved="0", settled="5000")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=1)

        # buying_power = settled_cash - reserved = 5000 - 0 = 5000 (unchanged)
        assert result.buying_power == Decimal("5000")

    def test_sell_buying_power_excludes_unsettled_with_existing_reserved(
        self, svc: CashLedgerService
    ) -> None:
        snap = _snap(cash="10000", reserved="3000", settled="10000")
        fill = make_fill(side=Side.SELL, quantity="10", price="200")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=1)

        # settled stays 10000, reserved stays 3000 → buying_power = 7000
        assert result.buying_power == Decimal("7000")
        assert result.unsettled_cash == Decimal("2000")

    def test_cumulative_unsettled_across_multiple_sells(self, svc: CashLedgerService) -> None:
        snap1 = _snap(cash="5000", settled="5000")
        fill1 = make_fill(side=Side.SELL, quantity="10", price="100")
        r1 = svc.apply_fill(existing_snapshot=snap1, fill=fill1, settlement_days=2)

        # Second sell; snapshot now has unsettled from first sell
        snap2 = _snap(
            cash=str(r1.cash),
            reserved="0",
            settled=str(r1.settled_cash),
            unsettled=str(r1.unsettled_cash),
        )
        fill2 = make_fill(side=Side.SELL, quantity="5", price="200")
        r2 = svc.apply_fill(existing_snapshot=snap2, fill=fill2, settlement_days=2)

        assert r2.unsettled_cash == Decimal("2000")  # 1000 + 1000
        assert r2.settled_cash == Decimal("5000")
        assert r2.cash == Decimal("7000")

    def test_cash_invariant_sell_days_positive(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="5000", settled="5000")
        fill = make_fill(side=Side.SELL, quantity="10", price="150")
        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=1)

        assert result.cash == result.settled_cash + result.unsettled_cash

    def test_settlement_days_t2(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="8000", settled="8000")
        fill = make_fill(side=Side.SELL, quantity="40", price="100")
        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=2)

        assert result.unsettled_cash == Decimal("4000")
        assert result.settled_cash == Decimal("8000")

    def test_negative_settlement_days_raises(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="5000", settled="5000")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        with pytest.raises(ValueError, match="settlement_days"):
            svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=-1)


# ---------------------------------------------------------------------------
# BUY with settlement_days — always deducts settled cash
# ---------------------------------------------------------------------------


class TestBuyWithSettlementDays:
    def test_buy_deducts_from_settled_when_days_positive(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="8000", settled="10000")
        fill = make_fill(side=Side.BUY, quantity="100", price="80")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=1)

        assert result.settled_cash == Decimal("2000")
        assert result.unsettled_cash == Decimal("0")

    def test_buy_with_existing_unsettled_does_not_touch_unsettled(
        self, svc: CashLedgerService
    ) -> None:
        # Prior sell proceeds are in unsettled; BUY should not consume them
        snap = _snap(cash="11000", reserved="8000", settled="10000", unsettled="1000")
        fill = make_fill(side=Side.BUY, quantity="100", price="80")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=1)

        assert result.settled_cash == Decimal("2000")
        assert result.unsettled_cash == Decimal("1000")  # unchanged
        assert result.cash == Decimal("3000")


# ---------------------------------------------------------------------------
# reserve_order: uses settled_cash for buying power
# ---------------------------------------------------------------------------


class TestReserveOrderWithSettlement:
    def test_reserve_uses_settled_cash_not_total_cash(self, svc: CashLedgerService) -> None:
        # total cash = 11000 but only 10000 is settled; reserve against settled
        snap = _snap(cash="11000", reserved="0", settled="10000", unsettled="1000")

        result = svc.reserve_order(snap, Decimal("8000"))

        assert result.reserved_cash == Decimal("8000")
        assert result.buying_power == Decimal("2000")  # settled - reserved = 10000 - 8000
        assert result.settled_cash == Decimal("10000")
        assert result.unsettled_cash == Decimal("1000")

    def test_reserve_cannot_exceed_settled_cash(self, svc: CashLedgerService) -> None:
        # 1000 settled, 5000 unsettled → cannot reserve beyond 1000
        snap = _snap(cash="6000", reserved="0", settled="1000", unsettled="5000")

        with pytest.raises(ValueError, match="insufficient"):
            svc.reserve_order(snap, Decimal("2000"))

    def test_reserve_up_to_settled_cash_is_allowed(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="6000", reserved="0", settled="1000", unsettled="5000")

        result = svc.reserve_order(snap, Decimal("1000"))

        assert result.reserved_cash == Decimal("1000")
        assert result.buying_power == Decimal("0")

    def test_reserve_preserves_unsettled_cash(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="11000", reserved="0", settled="10000", unsettled="1000")

        result = svc.reserve_order(snap, Decimal("5000"))

        assert result.unsettled_cash == Decimal("1000")
        assert result.cash == Decimal("11000")

    def test_reserve_cash_invariant(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="11000", reserved="0", settled="10000", unsettled="1000")
        result = svc.reserve_order(snap, Decimal("3000"))

        assert result.cash == result.settled_cash + result.unsettled_cash


# ---------------------------------------------------------------------------
# release_reservation: preserves settled/unsettled split
# ---------------------------------------------------------------------------


class TestReleaseReservationWithSettlement:
    def test_release_preserves_settled_unsettled_split(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="11000", reserved="8000", settled="10000", unsettled="1000")

        result = svc.release_reservation(snap, Decimal("8000"))

        assert result.settled_cash == Decimal("10000")
        assert result.unsettled_cash == Decimal("1000")
        assert result.cash == Decimal("11000")

    def test_release_restores_buying_power_from_settled(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="11000", reserved="8000", settled="10000", unsettled="1000")

        result = svc.release_reservation(snap, Decimal("8000"))

        assert result.buying_power == Decimal("10000")

    def test_release_cash_invariant(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="11000", reserved="5000", settled="10000", unsettled="1000")
        result = svc.release_reservation(snap, Decimal("3000"))

        assert result.cash == result.settled_cash + result.unsettled_cash


# ---------------------------------------------------------------------------
# Backward compatibility: legacy snapshots without settled_cash/unsettled_cash
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_legacy_snap_treats_all_cash_as_settled_for_sell(self, svc: CashLedgerService) -> None:
        # Legacy snapshot has no settled_cash/unsettled_cash fields
        snap = _snap(cash="5000", reserved="0")  # settled=None, unsettled=None by default

        fill = make_fill(side=Side.SELL, quantity="10", price="100")
        result = svc.apply_fill(existing_snapshot=snap, fill=fill, settlement_days=0)

        assert result.settled_cash == Decimal("6000")
        assert result.unsettled_cash == Decimal("0")
        assert result.cash == Decimal("6000")

    def test_legacy_snap_buy_deducts_from_full_cash(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="8000")  # settled=None

        fill = make_fill(side=Side.BUY, quantity="100", price="80")
        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("2000")
        assert result.reserved_cash == Decimal("0")
        assert result.buying_power == Decimal("2000")

    def test_legacy_snap_reserve_uses_full_cash_as_settled(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="0")  # settled=None

        result = svc.reserve_order(snap, Decimal("8000"))

        assert result.reserved_cash == Decimal("8000")
        assert result.buying_power == Decimal("2000")

    def test_existing_unit_tests_still_pass_sell_does_not_alter_reserved(
        self, svc: CashLedgerService
    ) -> None:
        snap = _snap(cash="5000", reserved="3000")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("6000")
        assert result.reserved_cash == Decimal("3000")
        assert result.buying_power == Decimal("3000")

    def test_existing_unit_tests_still_pass_buy_consumes_reservation(
        self, svc: CashLedgerService
    ) -> None:
        snap = _snap(cash="10000", reserved="8000")
        fill = make_fill(side=Side.BUY, quantity="100", price="80")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("2000")
        assert result.reserved_cash == Decimal("0")


# ---------------------------------------------------------------------------
# Integration: sell → pending → buy blocked → settlement conceptual flow
# ---------------------------------------------------------------------------


class TestSettlementIntegrationConcept:
    def test_sell_proceeds_cannot_back_buy_reservation_before_settlement(
        self, svc: CashLedgerService
    ) -> None:
        """After a sell with settlement_days=1, settled_cash is unchanged so a
        large buy reservation that would exceed settled_cash is blocked."""
        snap = _snap(cash="2000", reserved="0", settled="2000")

        # Sell — proceeds go to unsettled
        fill_sell = make_fill(side=Side.SELL, quantity="50", price="100")
        after_sell = svc.apply_fill(existing_snapshot=snap, fill=fill_sell, settlement_days=1)

        assert after_sell.settled_cash == Decimal("2000")  # unchanged
        assert after_sell.unsettled_cash == Decimal("5000")
        assert after_sell.buying_power == Decimal("2000")  # still only settled

        # Attempt to reserve more than settled cash → must fail
        snap_after_sell = _snap(
            cash=str(after_sell.cash),
            reserved="0",
            settled=str(after_sell.settled_cash),
            unsettled=str(after_sell.unsettled_cash),
        )
        with pytest.raises(ValueError, match="insufficient"):
            svc.reserve_order(snap_after_sell, Decimal("4000"))

    def test_settled_cash_backed_buy_is_allowed(self, svc: CashLedgerService) -> None:
        """A buy within settled_cash is still allowed even when unsettled_cash exists."""
        snap = _snap(cash="7000", reserved="0", settled="2000", unsettled="5000")

        result = svc.reserve_order(snap, Decimal("1500"))

        assert result.reserved_cash == Decimal("1500")
        assert result.buying_power == Decimal("500")
