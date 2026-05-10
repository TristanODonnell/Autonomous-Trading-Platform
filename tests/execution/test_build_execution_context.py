from types import SimpleNamespace
from typing import cast

from autonomous_trading_platform.execution.contexts import build_execution_context as module
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine


class FakeBrokerClient:
    def __init__(self, settings) -> None:
        self.settings = settings


class FakeBrokerStartupHealthCheckService:
    calls: list[FakeBrokerClient] = []

    def assert_broker_ready(self, broker_client: FakeBrokerClient):
        self.calls.append(broker_client)
        return SimpleNamespace(ok=True, account_id="paper-1")


class FakeService:
    def __init__(self, *args, **kwargs) -> None:
        pass


class FakeExecutionContext:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_build_execution_context_runs_broker_startup_health_check(monkeypatch) -> None:
    FakeBrokerStartupHealthCheckService.calls.clear()
    monkeypatch.setattr(module, "AlpacaBrokerClient", FakeBrokerClient)
    monkeypatch.setattr(
        module,
        "BrokerStartupHealthCheckService",
        FakeBrokerStartupHealthCheckService,
    )
    monkeypatch.setattr(module, "AlpacaBrokerAdapter", FakeService)
    monkeypatch.setattr(module, "OrderExecutionService", FakeService)
    monkeypatch.setattr(module, "OrderStateMachineService", FakeService)
    monkeypatch.setattr(module, "StrategyStateMachineService", FakeService)
    monkeypatch.setattr(module, "PositionSizer", FakeService)
    monkeypatch.setattr(module, "VolatilityScalingService", FakeService)
    monkeypatch.setattr(module, "PortfolioConstructionService", FakeService)
    monkeypatch.setattr(module, "FillQualityMetricsRepository", FakeService)
    monkeypatch.setattr(module, "RealisedSlippageService", FakeService)
    monkeypatch.setattr(module, "ExecutionPolicyEngine", FakeService)
    monkeypatch.setattr(module, "OrderTypeResolver", FakeService)
    monkeypatch.setattr(module, "TWAPSlicer", FakeService)
    monkeypatch.setattr(module, "VWAPLiteSlicer", FakeService)
    monkeypatch.setattr(module, "BrokerOrderMapper", FakeService)
    monkeypatch.setattr(module, "OrderReconciliationService", FakeService)
    monkeypatch.setattr(module, "OrderRuntimeStateService", FakeService)
    monkeypatch.setattr(module, "StrategyRuntimeStateService", FakeService)
    monkeypatch.setattr(module, "PositionLedgerService", FakeService)
    monkeypatch.setattr(module, "CashLedgerService", FakeService)
    monkeypatch.setattr(module, "PostFillAccountingService", FakeService)
    monkeypatch.setattr(module, "RiskSnapshotService", FakeService)
    monkeypatch.setattr(module, "ExecutionContext", FakeExecutionContext)

    settings = SimpleNamespace(max_symbol_exposure=1000)
    context = module.build_execution_context(
        session=object(),
        pre_trade_risk_service=object(),
        audit_log_repository=object(),
        alpaca_settings=settings,
        portfolio_engine=cast(PortfolioEngine, object()),
    )

    assert isinstance(context, FakeExecutionContext)
    assert len(FakeBrokerStartupHealthCheckService.calls) == 1
    assert FakeBrokerStartupHealthCheckService.calls[0].settings is settings
