from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.validators.cash_snapshot import CASH_SNAPSHOT_RULES
from autonomous_trading_platform.contracts.validators.core import run_rules


def make_cash_snapshot(**overrides) -> CashSnapshot:
    data = {
        "snapshot_id": uuid4(),
        "run_id": uuid4(),
        "timestamp": datetime.now(UTC),
        "currency": "USD",
        "cash": Decimal("1000"),
        "buying_power": Decimal("2000"),
        "reserved_cash": Decimal("500"),
        "equity": Decimal("3000"),
        "source": OrderSource.LEDGER,
        "capital_bucket": Decimal("1000"),
    }

    data.update(overrides)
    return CashSnapshot(**data)


def test_valid_cash_snapshot_passes():
    snapshot = make_cash_snapshot()

    result = run_rules(snapshot, CASH_SNAPSHOT_RULES)

    assert result.ok
    assert result.violations == []


def test_cash_must_be_non_negative():
    snapshot = make_cash_snapshot(cash=Decimal("-1"))

    result = run_rules(snapshot, CASH_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "CASH_NON_NEGATIVE" for v in result.violations)


def test_buying_power_must_be_non_negative():
    snapshot = make_cash_snapshot(buying_power=Decimal("-1"))

    result = run_rules(snapshot, CASH_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "BUYING_POWER_NON_NEGATIVE" for v in result.violations)


def test_reserved_cash_must_be_non_negative():
    snapshot = make_cash_snapshot(reserved_cash=Decimal("-1"))

    result = run_rules(snapshot, CASH_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "RESERVED_CASH_NON_NEGATIVE" for v in result.violations)


def test_reserved_cash_must_not_exceed_cash_plus_buying_power():
    snapshot = make_cash_snapshot(
        cash=Decimal("100"),
        buying_power=Decimal("50"),
        reserved_cash=Decimal("200"),
    )

    result = run_rules(snapshot, CASH_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "RESERVED_CASH_LTE_CASH_PLUS_BUYING_POWER" for v in result.violations)
