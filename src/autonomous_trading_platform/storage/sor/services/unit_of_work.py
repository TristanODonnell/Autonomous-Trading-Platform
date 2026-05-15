from typing import Literal

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.broker_account_snapshot_repository import (
    BrokerAccountSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.broker_order_repository import (
    BrokerOrderRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.cash_snapshot_repository import (
    CashSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.checksums_repository import (
    ChecksumsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_repository import (
    CorporateActionRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.experiments_repository import (
    ExperimentsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.feature_dataset_versions_repository import (
    FeatureDatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.fill_repository import FillRepository
from autonomous_trading_platform.storage.sor.repositories.core.ingestion_checkpoints_repository import (
    IngestionCheckpointsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ingestion_runs_repository import (
    IngestionRunsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.market_bar_repository import (
    MarketBarRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.metrics_summary_repository import (
    MetricsSummaryRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.missing_bar_incidents_repository import (
    MissingBarIncidentsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.order_intent_repository import (
    OrderIntentRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.position_snapshot_repository import (
    PositionSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.reconciliation_snapshot_repository import (
    ReconciliationSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.risk_snapshot_repository import (
    RiskSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.run_manifests_repository import (
    RunManifestRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_soak_report_repository import (
    RuntimeSoakReportRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.signals_repository import (
    SignalRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_runtime_state_repository import (
    StrategyRuntimeStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.symbol_date_coverage_repository import (
    SymbolDateCoverageRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.tracked_order_repository import (
    TrackedOrderRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)


class SorUnitOfWork:
    """Coordinates atomic writes across SOR repositories."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.market_bars = MarketBarRepository(session)
        self.corporate_actions = CorporateActionRepository(session)
        self.universe_snapshots = UniverseSnapshotRepository(session)
        self.universe_versions = UniverseVersionRepository(session)
        self.signals = SignalRepository(session)
        self.order_intents = OrderIntentRepository(session)
        self.broker_account_snapshots = BrokerAccountSnapshotRepository(session)
        self.broker_orders = BrokerOrderRepository(session)
        self.fills = FillRepository(session)
        self.position_snapshots = PositionSnapshotRepository(session)
        self.cash_snapshots = CashSnapshotRepository(session)
        self.risk_snapshots = RiskSnapshotRepository(session)
        self.audit_logs = AuditLogRepository(session)
        self.run_manifests = RunManifestRepository(session)
        self.tracked_orders = TrackedOrderRepository(session)
        self.strategy_control_states = StrategyControlStateRepository(session)
        self.strategy_runtime_states = StrategyRuntimeStateRepository(session)
        self.ticker_lifecycles = TickerLifecycleRepository(session)
        self.dataset_versions = DatasetVersionsRepository(session)
        self.missing_bar_incidents = MissingBarIncidentsRepository(session)
        self.symbol_date_coverage = SymbolDateCoverageRepository(session)
        self.ingestion_runs = IngestionRunsRepository(session)
        self.checksums = ChecksumsRepository(session)
        self.ingestion_checkpoints = IngestionCheckpointsRepository(session)
        self.feature_dataset_versions = FeatureDatasetVersionsRepository(session)
        self.experiments_repository = ExperimentsRepository(session)
        self.metrics_summary_repository = MetricsSummaryRepository(session)
        self.reconciliation_snapshots = ReconciliationSnapshotRepository(session)
        self.runtime_soak_reports = RuntimeSoakReportRepository(session)

    def __enter__(self) -> "SorUnitOfWork":
        self._started_transaction = False

        if not self.session.in_transaction():
            self.session.begin()
            self._started_transaction = True

        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()

        return False
