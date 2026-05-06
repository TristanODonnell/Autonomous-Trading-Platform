from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import pyarrow as pa
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    OrderSource,
    PriceBasis,
)
from autonomous_trading_platform.execution.services.strategy_state_machine_service import (
    StrategyState,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
    FEATURE_RETURNS_DATASET,
)
from autonomous_trading_platform.storage.parquet.paths import dataset_version_root
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
    StrategyRuntimeState,
)
from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import (
    SymbolDateCoverage,
)

PAPER_CYCLE_NOW_UTC = datetime(2025, 2, 14, 21, 0, tzinfo=UTC)
PAPER_CYCLE_DATASET_VERSION = "v1"
PAPER_CYCLE_FEATURE_DATASET_VERSION = "task_354_spy_returns_v1"
PAPER_CYCLE_ACCOUNT_ID = "paper"
PAPER_CYCLE_SYMBOL = "SPY"
PAPER_CYCLE_STARTING_CASH = Decimal("100000.00")


@dataclass(frozen=True)
class SeededPaperTradingCycleFixture:
    now_utc: datetime
    dataset_version: str
    feature_dataset_version: str
    account_id: str
    symbol: str
    starting_cash: Decimal
    starting_position: Decimal
    data_root: Path
    strategy_ids: tuple[str, ...]


class FakePaperBrokerClient:
    def __init__(self, _settings: Any) -> None:
        self.submitted_orders: list[dict[str, Any]] = []

    def get_account(self) -> dict[str, str]:
        return {
            "id": PAPER_CYCLE_ACCOUNT_ID,
            "equity": str(PAPER_CYCLE_STARTING_CASH),
            "cash": str(PAPER_CYCLE_STARTING_CASH),
            "buying_power": str(PAPER_CYCLE_STARTING_CASH),
            "portfolio_value": str(PAPER_CYCLE_STARTING_CASH),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        return []

    def get_latest_trades(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        return {
            symbol: {"p": 100.0, "s": 100, "t": PAPER_CYCLE_NOW_UTC.isoformat()}
            for symbol in symbols
        }

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted_orders.append(payload)
        broker_order_id = f"paper-{len(self.submitted_orders):06d}"
        return {
            "id": broker_order_id,
            "client_order_id": payload.get("client_order_id", broker_order_id),
            "symbol": payload.get("symbol", PAPER_CYCLE_SYMBOL),
            "qty": str(payload.get("qty", "0")),
            "side": payload.get("side", "buy"),
            "type": payload.get("type", "market"),
            "time_in_force": payload.get("time_in_force", "day"),
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
            "submitted_at": PAPER_CYCLE_NOW_UTC.isoformat(),
            "updated_at": PAPER_CYCLE_NOW_UTC.isoformat(),
        }

    def get_order_by_id(self, order_id: str) -> dict[str, Any]:
        return {
            "id": order_id,
            "client_order_id": order_id,
            "symbol": PAPER_CYCLE_SYMBOL,
            "qty": "0",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
            "submitted_at": PAPER_CYCLE_NOW_UTC.isoformat(),
            "updated_at": PAPER_CYCLE_NOW_UTC.isoformat(),
        }

    def list_open_orders(self) -> list[dict[str, Any]]:
        return []

    def cancel_order(self, _order_id: str) -> None:
        return None

    def close(self) -> None:
        return None


def seed_paper_trading_cycle_fixture(
    *,
    session: Session,
    data_root: Path,
    monkeypatch,
) -> SeededPaperTradingCycleFixture:
    _seed_environment(monkeypatch)
    _patch_runtime_seams(session=session, monkeypatch=monkeypatch)
    _write_adjusted_spy_bars(data_root=data_root)
    _write_spy_feature_dataset(data_root=data_root)
    _seed_database(session=session, data_root=data_root)

    return SeededPaperTradingCycleFixture(
        now_utc=PAPER_CYCLE_NOW_UTC,
        dataset_version=PAPER_CYCLE_DATASET_VERSION,
        feature_dataset_version=PAPER_CYCLE_FEATURE_DATASET_VERSION,
        account_id=PAPER_CYCLE_ACCOUNT_ID,
        symbol=PAPER_CYCLE_SYMBOL,
        starting_cash=PAPER_CYCLE_STARTING_CASH,
        starting_position=Decimal("0"),
        data_root=data_root,
        strategy_ids=("stub_strategy_v1", "baseline_strategy"),
    )


def _seed_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.setenv("NO_LIVE_TRADING", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "false")
    monkeypatch.setenv("SHADOW_MODE_ENABLED", "false")
    monkeypatch.setenv("PAPER_ALLOWED_ACCOUNT_IDS", PAPER_CYCLE_ACCOUNT_ID)
    monkeypatch.setenv("PAPER_BROKER_API_KEY", "paper-test-key")
    monkeypatch.setenv("PAPER_BROKER_API_SECRET", "paper-test-secret")
    monkeypatch.setenv("INITIAL_CAPITAL", str(PAPER_CYCLE_STARTING_CASH))
    monkeypatch.setenv("SKIP_EVALUATION_ON_INGESTION_FAILURE", "true")
    monkeypatch.setenv("HOLD_POSITIONS_ON_EVALUATION_FAILURE", "true")
    monkeypatch.setenv("FREEZE_TRADING_ON_RECONCILIATION_FAILURE", "true")


def _patch_runtime_seams(*, session: Session, monkeypatch) -> None:
    import autonomous_trading_platform.execution.contexts.build_execution_context as execution_builder
    import autonomous_trading_platform.scheduler.common.trading_cycle_common as trading_common
    import autonomous_trading_platform.strategy.contexts.build_strategy_runtime_context as strategy_builder

    monkeypatch.setattr(trading_common, "get_session", lambda: session)
    monkeypatch.setattr(execution_builder, "AlpacaBrokerClient", FakePaperBrokerClient)
    monkeypatch.setattr(
        strategy_builder.SqlAlchemyUniverseMembershipReader,
        "get_symbols_for_timestamp",
        lambda _self, _as_of: [PAPER_CYCLE_SYMBOL],
    )


def _write_adjusted_spy_bars(*, data_root: Path) -> None:
    root = dataset_version_root(data_root, ADJUSTED_BARS_DATASET, PAPER_CYCLE_DATASET_VERSION)
    if root.exists():
        return

    bar_count = 301
    first_timestamp = PAPER_CYCLE_NOW_UTC - timedelta(minutes=5 * bar_count)
    timestamps = [first_timestamp + timedelta(minutes=5 * index) for index in range(bar_count)]

    table = pa.table(
        {
            "bar_id": [
                f"{PAPER_CYCLE_SYMBOL}-{timestamp.isoformat()}-{PAPER_CYCLE_DATASET_VERSION}"
                for timestamp in timestamps
            ],
            "timestamp": timestamps,
            "end_timestamp": [timestamp + timedelta(minutes=5) for timestamp in timestamps],
            "interval": [BarInterval.FIVE_MIN.value] * bar_count,
            "symbol": [PAPER_CYCLE_SYMBOL] * bar_count,
            "open": [100.0] * bar_count,
            "high": [100.0] * bar_count,
            "low": [100.0] * bar_count,
            "close": [100.0] * bar_count,
            "volume": [1_000_000] * bar_count,
            "vwap": [100.0] * bar_count,
            "trade_count": [100] * bar_count,
            "price_basis": [PriceBasis.ADJUSTED.value] * bar_count,
            "adjustment_factor": [1.0] * bar_count,
            "source": ["task_354_fixture"] * bar_count,
            "ingested_at": [PAPER_CYCLE_NOW_UTC] * bar_count,
            "quality_flags": [[] for _ in range(bar_count)],
        }
    )
    write_table(
        table=table,
        dataset=ADJUSTED_BARS_DATASET,
        base_path=data_root,
        dataset_version=PAPER_CYCLE_DATASET_VERSION,
    )


def _write_spy_feature_dataset(*, data_root: Path) -> None:
    root = dataset_version_root(
        data_root,
        FEATURE_RETURNS_DATASET,
        PAPER_CYCLE_FEATURE_DATASET_VERSION,
    )
    if root.exists():
        return

    feature_timestamp = PAPER_CYCLE_NOW_UTC - timedelta(minutes=5)
    table = pa.table(
        {
            "symbol": [PAPER_CYCLE_SYMBOL],
            "timestamp": [feature_timestamp],
            "date": [feature_timestamp.date()],
            "ret_1d": [0.0],
            "ret_5d": [0.0],
            "ret_20d": [0.0],
            "underlying_dataset_version": [PAPER_CYCLE_DATASET_VERSION],
            "price_basis": [PriceBasis.ADJUSTED.value],
            "year": [f"{feature_timestamp.year:04d}"],
            "month": [f"{feature_timestamp.month:02d}"],
        }
    )
    write_table(
        table=table,
        dataset=FEATURE_RETURNS_DATASET,
        base_path=data_root,
        dataset_version=PAPER_CYCLE_FEATURE_DATASET_VERSION,
    )


def _seed_database(*, session: Session, data_root: Path) -> None:
    _upsert_dataset_metadata(session=session, data_root=data_root)
    _upsert_strategy_config(session=session, strategy_id="stub_strategy_v1")
    _upsert_strategy_config(session=session, strategy_id="baseline_strategy")
    _upsert_account_state(session=session)
    session.flush()


def _upsert_dataset_metadata(*, session: Session, data_root: Path) -> None:
    coverage_date = PAPER_CYCLE_NOW_UTC.date()
    session.merge(
        DatasetVersions(
            dataset_version_id=PAPER_CYCLE_DATASET_VERSION,
            dataset_name=ADJUSTED_BARS_DATASET.dataset_key,
            created_at=PAPER_CYCLE_NOW_UTC,
            source="task_354_fixture",
            price_basis=PriceBasis.ADJUSTED,
            interval=BarInterval.FIVE_MIN,
            schema_version=ADJUSTED_BARS_DATASET.schema_version,
            symbol_coverage=1,
            date_coverage_start=coverage_date - timedelta(days=1),
            date_coverage_end=coverage_date,
            validation_status="validated",
            checksum=_stable_checksum(PAPER_CYCLE_DATASET_VERSION),
            source_manifest={"symbols": [PAPER_CYCLE_SYMBOL]},
            metadata_json={
                "fixture": "task_354",
                "storage_path": str(
                    dataset_version_root(
                        data_root,
                        ADJUSTED_BARS_DATASET,
                        PAPER_CYCLE_DATASET_VERSION,
                    )
                ),
            },
        )
    )
    session.merge(
        FeatureDatasetVersions(
            dataset_version_id=PAPER_CYCLE_FEATURE_DATASET_VERSION,
            feature_name="returns",
            dataset_name=FEATURE_RETURNS_DATASET.dataset_key,
            created_at=PAPER_CYCLE_NOW_UTC,
            schema_version=FEATURE_RETURNS_DATASET.schema_version,
            source_dataset_version=PAPER_CYCLE_DATASET_VERSION,
            underlying_price_basis=PriceBasis.ADJUSTED,
            computation_parameters={"fixture": "task_354"},
            storage_path=str(
                dataset_version_root(
                    data_root,
                    FEATURE_RETURNS_DATASET,
                    PAPER_CYCLE_FEATURE_DATASET_VERSION,
                )
            ),
            symbol_coverage=1,
            date_coverage_start=coverage_date,
            date_coverage_end=coverage_date,
            validation_status="validated",
            checksum=_stable_checksum(PAPER_CYCLE_FEATURE_DATASET_VERSION),
            source_manifest={"source_dataset_version": PAPER_CYCLE_DATASET_VERSION},
            metadata_json={"fixture": "task_354", "symbols": [PAPER_CYCLE_SYMBOL]},
            computation_code_version="task_354_fixture",
        )
    )
    session.merge(
        SymbolDateCoverage(
            coverage_id=f"{PAPER_CYCLE_DATASET_VERSION}:{PAPER_CYCLE_SYMBOL}:{coverage_date.isoformat()}",
            symbol=PAPER_CYCLE_SYMBOL,
            date=coverage_date,
            dataset_version=PAPER_CYCLE_DATASET_VERSION,
            expected_bar_count=1,
            actual_bar_count=1,
            completeness_status="complete",
            gap_summary=None,
            updated_at=PAPER_CYCLE_NOW_UTC,
        )
    )


def _upsert_strategy_config(*, session: Session, strategy_id: str) -> None:
    config_json = {
        "strategy_type": "stub_zero_order",
        "symbol": PAPER_CYCLE_SYMBOL,
        "price_change_threshold": 0.0,
        "expected_order_count": 0,
    }
    config_hash = hashlib.sha256(
        f"{strategy_id}:{json.dumps(config_json, sort_keys=True, separators=(',', ':'))}".encode()
    ).hexdigest()

    session.merge(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=config_hash,
            config_json=config_json,
            created_at=PAPER_CYCLE_NOW_UTC,
            strategy_type="stub",
            metadata_json={"fixture": "task_354", "mode": "paper"},
        )
    )
    session.merge(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=config_hash,
            current_state="approved_for_paper_trading",
            experiment_id="task_354_seeded_fixture",
            source_run_id=None,
            submitted_at=PAPER_CYCLE_NOW_UTC,
            updated_at=PAPER_CYCLE_NOW_UTC,
            submitted_by="task_354_fixture",
        )
    )
    session.merge(
        StrategyRuntimeState(
            strategy_id=strategy_id,
            current_state=StrategyState.IDLE,
            last_signal_at=None,
            last_transition_at=PAPER_CYCLE_NOW_UTC,
            cooldown_until=None,
            updated_at=PAPER_CYCLE_NOW_UTC,
            last_evaluated_bar_timestamp=None,
        )
    )


def _upsert_account_state(*, session: Session) -> None:
    run_id = _fixture_uuid("paper-cycle-account-state")
    position_snapshot_id = _fixture_uuid("paper-cycle-position-snapshot")
    session.merge(
        CashSnapshot(
            snapshot_id=_fixture_uuid("paper-cycle-cash-snapshot"),
            run_id=run_id,
            timestamp=PAPER_CYCLE_NOW_UTC,
            currency="USD",
            cash=PAPER_CYCLE_STARTING_CASH,
            buying_power=PAPER_CYCLE_STARTING_CASH,
            reserved_cash=Decimal("0.00"),
            equity=PAPER_CYCLE_STARTING_CASH,
            source=OrderSource.BROKER_RECONCILED,
            capital_bucket=PAPER_CYCLE_STARTING_CASH,
        )
    )
    position_snapshot = PositionSnapshot(
        snapshot_id=position_snapshot_id,
        run_id=run_id,
        timestamp=PAPER_CYCLE_NOW_UTC,
        source=OrderSource.BROKER_RECONCILED,
    )
    position_snapshot.positions = [
        PositionSnapshotItem(
            snapshot_id=position_snapshot_id,
            symbol=PAPER_CYCLE_SYMBOL,
            quantity=Decimal("0"),
            avg_cost=Decimal("0.00"),
            market_price=Decimal("100.00"),
            market_value=Decimal("0.00"),
            unrealized_pnl=Decimal("0.00"),
        )
    ]
    session.merge(position_snapshot)


def _fixture_uuid(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"task-354:{name}")


def _stable_checksum(value: str) -> str:
    return hashlib.sha256(f"task-354:{value}".encode()).hexdigest()
