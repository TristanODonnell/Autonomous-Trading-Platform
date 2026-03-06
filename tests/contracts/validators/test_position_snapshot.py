from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.position_snapshot import (
    POSITION_SNAPSHOT_RULES,
)
from src.autonomous_trading_platform.contracts.accounting.position_snapshot import (
    Position,
    PositionSnapshot,
)


def make_position(**overrides) -> Position:
    data = {
        "symbol": "AAPL",
        "quantity": Decimal("10"),
        "avg_cost": Decimal("150"),
        "market_price": Decimal("155"),
        "market_value": Decimal("1550"),
        "unrealized_pnl": Decimal("50"),
    }

    data.update(overrides)
    return Position(**data)


def make_position_snapshot(**overrides) -> PositionSnapshot:
    data = {
        "snapshot_id": uuid4(),
        "run_id": uuid4(),
        "timestamp": datetime.now(UTC),
        "positions": [
            make_position(symbol="AAPL"),
            make_position(symbol="MSFT"),
        ],
        "source": OrderSource.BROKER_RECONCILED,
    }

    data.update(overrides)
    return PositionSnapshot(**data)


def test_valid_position_snapshot_passes():
    snapshot = make_position_snapshot()

    result = run_rules(snapshot, POSITION_SNAPSHOT_RULES)

    assert result.ok
    assert result.violations == []


def test_positions_must_have_unique_symbols():
    snapshot = make_position_snapshot(
        positions=[
            make_position(symbol="AAPL"),
            make_position(symbol="AAPL"),
        ]
    )

    result = run_rules(snapshot, POSITION_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "UNIQUE_SYMBOLS_IN_POSITION_SNAPSHOT" for v in result.violations)


def test_quantities_must_be_non_negative():
    snapshot = make_position_snapshot(
        positions=[
            make_position(symbol="AAPL", quantity=Decimal("-5")),
            make_position(symbol="MSFT", quantity=Decimal("10")),
        ]
    )

    result = run_rules(snapshot, POSITION_SNAPSHOT_RULES)

    assert not result.ok
    assert any(v.code == "LONG_ONLY_QUANTITY_NON_NEGATIVE" for v in result.violations)
