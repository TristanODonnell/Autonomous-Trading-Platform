# autonomous_trading_platform/scheduler/common/trading_cycle_common.py

from __future__ import annotations

import platform
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.contexts.build_execution_context import (
    ExecutionContext,
    build_execution_context,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.portfolio.allocation_provider import IAllocationProvider
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
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.strategy.contexts.build_strategy_runtime_context import (
    StrategyRuntimeContext,
    build_strategy_runtime_context,
)
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from autonomous_trading_platform.universe.services.universe_resolution_service import (
    UniverseResolutionService,
)

logger = get_logger(__name__)

# DB state strings used throughout the codebase (long form)
_PAPER_DB_STATE = "approved_for_paper_trading"
_LIVE_DB_STATE = "approved_for_live_trading"
_DB_STATE_TO_GOVERNANCE: dict[str, GovernanceState] = {
    _PAPER_DB_STATE: GovernanceState.APPROVED_PAPER,
    _LIVE_DB_STATE: GovernanceState.APPROVED_LIVE,
}


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
    portfolio_engine: IAllocationProvider
    active_strategy_id: str
    active_governance_state: GovernanceState


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


def _resolve_active_strategy(
    session: Session,
    settings: Settings,
) -> tuple[BaseStrategy, str, GovernanceState, int]:
    """
    Query governance for the approved strategy appropriate to the current trading
    environment. Returns (strategy_instance, strategy_id, governance_state, warmup_bars).

    Tries each approved strategy in order (most recently updated first, then
    alphabetically for stable tiebreaking). Attempts instantiation via the
    StrategyRegistry; if the type is unknown, falls back to a StubStrategy that
    carries the correct strategy_id so runtime state and signal attribution stay
    consistent with the governance entry.

    Falls back to (StubStrategy(), "baseline_strategy", APPROVED_PAPER, 1) when no
    approved strategy rows exist.
    """
    from sqlalchemy import or_ as _sa_or
    from sqlalchemy import select as _sa_select

    from autonomous_trading_platform.strategy.registry import get_registry

    if settings.trading_environment is TradingEnvironment.LIVE:
        # Live environment: only pick strategies explicitly approved for live.
        state_filter = StrategyGovernance.current_state == _LIVE_DB_STATE
        fallback_governance_state = GovernanceState.APPROVED_LIVE
    else:
        # Paper / backtest: include live-approved strategies too.
        # Strategies that graduate to approved_for_live_trading are the best
        # performers; in a backtest there is no separate live broker, so they
        # should keep executing through the simulated broker rather than falling
        # out of the active pool.
        state_filter = _sa_or(
            StrategyGovernance.current_state == _PAPER_DB_STATE,
            StrategyGovernance.current_state == _LIVE_DB_STATE,
        )
        fallback_governance_state = GovernanceState.APPROVED_PAPER

    gov_rows = list(
        session.scalars(
            _sa_select(StrategyGovernance)
            .where(state_filter)
            .order_by(
                StrategyGovernance.updated_at.desc(),
                StrategyGovernance.strategy_id.asc(),
            )
        ).all()
    )

    if not gov_rows:
        logger.warning(
            "trading_cycle.no_approved_strategy_found",
            extra={
                "target_env": settings.trading_environment.value,
                "live_included": settings.trading_environment is not TradingEnvironment.LIVE,
            },
        )
        return StubStrategy(), "baseline_strategy", GovernanceState.APPROVED_PAPER, 1

    registry = get_registry()
    for gov_row in gov_rows:
        # Derive governance state from the actual row — a graduating strategy
        # may be in approved_for_live_trading while the cycle is paper mode.
        row_governance_state = _DB_STATE_TO_GOVERNANCE.get(
            gov_row.current_state, fallback_governance_state
        )
        config_row = session.get(StrategyConfigs, gov_row.strategy_id)
        if config_row is None:
            continue
        try:
            defn = registry.get_definition(config_row.strategy_type)
            params = {**(defn.default_parameters or {}), **(config_row.config_json or {})}
            strategy = defn.builder(strategy_id=gov_row.strategy_id, params=params)
            warmup_bars = defn.warmup_bars_fn(params)
            logger.info(
                "trading_cycle.active_strategy_resolved",
                extra={
                    "strategy_id": gov_row.strategy_id,
                    "strategy_type": config_row.strategy_type,
                    "governance_state": gov_row.current_state,
                    "warmup_bars": warmup_bars,
                },
            )
            return strategy, gov_row.strategy_id, row_governance_state, warmup_bars
        except KeyError:
            logger.warning(
                "trading_cycle.strategy_type_not_registered_using_stub",
                extra={
                    "strategy_id": gov_row.strategy_id,
                    "strategy_type": config_row.strategy_type,
                },
            )
            return (
                StubStrategy(strategy_id=gov_row.strategy_id),
                gov_row.strategy_id,
                row_governance_state,
                1,
            )
        except Exception as exc:
            logger.warning(
                "trading_cycle.strategy_instantiation_failed_using_stub",
                extra={
                    "strategy_id": gov_row.strategy_id,
                    "strategy_type": config_row.strategy_type,
                    "error": str(exc),
                },
            )
            return (
                StubStrategy(strategy_id=gov_row.strategy_id),
                gov_row.strategy_id,
                row_governance_state,
                1,
            )

    # All rows lacked configs
    logger.warning(
        "trading_cycle.no_strategy_config_found_using_stub",
        extra={
            "fallback_state": str(fallback_governance_state),
            "governance_row_count": len(gov_rows),
        },
    )
    return StubStrategy(), "baseline_strategy", GovernanceState.APPROVED_PAPER, 1


def build_trading_cycle_dependencies(
    broker_client: object | None = None,
    dataset_version_id_override: str | None = None,
) -> TradingCycleDependencies:
    settings = Settings()
    session = get_session()
    audit_logger = AuditLoggingService(session)
    audit_log_repository = AuditLogRepository(session)
    manifest_service = RunManifestService(session)

    environment_safety_policy = EnvironmentSafetyPolicy(settings=settings)

    active_strategy, active_strategy_id, active_governance_state, warmup_bars = (
        _resolve_active_strategy(session=session, settings=settings)
    )
    use_raw = dataset_version_id_override is not None and dataset_version_id_override.startswith(
        "raw_bars_"
    )
    strategy_context = build_strategy_runtime_context(
        session=session,
        strategy=active_strategy,
        dataset_version=dataset_version_id_override or "v1",
        use_raw_bars=use_raw,
        lookback_bars=warmup_bars,
    )

    risk_state_reader = StubRiskStateReader()
    order_activity_reader = StubOrderActivityReader()

    safety_context = build_safety_context(
        settings=settings,
        environment_policy=environment_safety_policy,
        risk_state_reader=risk_state_reader,
        order_activity_reader=order_activity_reader,
        audit_log_repository=audit_log_repository,
        session=session,
    )

    portfolio_engine = _build_portfolio_engine(session=session, settings=settings)

    execution_context = build_execution_context(
        pre_trade_risk_service=safety_context.pre_trade_risk_service,
        audit_log_repository=audit_log_repository,
        alpaca_settings=settings,
        portfolio_engine=portfolio_engine,
        session=session,
        broker_client=broker_client,
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
        active_strategy_id=active_strategy_id,
        active_governance_state=active_governance_state,
    )


def build_trading_run_manifest(
    *,
    run_id,
    now_utc: datetime,
    cycle_start: datetime,
    cycle_end: datetime,
    trading_environment: TradingEnvironment = TradingEnvironment.PAPER,
    created_at: datetime | None = None,
    universe_version_id: str | None = None,
    universe_source: str | None = None,
    universe_member_count: int | None = None,
    strategy_id: str | None = None,
    governance_state: GovernanceState | None = None,
) -> RunManifest:
    run_type = RunType.LIVE if trading_environment is TradingEnvironment.LIVE else RunType.PAPER
    resolved_governance_state = governance_state or GovernanceState.APPROVED_PAPER
    return RunManifest(
        run_id=run_id,
        run_type=run_type,
        created_at=created_at or datetime.now(UTC),
        environment="local",
        broker="alpaca",
        broker_account_id="paper",
        strategy_id=strategy_id or "baseline_strategy",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000.00"),
        interval=BarInterval.FIVE_MIN,
        start_date=cycle_start.date(),
        end_date=cycle_end.date(),
        dataset_version="v1",
        universe_version=universe_version_id or "v1",
        universe_version_id=universe_version_id,
        universe_source=universe_source,
        universe_member_count=universe_member_count,
        git_commit="dev",
        python_version=platform.python_version(),
        notes="5-minute trading cycle",
        price_basis=PriceBasis.ADJUSTED,
        governance_state=resolved_governance_state,
    )


def resolve_trading_universe(
    session,
    now_utc: datetime,
) -> tuple[set[str], str | None, str | None, int | None]:
    """
    Resolve the active universe for a trading cycle.

    Returns (symbols, universe_version_id, universe_source, member_count).
    Raises ``NoActiveUniverseError`` if no active universe exists.
    """

    resolution_service = UniverseResolutionService(UniverseVersionRepository(session))
    resolution_service.assert_active_universe_exists(now_utc)
    active_version = resolution_service.resolve_active(now_utc)
    members = resolution_service.resolve_active_members(now_utc)
    return (
        set(members),
        active_version.universe_version_id,
        active_version.source,
        len(members),
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
