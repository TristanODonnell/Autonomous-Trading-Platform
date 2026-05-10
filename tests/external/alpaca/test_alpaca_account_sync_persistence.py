from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.services.broker_runtime_sync_service import (
    BrokerRuntimeSyncService,
)
from autonomous_trading_platform.storage.sor.models.broker_account_snapshots import (
    BrokerAccountSnapshot,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot

pytestmark = [
    pytest.mark.external,
    pytest.mark.alpaca,
    pytest.mark.smoke,
]


def test_real_alpaca_account_sync_persists_account_and_cash_state(
    db_session: Session,
    alpaca_paper_client: AlpacaBrokerClient,
    alpaca_paper_settings: Settings,
) -> None:
    run_id = uuid4()
    service = BrokerRuntimeSyncService(
        session=db_session,
        broker_client=alpaca_paper_client,
        settings=alpaca_paper_settings,
    )

    result = service.sync_broker_runtime_state(run_id=run_id)

    account_snapshot = db_session.get(BrokerAccountSnapshot, result.account_snapshot_id)
    cash_snapshot = db_session.get(CashSnapshot, result.cash_snapshot_id)

    assert account_snapshot is not None
    assert cash_snapshot is not None
    assert account_snapshot.broker == "alpaca"
    assert account_snapshot.trading_environment == "paper"
    assert account_snapshot.broker_account_id == result.broker_account_id
    assert account_snapshot.account_status is not None
    assert account_snapshot.cash is not None
    assert account_snapshot.buying_power is not None
    assert account_snapshot.equity is not None
    assert account_snapshot.portfolio_value is not None
    assert account_snapshot.cash >= Decimal("0")
    assert account_snapshot.buying_power >= Decimal("0")
    assert account_snapshot.equity >= Decimal("0")

    assert cash_snapshot.run_id == run_id
    assert cash_snapshot.source == OrderSource.BROKER_RECONCILED
    assert cash_snapshot.currency == "USD"
    assert cash_snapshot.cash == account_snapshot.cash
    assert cash_snapshot.buying_power == account_snapshot.buying_power
    assert cash_snapshot.equity == account_snapshot.equity

    consistency = service.validate_broker_runtime_consistency()

    assert consistency.ok is True
    assert consistency.mismatches == []
    assert consistency.account_snapshot_id == account_snapshot.snapshot_id
    assert consistency.cash_snapshot_id == cash_snapshot.snapshot_id
