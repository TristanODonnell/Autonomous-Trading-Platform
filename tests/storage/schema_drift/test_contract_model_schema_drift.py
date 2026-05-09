from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from autonomous_trading_platform.contracts.accounting.cash_snapshot import (
    CashSnapshot as CashSnapshotContract,
)
from autonomous_trading_platform.contracts.accounting.risk_snapshot import (
    RiskSnapshot as RiskSnapshotContract,
)
from autonomous_trading_platform.contracts.execution.fill_quality_record import (
    FillQualityRecord as FillQualityRecordContract,
)
from autonomous_trading_platform.contracts.governance.strategy_governance import (
    StrategyGovernanceRecord as StrategyGovernanceContract,
)
from autonomous_trading_platform.contracts.market.corporate_action import (
    CorporateAction as CorporateActionContract,
)
from autonomous_trading_platform.contracts.market.market_bar import (
    MarketBar as MarketBarContract,
)
from autonomous_trading_platform.contracts.runtime.audit_log import (
    AuditLogEvent as AuditLogContract,
)
from autonomous_trading_platform.contracts.runtime.dataset_version import (
    DatasetVersion as DatasetVersionContract,
)
from autonomous_trading_platform.contracts.runtime.experiment import (
    Experiment as ExperimentContract,
)
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion as FeatureDatasetVersionContract,
)
from autonomous_trading_platform.contracts.runtime.ingestion_run import (
    IngestionRun as IngestionRunContract,
)
from autonomous_trading_platform.contracts.runtime.metrics_summary import (
    MetricsSummary as MetricsSummaryContract,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import (
    RunManifest as RunManifestContract,
)
from autonomous_trading_platform.contracts.runtime.runtime_job_run import (
    RuntimeJobRun as RuntimeJobRunContract,
)
from autonomous_trading_platform.contracts.runtime.simulation_run import (
    SimulationRun as SimulationRunContract,
)
from autonomous_trading_platform.contracts.runtime.strategy_config import (
    StrategyConfig as StrategyConfigContract,
)
from autonomous_trading_platform.contracts.runtime.ticker_lifecycle_event import (
    TickerLifecycleEvent as TickerLifecycleEventContract,
)
from autonomous_trading_platform.contracts.runtime.universe_snapshot import (
    UniverseSnapshot as UniverseSnapshotContract,
)
from autonomous_trading_platform.contracts.trading.broker_order import (
    BrokerOrder as BrokerOrderContract,
)
from autonomous_trading_platform.contracts.trading.fill import Fill as FillContract
from autonomous_trading_platform.contracts.trading.order_intent import (
    OrderIntent as OrderIntentContract,
)
from autonomous_trading_platform.contracts.trading.signal import Signal as SignalContract
from autonomous_trading_platform.storage.sor.models.audit_logs import (
    AuditLogRow,
)
from autonomous_trading_platform.storage.sor.models.broker_orders import (
    BrokerOrder as BrokerOrderOrm,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import (
    CashSnapshot as CashSnapshotOrm,
)
from autonomous_trading_platform.storage.sor.models.corporate_actions import (
    CorporateAction as CorporateActionOrm,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import (
    DatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.experiments import Experiments
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import (
    FillQualityMetrics,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill as FillOrm
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.models.market_bars import (
    MarketBar as MarketBarOrm,
)
from autonomous_trading_platform.storage.sor.models.metrics_summary import (
    MetricsSummary as MetricsSummaryOrm,
)
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.risk_snapshots import (
    RiskSnapshot as RiskSnapshotOrm,
)
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.signals import Signal as SignalOrm
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.models.strategy_configs import (
    StrategyConfigs,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import (
    StrategyGovernance,
)
from autonomous_trading_platform.storage.sor.models.ticker_lifecycle_event import (
    TickerLifecycleEvent as TickerLifecycleEventOrm,
)
from autonomous_trading_platform.storage.sor.models.universe_snapshots import (
    UniverseSnapshot as UniverseSnapshotOrm,
)

from .schema_drift_helpers import (
    ContractOrmSchemaPair,
    assert_contract_matches_orm,
    assert_orm_matches_database,
)

SCHEMA_PAIRS: tuple[ContractOrmSchemaPair, ...] = (
    ContractOrmSchemaPair(
        contract=CashSnapshotContract,
        orm_model=CashSnapshotOrm,
        expected_fields=tuple(CashSnapshotContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=RiskSnapshotContract,
        orm_model=RiskSnapshotOrm,
        expected_fields=tuple(RiskSnapshotContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=FillQualityRecordContract,
        orm_model=FillQualityMetrics,
        expected_fields=tuple(FillQualityRecordContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=StrategyGovernanceContract,
        orm_model=StrategyGovernance,
        expected_fields=tuple(StrategyGovernanceContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=CorporateActionContract,
        orm_model=CorporateActionOrm,
        expected_fields=tuple(CorporateActionContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=MarketBarContract,
        orm_model=MarketBarOrm,
        expected_fields=tuple(MarketBarContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=AuditLogContract,
        orm_model=AuditLogRow,
        expected_fields=tuple(AuditLogContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=DatasetVersionContract,
        orm_model=DatasetVersions,
        expected_fields=tuple(DatasetVersionContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=ExperimentContract,
        orm_model=Experiments,
        expected_fields=tuple(ExperimentContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=FeatureDatasetVersionContract,
        orm_model=FeatureDatasetVersions,
        expected_fields=tuple(FeatureDatasetVersionContract.__annotations__),
    ),
    ContractOrmSchemaPair(
        contract=IngestionRunContract,
        orm_model=IngestionRuns,
        expected_fields=tuple(IngestionRunContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=MetricsSummaryContract,
        orm_model=MetricsSummaryOrm,
        expected_fields=tuple(MetricsSummaryContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=RunManifestContract,
        orm_model=RunManifestRow,
        expected_fields=tuple(RunManifestContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=RuntimeJobRunContract,
        orm_model=RuntimeJobRuns,
        expected_fields=tuple(RuntimeJobRunContract.__annotations__),
    ),
    ContractOrmSchemaPair(
        contract=SimulationRunContract,
        orm_model=SimulationRuns,
        expected_fields=tuple(SimulationRunContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=StrategyConfigContract,
        orm_model=StrategyConfigs,
        expected_fields=tuple(StrategyConfigContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=TickerLifecycleEventContract,
        orm_model=TickerLifecycleEventOrm,
        expected_fields=tuple(TickerLifecycleEventContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=UniverseSnapshotContract,
        orm_model=UniverseSnapshotOrm,
        expected_fields=tuple(UniverseSnapshotContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=BrokerOrderContract,
        orm_model=BrokerOrderOrm,
        expected_fields=tuple(BrokerOrderContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=FillContract,
        orm_model=FillOrm,
        expected_fields=tuple(FillContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=OrderIntentContract,
        orm_model=OrderIntents,
        expected_fields=tuple(OrderIntentContract.model_fields),
    ),
    ContractOrmSchemaPair(
        contract=SignalContract,
        orm_model=SignalOrm,
        expected_fields=tuple(SignalContract.model_fields),
    ),
)

UNMAPPED_NOT_TESTED: tuple[tuple[str, str], ...] = (
    (
        "contracts.accounting.position_snapshot.PositionSnapshot / Position",
        "Composite persistence shape uses position_snapshots plus position_snapshot_items.",
    ),
    (
        "contracts.common.enums.*",
        "Shared enum definitions, not standalone runtime persistence contracts.",
    ),
    (
        "contracts.common.types.*",
        "Shared scalar aliases/validators, not standalone runtime persistence contracts.",
    ),
    (
        "contracts.execution.execution_policy_config.*",
        "Runtime configuration contracts; no direct SOR ORM model.",
    ),
    (
        "contracts.execution.execution_policy_result.*",
        "Runtime policy result contracts; no direct SOR ORM model.",
    ),
    (
        "contracts.runtime.feature_dataset_audit_record.FeatureDatasetAuditRecord",
        "Audit/view aggregate contract; no direct one-table ORM model.",
    ),
    (
        "contracts.trading.cost_breakdown.CostBreakdown",
        "Value-object contract embedded in execution calculations; no direct ORM model.",
    ),
    (
        "contracts.trading.slippage_measurement.SlippageMeasurement",
        "Value-object contract embedded in execution calculations; no direct ORM model.",
    ),
    (
        "contracts.validators.*",
        "Validation framework/results, not SOR persistence contracts.",
    ),
    (
        "storage.sor.models.allocation_overrides.AllocationOverrides",
        "Operational/governance table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.capital_allocation_policies.CapitalAllocationPolicies",
        "Operational/governance table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.checksums.Checksums",
        "Storage metadata table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.ingestion_checkpoint.IngestionCheckpoint",
        "Checkpoint table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.missing_bar_incidents.MissingBarIncidents",
        "Operational incident table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.operator_settings.OperatorSettingsRow",
        "Application settings table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.promotion_rules.PromotionRules",
        "Governance rule table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.runtime_control_state.RuntimeControlState",
        "Control-plane state table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.strategy_control_states.StrategyControlState",
        "Control-plane state table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.strategy_runtime_states.StrategyRuntimeState",
        "Runtime state table; no matching contract found.",
    ),
    (
        "storage.sor.models.symbol_date_coverage.SymbolDateCoverage",
        "Coverage metadata table; no matching runtime contract found.",
    ),
    (
        "storage.sor.models.tracked_orders.TrackedOrder",
        "Operational order tracking table; no matching runtime contract found.",
    ),
)


def _schema_pair_id(schema_pair: ContractOrmSchemaPair) -> str:
    return f"{schema_pair.contract.__name__}->{schema_pair.orm_model.__tablename__}"


@pytest.mark.parametrize("schema_pair", SCHEMA_PAIRS, ids=_schema_pair_id)
def test_contract_matches_orm_mapping(schema_pair: ContractOrmSchemaPair) -> None:
    assert_contract_matches_orm(schema_pair)


@pytest.fixture(scope="session")
def migrated_postgres_engine() -> Iterator[Engine]:
    database_url = _schema_drift_database_url()
    if database_url is None:
        pytest.skip(
            "Set STORAGE_SCHEMA_DRIFT_DATABASE_URL or a PostgreSQL DATABASE_URL "
            "to run migrated database schema drift checks."
        )
        raise AssertionError("unreachable after pytest.skip")

    os.environ["DATABASE_URL"] = database_url
    alembic_config = Config(str(_repo_root() / "infra" / "db" / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("schema_pair", SCHEMA_PAIRS, ids=_schema_pair_id)
def test_orm_matches_migrated_database_columns(
    schema_pair: ContractOrmSchemaPair,
    migrated_postgres_engine: Engine,
) -> None:
    assert_orm_matches_database(schema_pair, migrated_postgres_engine)


def test_unmapped_contracts_or_models_are_documented() -> None:
    assert UNMAPPED_NOT_TESTED


def _schema_drift_database_url() -> str | None:
    database_url = os.environ.get("STORAGE_SCHEMA_DRIFT_DATABASE_URL") or os.environ.get(
        "DATABASE_URL"
    )
    if database_url is None:
        return None
    if not database_url.startswith(("postgresql://", "postgresql+")):
        return None
    return database_url


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
