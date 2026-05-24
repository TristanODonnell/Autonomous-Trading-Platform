"""
Unit tests for the reserved cash lifecycle in CashLedgerService (TASK A-03).

Covers:
- reserve_order: increases reserved_cash, reduces buying_power
- reserve_order: raises on insufficient buying power
- reserve_order: raises on negative notional
- release_reservation: decreases reserved_cash, increases buying_power
- release_reservation: clamps to zero (cannot go negative)
- release_reservation: raises on negative notional
- apply_fill BUY: consumes reservation proportionally
- apply_fill SELL: does NOT alter reserved_cash (sell proceeds != buy reservation release)
- buying_power = cash - reserved_cash after BUY fill
- buying_power = cash - reserved_cash after reserve_order
- Sequential reservations reduce buying_power cumulatively
- Full fill: reservation fully consumed
- Partial fill: reservation partially consumed, remainder stays reserved
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

_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000002")
_TS = datetime(2025, 1, 1, 15, 30, tzinfo=UTC)


def _snap(
    *,
    cash: str,
    reserved: str = "0",
) -> CashSnapshot:
    c = Decimal(cash)
    r = Decimal(reserved)
    return CashSnapshot(
        snapshot_id=_SNAPSHOT_ID,
        run_id=_RUN_ID,
        timestamp=_TS,
        currency="USD",
        cash=c,
        buying_power=c - r,
        reserved_cash=r,
        equity=None,
        source=OrderSource.LEDGER,
        capital_bucket=None,
    )


@pytest.fixture
def svc() -> CashLedgerService:
    return CashLedgerService()


# ---------------------------------------------------------------------------
# reserve_order
# ---------------------------------------------------------------------------


class TestReserveOrder:
    def test_reserve_increases_reserved_cash(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000")
        result = svc.reserve_order(snap, Decimal("8000"))

        assert result.reserved_cash == Decimal("8000")
        assert result.cash == Decimal("10000")

    def test_reserve_reduces_buying_power(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000")
        result = svc.reserve_order(snap, Decimal("8000"))

        assert result.buying_power == Decimal("2000")

    def test_reserve_exact_buying_power_is_allowed(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000")
        result = svc.reserve_order(snap, Decimal("10000"))

        assert result.reserved_cash == Decimal("10000")
        assert result.buying_power == Decimal("0")

    def test_reserve_exceeding_buying_power_raises(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000")

        with pytest.raises(ValueError, match="insufficient"):
            svc.reserve_order(snap, Decimal("10001"))

    def test_reserve_exceeding_buying_power_with_existing_reservation_raises(
        self, svc: CashLedgerService
    ) -> None:
        snap = _snap(cash="10000", reserved="8000")  # only $2000 free

        with pytest.raises(ValueError, match="insufficient"):
            svc.reserve_order(snap, Decimal("2001"))

    def test_reserve_negative_notional_raises(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000")

        with pytest.raises(ValueError, match="negative"):
            svc.reserve_order(snap, Decimal("-1"))

    def test_reserve_zero_notional_is_noop(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="500")
        result = svc.reserve_order(snap, Decimal("0"))

        assert result.reserved_cash == Decimal("500")
        assert result.buying_power == Decimal("9500")

    def test_sequential_reservations_reduce_buying_power_cumulatively(
        self, svc: CashLedgerService
    ) -> None:
        snap = _snap(cash="10000")

        r1 = svc.reserve_order(snap, Decimal("3000"))
        snap2 = _snap(cash="10000", reserved=str(r1.reserved_cash))
        r2 = svc.reserve_order(snap2, Decimal("3000"))

        assert r2.reserved_cash == Decimal("6000")
        assert r2.buying_power == Decimal("4000")

    def test_reserve_from_none_snapshot_treats_cash_as_zero(self, svc: CashLedgerService) -> None:
        # No prior snapshot → no free cash → any reservation should fail
        with pytest.raises(ValueError, match="insufficient"):
            svc.reserve_order(None, Decimal("1"))

    def test_total_costs_is_zero_for_reservation(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000")
        result = svc.reserve_order(snap, Decimal("5000"))
        assert result.total_costs == Decimal("0")


# ---------------------------------------------------------------------------
# release_reservation
# ---------------------------------------------------------------------------


class TestReleaseReservation:
    def test_release_decreases_reserved_cash(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="8000")
        result = svc.release_reservation(snap, Decimal("8000"))

        assert result.reserved_cash == Decimal("0")

    def test_release_increases_buying_power(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="8000")
        result = svc.release_reservation(snap, Decimal("8000"))

        assert result.buying_power == Decimal("10000")

    def test_release_partial_amount(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="8000")
        result = svc.release_reservation(snap, Decimal("3000"))

        assert result.reserved_cash == Decimal("5000")
        assert result.buying_power == Decimal("5000")

    def test_release_clamps_to_zero_when_exceeding_pool(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="500")
        # Releasing more than reserved should floor at 0, not go negative
        result = svc.release_reservation(snap, Decimal("1000"))

        assert result.reserved_cash == Decimal("0")

    def test_release_negative_notional_raises(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="500")

        with pytest.raises(ValueError, match="negative"):
            svc.release_reservation(snap, Decimal("-1"))

    def test_release_zero_is_noop(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="500")
        result = svc.release_reservation(snap, Decimal("0"))

        assert result.reserved_cash == Decimal("500")

    def test_cash_unchanged_after_release(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="3000")
        result = svc.release_reservation(snap, Decimal("3000"))

        assert result.cash == Decimal("10000")

    def test_total_costs_is_zero_for_release(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="3000")
        result = svc.release_reservation(snap, Decimal("3000"))
        assert result.total_costs == Decimal("0")


# ---------------------------------------------------------------------------
# apply_fill — reservation consumption
# ---------------------------------------------------------------------------


class TestApplyFillReservation:
    def test_buy_fill_consumes_reservation(self, svc: CashLedgerService) -> None:
        # $8000 reserved for 100 shares; fill 100 shares @ $80 consumes the reservation
        snap = _snap(cash="10000", reserved="8000")
        fill = make_fill(side=Side.BUY, quantity="100", price="80")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("2000")
        assert result.reserved_cash == Decimal("0")

    def test_buy_fill_buying_power_equals_cash_minus_remaining_reserved(
        self, svc: CashLedgerService
    ) -> None:
        # $8000 reserved; fill consumes $1000; $7000 still reserved for other orders
        snap = _snap(cash="10000", reserved="8000")
        fill = make_fill(side=Side.BUY, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("9000")
        assert result.reserved_cash == Decimal("7000")
        assert result.buying_power == Decimal("2000")

    def test_partial_fill_leaves_remaining_reservation(self, svc: CashLedgerService) -> None:
        # Reservation for 100 shares @ $80 = $8000
        # Partial fill: 60 shares @ $80 = $4800 consumed → $3200 still reserved
        snap = _snap(cash="10000", reserved="8000")
        fill = make_fill(side=Side.BUY, quantity="60", price="80")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("5200")
        assert result.reserved_cash == Decimal("3200")
        assert result.buying_power == Decimal("2000")

    def test_sell_fill_does_not_alter_reserved_cash(self, svc: CashLedgerService) -> None:
        # Pending BUY orders have $3000 reserved; SELL fill must not release that
        snap = _snap(cash="5000", reserved="3000")
        fill = make_fill(side=Side.SELL, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("6000")
        assert result.reserved_cash == Decimal("3000")
        assert result.buying_power == Decimal("3000")

    def test_buy_fill_with_no_prior_reservation(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="0")
        fill = make_fill(side=Side.BUY, quantity="10", price="100")

        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.cash == Decimal("9000")
        assert result.reserved_cash == Decimal("0")
        assert result.buying_power == Decimal("9000")

    def test_reserve_then_full_fill_restores_buying_power(self, svc: CashLedgerService) -> None:
        # Full round-trip: reserve → fill → buying_power back to cash
        snap = _snap(cash="10000")
        reserved = svc.reserve_order(snap, Decimal("5000"))
        assert reserved.buying_power == Decimal("5000")

        snap2 = _snap(cash="10000", reserved="5000")
        fill = make_fill(side=Side.BUY, quantity="50", price="100")
        filled = svc.apply_fill(existing_snapshot=snap2, fill=fill)

        assert filled.cash == Decimal("5000")
        assert filled.reserved_cash == Decimal("0")
        assert filled.buying_power == Decimal("5000")

    def test_over_allocation_prevention_second_order_blocked(self, svc: CashLedgerService) -> None:
        # $10000 available; first order reserves $8000; second $8000 order must be blocked
        snap = _snap(cash="10000")
        svc.reserve_order(snap, Decimal("8000"))  # first reservation succeeds

        snap_after_first = _snap(cash="10000", reserved="8000")
        with pytest.raises(ValueError, match="insufficient"):
            svc.reserve_order(snap_after_first, Decimal("8000"))

    def test_buying_power_formula_with_zero_reserved(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="0")
        fill = make_fill(side=Side.BUY, quantity="10", price="100")
        result = svc.apply_fill(existing_snapshot=snap, fill=fill)

        assert result.buying_power == result.cash - result.reserved_cash


# ---------------------------------------------------------------------------
# Lifecycle integration: reserve → partial fill → release remainder
# ---------------------------------------------------------------------------


class TestReservationLifecycleIntegration:
    def test_partial_fill_carry_forward_lifecycle(self, svc: CashLedgerService) -> None:
        """Simulate the full lifecycle of a partially filled order.

        1. Reserve $8000 for 100 shares @ $80
        2. Partial fill: 60 shares at $80 → consumes $4800
        3. 40 shares carry forward: $3200 stays reserved
        4. Second fill: 40 shares @ $80 → consumes remaining $3200
        5. Final state: $0 reserved, cash = initial - 100*80
        """
        initial_cash = Decimal("10000")
        initial_snap = _snap(cash="10000")

        # Step 1: reserve
        reserved = svc.reserve_order(initial_snap, Decimal("8000"))
        assert reserved.reserved_cash == Decimal("8000")
        assert reserved.buying_power == Decimal("2000")

        # Step 2: partial fill (60 shares)
        snap_after_reserve = _snap(cash="10000", reserved="8000")
        fill1 = make_fill(side=Side.BUY, quantity="60", price="80")
        after_fill1 = svc.apply_fill(existing_snapshot=snap_after_reserve, fill=fill1)

        assert after_fill1.cash == Decimal("5200")
        assert after_fill1.reserved_cash == Decimal("3200")  # 40 shares still pending

        # Step 3: second fill (remaining 40 shares)
        snap_after_fill1 = _snap(cash="5200", reserved="3200")
        fill2 = make_fill(side=Side.BUY, quantity="40", price="80")
        after_fill2 = svc.apply_fill(existing_snapshot=snap_after_fill1, fill=fill2)

        assert after_fill2.cash == initial_cash - Decimal("8000")
        assert after_fill2.reserved_cash == Decimal("0")
        assert after_fill2.buying_power == after_fill2.cash

    def test_cancellation_releases_full_reservation(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="8000")
        released = svc.release_reservation(snap, Decimal("8000"))

        assert released.reserved_cash == Decimal("0")
        assert released.cash == Decimal("10000")
        assert released.buying_power == Decimal("10000")

    def test_rejection_releases_reservation(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="5000")
        released = svc.release_reservation(snap, Decimal("5000"))

        assert released.reserved_cash == Decimal("0")
        assert released.buying_power == Decimal("10000")

    def test_expiry_releases_reservation(self, svc: CashLedgerService) -> None:
        snap = _snap(cash="10000", reserved="2500")
        released = svc.release_reservation(snap, Decimal("2500"))

        assert released.reserved_cash == Decimal("0")
