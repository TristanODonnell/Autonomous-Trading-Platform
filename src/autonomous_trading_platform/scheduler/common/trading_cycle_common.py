# autonomous_trading_platform/scheduler/common/trading_cycle_common.py

from __future__ import annotations

import platform
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.contexts.build_execution_context import (
    ExecutionContext,
    build_execution_context,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.portfolio.portfolio_engine import PortfolioEngine
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.safety.contexts.build_safety_context import (
    SafetyContext,
    build_safety_context,
)
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.readers.order_activity_reader import StubOrderActivityReader
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.storage.sor.repositories.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.promotion_rules_repository import (
    PromotionRulesRepository,
)
from autonomous_trading_platform.strategy.contexts.build_strategy_runtime_context import (
    StrategyRuntimeContext,
    build_strategy_runtime_context,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy


@dataclass(slots=True)
class TradingCycleWindow:
    now_utc: datetime
    cycle_start: datetime
    cycle_end: datetime
    ingestion_deadline: datetime


@dataclass(slots=True)
class TradingCycleDependencies:
    session: Session
    settings: Settings
    audit_logger: AuditLoggingService
    manifest_service: RunManifestService
    strategy_context: StrategyRuntimeContext
    safety_context: SafetyContext
    execution_context: ExecutionContext
    portfolio_engine: PortfolioEngine


def floor_to_five_minutes(timestamp: datetime) -> datetime:
    minute = (timestamp.minute // 5) * 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def build_trading_cycle_window(
    now_utc: datetime | None = None,
    ingestion_grace_seconds: int = 60,
) -> TradingCycleWindow:
    resolved_now = now_utc or datetime.now(UTC)
    cycle_end = floor_to_five_minutes(resolved_now)
    cycle_start = cycle_end - timedelta(minutes=5)
    ingestion_deadline = cycle_end + timedelta(seconds=ingestion_grace_seconds)

    return TradingCycleWindow(
        now_utc=resolved_now,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        ingestion_deadline=ingestion_deadline,
    )


def _build_portfolio_engine(session: Session, settings: Settings) -> PortfolioEngine:
    """
    Construct PortfolioEngine with its three repositories.
    initial_capital from settings is the starting total_capital;
    it gets synced to real broker equity at the start of each cycle.
    """
    return PortfolioEngine(
        policies_repo=CapitalAllocationPoliciesRepository(session),
        overrides_repo=AllocationOverridesRepository(session),
        promotion_rules_repo=PromotionRulesRepository(session),
        total_capital=float(settings.initial_capital),
    )


def build_trading_cycle_dependencies() -> TradingCycleDependencies:
    settings = Settings()
    session = get_session()
    audit_logger = AuditLoggingService(session)
    audit_log_repository = AuditLogRepository(session)
    manifest_service = RunManifestService(session)

    environment_safety_policy = EnvironmentSafetyPolicy(settings=settings)

    strategy_stub = StubStrategy()
    strategy_context = build_strategy_runtime_context(session=session, strategy=strategy_stub)

    risk_state_reader = StubRiskStateReader()
    order_activity_reader = StubOrderActivityReader()

    safety_context = build_safety_context(
        settings=settings,
        environment_policy=environment_safety_policy,
        risk_state_reader=risk_state_reader,
        order_activity_reader=order_activity_reader,
        audit_log_repository=audit_log_repository,
    )

    portfolio_engine = _build_portfolio_engine(session=session, settings=settings)

    execution_context = build_execution_context(
        pre_trade_risk_service=safety_context.pre_trade_risk_service,
        audit_log_repository=audit_log_repository,
        alpaca_settings=settings,
        portfolio_engine=portfolio_engine,
        session=session,
    )

    return TradingCycleDependencies(
        session=session,
        settings=settings,
        audit_logger=audit_logger,
        manifest_service=manifest_service,
        strategy_context=strategy_context,
        safety_context=safety_context,
        execution_context=execution_context,
        portfolio_engine=portfolio_engine,
    )


def build_trading_run_manifest(
    *,
    run_id,
    now_utc: datetime,
    cycle_start: datetime,
    cycle_end: datetime,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        run_type=RunType.BACKTEST,
        created_at=now_utc,
        environment="local",
        broker="alpaca",
        broker_account_id="paper",
        strategy_id="baseline_strategy",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000.00"),
        interval=BarInterval.FIVE_MIN,
        start_date=cycle_start.date(),
        end_date=cycle_end.date(),
        dataset_version="v1",
        universe_version="v1",
        git_commit="dev",
        python_version=platform.python_version(),
        notes="5-minute trading cycle",
        price_basis=PriceBasis.ADJUSTED,
        governance_state=GovernanceState.APPROVED_PAPER,
    )


def build_trading_base_metadata(
    *,
    cycle_start: datetime,
    cycle_end: datetime,
    expected_symbols: set[str],
    manifest: RunManifest,
) -> dict[str, object]:
    return {
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "expected_symbols": sorted(expected_symbols),
        "manifest_run_type": manifest.run_type.value,
        "manifest_interval": manifest.interval.value,
    }


def new_trading_run_id():
    return uuid.uuid4()
