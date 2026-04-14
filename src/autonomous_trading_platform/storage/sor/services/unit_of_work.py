from typing import Literal

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.broker_order_repository import (
    BrokerOrderRepository,
)
from autonomous_trading_platform.storage.sor.repositories.cash_snapshot_repository import (
    CashSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.checksums_repository import (
    ChecksumsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.corporate_action_repository import (
    CorporateActionRepository,
)
from autonomous_trading_platform.storage.sor.repositories.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.fill_repository import FillRepository
from autonomous_trading_platform.storage.sor.repositories.ingestion_runs_repository import (
    IngestionRunsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.market_bar_repository import (
    MarketBarRepository,
)
from autonomous_trading_platform.storage.sor.repositories.missing_bar_incidents_repository import (
    MissingBarIncidentsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.order_intent_repository import (
    OrderIntentRepository,
)
from autonomous_trading_platform.storage.sor.repositories.position_snapshot_repository import (
    PositionSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.risk_snapshot_repository import (
    RiskSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.run_manifests_repository import (
    RunManifestRepository,
)
from autonomous_trading_platform.storage.sor.repositories.signals_repository import SignalRepository
from autonomous_trading_platform.storage.sor.repositories.strategy_runtime_state_repository import (
    StrategyRuntimeStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.symbol_date_coverage_repository import (
    SymbolDateCoverageRepository,
)
from autonomous_trading_platform.storage.sor.repositories.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.tracked_order_repository import (
    TrackedOrderRepository,
)
from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)


class SorUnitOfWork:
    """Coordinates atomic writes across SOR repositories."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.market_bars = MarketBarRepository(session)
        self.corporate_actions = CorporateActionRepository(session)
        self.universe_snapshots = UniverseSnapshotRepository(session)
        self.signals = SignalRepository(session)
        self.order_intents = OrderIntentRepository(session)
        self.broker_orders = BrokerOrderRepository(session)
        self.fills = FillRepository(session)
        self.position_snapshots = PositionSnapshotRepository(session)
        self.cash_snapshots = CashSnapshotRepository(session)
        self.risk_snapshots = RiskSnapshotRepository(session)
        self.audit_logs = AuditLogRepository(session)
        self.run_manifests = RunManifestRepository(session)
        self.tracked_orders = TrackedOrderRepository(session)
        self.strategy_runtime_states = StrategyRuntimeStateRepository(session)
        self.ticker_lifecycles = TickerLifecycleRepository(session)
        self.dataset_versions = DatasetVersionsRepository(session)
        self.missing_bar_incidents = MissingBarIncidentsRepository(session)
        self.symbol_date_coverage = SymbolDateCoverageRepository(session)
        self.ingestion_runs = IngestionRunsRepository(session)
        self.checksums = ChecksumsRepository(session)

    def __enter__(self) -> "SorUnitOfWork":
        self._started_transaction = False

        if not self.session.in_transaction():
            self.session.begin()
            self._started_transaction = True

        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        if self._started_transaction:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        return False
