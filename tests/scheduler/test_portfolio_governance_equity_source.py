"""The drawdown guards measure the broker account, not the fill-by-fill ledger.

Findings 21 / 25: the ledger said $121k while the account held $97k, and a resync left it
3 AAPL over the broker for two sessions. Both cycles now read live broker equity and only
fall back to the ledger's latest cash snapshot when the broker does not answer.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest

import autonomous_trading_platform.scheduler.cycles.run_allocation_rebalance_cycle as rebalance
import autonomous_trading_platform.scheduler.cycles.run_trading_cycle as trading
from autonomous_trading_platform.execution.services.portfolio_drawdown_governance_service import (
    fetch_broker_equity,
)


class _FakeGovernanceService:
    """Records the equity the guard evaluates; never blocks."""

    seen_equity: list[float] = []

    def __init__(self, **kwargs) -> None:
        pass

    def evaluate(self, *, current_equity: float, config):
        type(self).seen_equity.append(current_equity)
        return SimpleNamespace(
            rebalancing_blocked=False,
            trading_blocked=False,
            drawdown_pct=0.0,
            threshold_pct=0.15,
            triggered_action=None,
        )

    def is_rebalancing_blocked(self) -> bool:
        return False

    def is_trading_blocked(self) -> bool:
        return False


def _patch(monkeypatch, module, *, ledger_equity: str) -> None:
    class FakeCashSnapshotRepository:
        def __init__(self, session) -> None:
            pass

        def get_latest(self):
            return SimpleNamespace(equity=Decimal(ledger_equity))

    class FakeOperatorSettingsRepository:
        def __init__(self, session) -> None:
            pass

        def get_or_create_default(self):
            return object()

    _FakeGovernanceService.seen_equity = []
    monkeypatch.setattr(module, "CashSnapshotRepository", FakeCashSnapshotRepository)
    monkeypatch.setattr(module, "PortfolioDrawdownGovernanceService", _FakeGovernanceService)
    monkeypatch.setattr(module, "PortfolioDrawdownGovernanceRepository", lambda session: None)
    monkeypatch.setattr(module, "AuditLogRepository", lambda session: None)
    if hasattr(module, "OperatorSettingsRepository"):
        monkeypatch.setattr(module, "OperatorSettingsRepository", FakeOperatorSettingsRepository)


def _broker(equity):
    return SimpleNamespace(get_account=lambda: {"equity": equity})


def _failing_broker():
    def _boom():
        raise RuntimeError("broker down")

    return SimpleNamespace(get_account=_boom)


def test_fetch_broker_equity_reads_the_account() -> None:
    assert fetch_broker_equity(_broker("97238.12")) == 97238.12


@pytest.mark.parametrize("client", [None, object(), _failing_broker(), _broker(None), _broker("0")])
def test_fetch_broker_equity_is_none_when_unusable(client) -> None:
    assert fetch_broker_equity(client) is None


def test_rebalance_guard_measures_broker_equity(monkeypatch) -> None:
    _patch(monkeypatch, rebalance, ledger_equity="121007")

    result = rebalance._evaluate_portfolio_governance_for_rebalance(
        session=object(),
        settings=object(),
        logger=logging.getLogger("test"),
        broker_client=_broker("97238.12"),
    )

    assert result is None
    assert _FakeGovernanceService.seen_equity == [97238.12]


def test_rebalance_guard_falls_back_to_the_ledger_when_the_broker_fails(monkeypatch) -> None:
    _patch(monkeypatch, rebalance, ledger_equity="96513")

    result = rebalance._evaluate_portfolio_governance_for_rebalance(
        session=object(),
        settings=object(),
        logger=logging.getLogger("test"),
        broker_client=_failing_broker(),
    )

    assert result is None
    assert _FakeGovernanceService.seen_equity == [96513.0]


def test_rebalance_guard_uses_the_ledger_without_a_broker_client(monkeypatch) -> None:
    _patch(monkeypatch, rebalance, ledger_equity="96513")

    result = rebalance._evaluate_portfolio_governance_for_rebalance(
        session=object(), settings=object(), logger=logging.getLogger("test")
    )

    assert result is None
    assert _FakeGovernanceService.seen_equity == [96513.0]


def test_trading_guard_measures_broker_equity(monkeypatch) -> None:
    _patch(monkeypatch, trading, ledger_equity="121007")

    result = trading._evaluate_portfolio_governance_for_trading(
        session=object(),
        logger=logging.getLogger("test"),
        component="test",
        broker_client=_broker("97238.12"),
    )

    assert result is None
    assert _FakeGovernanceService.seen_equity == [97238.12]


def test_trading_guard_falls_back_to_the_ledger(monkeypatch) -> None:
    _patch(monkeypatch, trading, ledger_equity="96513")

    result = trading._evaluate_portfolio_governance_for_trading(
        session=object(),
        logger=logging.getLogger("test"),
        component="test",
        broker_client=_broker(None),
    )

    assert result is None
    assert _FakeGovernanceService.seen_equity == [96513.0]
