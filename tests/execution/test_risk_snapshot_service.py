from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.accounting.cash_snapshot import CashSnapshot
from autonomous_trading_platform.contracts.accounting.position_snapshot import (
    Position,
    PositionSnapshot,
)
from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskLimitConfig,
    RiskSnapshotService,
)
from autonomous_trading_platform.storage.sor.repositories.core.risk_snapshot_repository import (
    RiskSnapshotRepository,
)


def test_risk_snapshot_exposes_portfolio_concentration_metrics() -> None:
    now = datetime.now(UTC)
    run_id = uuid4()
    position_snapshot = PositionSnapshot(
        snapshot_id=uuid4(),
        run_id=run_id,
        timestamp=now,
        source=OrderSource.LEDGER,
        positions=[
            Position(
                symbol="MSFT",
                quantity=Decimal("10"),
                avg_cost=Decimal("200"),
                market_price=Decimal("200"),
                market_value=Decimal("2000"),
                unrealized_pnl=Decimal("0"),
            ),
            Position(
                symbol="AAPL",
                quantity=Decimal("50"),
                avg_cost=Decimal("100"),
                market_price=Decimal("100"),
                market_value=Decimal("5000"),
                unrealized_pnl=Decimal("0"),
            ),
        ],
    )
    cash_snapshot = CashSnapshot(
        snapshot_id=uuid4(),
        run_id=run_id,
        timestamp=now,
        currency="USD",
        cash=Decimal("93000"),
        buying_power=Decimal("93000"),
        reserved_cash=Decimal("0"),
        equity=Decimal("100000"),
        source=OrderSource.LEDGER,
    )

    snapshot = RiskSnapshotService().compute_snapshot(
        run_id=run_id,
        timestamp=now,
        position_snapshot=position_snapshot,
        cash_snapshot=cash_snapshot,
        limits_config=RiskLimitConfig(max_portfolio_symbol_pct=Decimal("0.20")),
    )

    concentration = snapshot.utilization["portfolio_symbol_concentration"]
    top_symbols = concentration["top_concentrated_symbols"]

    assert concentration["gross_exposure_usd"] == Decimal("7000")
    assert concentration["net_exposure_usd"] == Decimal("7000")
    assert top_symbols[0]["symbol"] == "AAPL"
    assert top_symbols[0]["exposure_pct"] == 0.05
    assert top_symbols[0]["limit_utilization"] == 0.25
    assert snapshot.limits["max_portfolio_symbol_pct"] == Decimal("0.20")


def test_risk_snapshot_repository_persists_concentration_metrics(db_session) -> None:
    now = datetime.now(UTC)
    run_id = uuid4()
    snapshot = RiskSnapshotService().compute_snapshot(
        run_id=run_id,
        timestamp=now,
        position_snapshot=PositionSnapshot(
            snapshot_id=uuid4(),
            run_id=run_id,
            timestamp=now,
            source=OrderSource.LEDGER,
            positions=[
                Position(
                    symbol="AAPL",
                    quantity=Decimal("50"),
                    avg_cost=Decimal("100"),
                    market_price=Decimal("100"),
                    market_value=Decimal("5000"),
                    unrealized_pnl=Decimal("0"),
                ),
            ],
        ),
        cash_snapshot=CashSnapshot(
            snapshot_id=uuid4(),
            run_id=run_id,
            timestamp=now,
            currency="USD",
            cash=Decimal("95000"),
            buying_power=Decimal("95000"),
            reserved_cash=Decimal("0"),
            equity=Decimal("100000"),
            source=OrderSource.LEDGER,
        ),
        limits_config=RiskLimitConfig(max_portfolio_symbol_pct=Decimal("0.20")),
    )

    row = RiskSnapshotRepository(db_session).upsert(snapshot)

    concentration = row.utilization["portfolio_symbol_concentration"]
    assert row.limits["max_portfolio_symbol_pct"] == "0.20"
    assert concentration["top_concentrated_symbols"][0]["symbol"] == "AAPL"
    assert concentration["top_concentrated_symbols"][0]["limit_utilization"] == 0.25
