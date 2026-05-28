"""
BacktestTradingCycleOrchestrator

Full-fidelity historical backtest that mirrors the real trading pipeline:

  1. run_market_backfill_cycle   — fetches raw bars from Alpaca (replaces live ingestion)
  2. run_feature_pipeline_cycle  — computes features on those bars (same as real)
  3. For each bar timestamp in [start, end]:
       a. run_trading_evaluation_job — real strategy evaluation (reads features from parquet)
       b. BacktestFillSimulator      — synthetic fills at close price (replaces Alpaca order submission)
       c. write position/cash snapshots to SOR
       d. run_risk_snapshot_job      — same as real

The only substitution vs live trading is (b): instead of submitting orders to Alpaca,
fills are created instantly at the bar close price so Portfolio/Dashboard pages reflect
a realistic equity curve.
"""

from __future__ import annotations

import contextlib
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import (
    LiquiditySide,
    OrderSource,
    PriceBasis,
    Side,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.execution.contexts.build_execution_context import (
    build_execution_context,
)
from autonomous_trading_platform.portfolio.simulation_allocation_provider import (
    SimulationAllocationProvider,
    snapshot_allocation_config,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from autonomous_trading_platform.safety.contexts.build_safety_context import build_safety_context
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.readers.order_activity_reader import StubOrderActivityReader
from autonomous_trading_platform.safety.readers.risk_state_reader import StubRiskStateReader
from autonomous_trading_platform.scheduler.backtest.backtest_broker_client import (
    BacktestBrokerClient,
)
from autonomous_trading_platform.scheduler.backtest.backtest_config import BacktestConfig
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    TradingCycleDependencies,
    build_trading_run_manifest,
    new_trading_run_id,
)
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.scheduler.cycles.run_market_backfill_cycle import (
    run_market_backfill_cycle,
)
from autonomous_trading_platform.scheduler.jobs.run_risk_snapshot_job import run_risk_snapshot_job
from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
    run_trading_evaluation_job,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.capital_allocation_policies_repository import (
    CapitalAllocationPoliciesRepository,
)
from autonomous_trading_platform.strategy.contexts.build_strategy_runtime_context import (
    build_strategy_runtime_context,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy


@dataclass
class BacktestTradingCycleResult:
    run_id: str
    bars_processed: int
    fills_created: int
    snapshots_written: int
    final_equity: Decimal
    final_cash: Decimal


def _latest_backfill_raw_dataset(session: Session) -> DatasetVersions:
    row = (
        session.query(DatasetVersions)
        .filter(DatasetVersions.dataset_name == "raw_bars")
        .filter(DatasetVersions.validation_status == "validated")
        .filter(DatasetVersions.metadata_json["dataset_type"].as_string() == "historical_backfill")
        .order_by(DatasetVersions.created_at.desc())
        .first()
    )
    if not isinstance(row, DatasetVersions):
        raise RuntimeError("No validated historical raw_bars backfill dataset found")
    return row


def _build_backtest_dependencies(
    *,
    session: Session,
    settings: Settings,
    broker_client: BacktestBrokerClient,
    raw_dataset_version_id: str,
    run_id: uuid.UUID,
    allocation_provider: SimulationAllocationProvider,
) -> TradingCycleDependencies:
    audit_logger = AuditLoggingService(session)
    audit_log_repository = AuditLogRepository(session)
    manifest_service = RunManifestService(session)

    environment_safety_policy = EnvironmentSafetyPolicy(settings=settings)
    # Use a run-scoped strategy_id so stale checkpoints from prior runs don't block evaluation
    strategy_context = build_strategy_runtime_context(
        session=session,
        strategy=StubStrategy(strategy_id=f"backtest_{run_id}"),
        fallback_dataset=RAW_BARS_DATASET,
        fallback_dataset_version=raw_dataset_version_id,
    )

    safety_context = build_safety_context(
        settings=settings,
        environment_policy=environment_safety_policy,
        risk_state_reader=StubRiskStateReader(),
        order_activity_reader=StubOrderActivityReader(),
        audit_log_repository=audit_log_repository,
        session=session,
    )

    # Update live capital from current broker equity — policy params stay pinned (FINDING-11)
    allocation_provider.update_total_capital(float(broker_client.portfolio_equity()))

    execution_context = build_execution_context(
        pre_trade_risk_service=safety_context.pre_trade_risk_service,
        audit_log_repository=audit_log_repository,
        alpaca_settings=settings,
        portfolio_engine=allocation_provider,
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
        portfolio_engine=allocation_provider,
    )


class BacktestTradingCycleOrchestrator:
    """
    Full-fidelity backtest: backfill → features → [evaluation → simulated fills → snapshots] per bar.
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config

    def run(self, *, progress: bool = True) -> BacktestTradingCycleResult:
        cfg = self._config
        run_id = new_trading_run_id()

        start_dt = datetime(
            cfg.start_date.year, cfg.start_date.month, cfg.start_date.day, tzinfo=UTC
        )
        end_dt = datetime(
            cfg.end_date.year, cfg.end_date.month, cfg.end_date.day, 23, 59, 59, tzinfo=UTC
        )

        # ── Phase 1: Backfill raw bars ──────────────────────────────────
        if progress:
            print(
                f"[Backtest] Phase 1/3: Backfilling bars for {cfg.symbols}  "
                f"{cfg.start_date} → {cfg.end_date}"
            )
        session = get_session()
        try:
            run_market_backfill_cycle(
                symbols=cfg.symbols,
                start=start_dt,
                end=end_dt,
            )

            # ── Phase 2: Feature pipeline ───────────────────────────────
            if progress:
                print("[Backtest] Phase 2/3: Running feature pipeline")
            raw_dataset = _latest_backfill_raw_dataset(session)
            source_manifest = raw_dataset.source_manifest or {}
            raw_symbols = sorted(
                s for s in source_manifest.get("symbols", cfg.symbols) if isinstance(s, str)
            )
            run_feature_pipeline_cycle(
                now_utc=datetime.now(UTC),
                price_basis=PriceBasis.RAW,
                dataset_version_id=raw_dataset.dataset_version_id,
                symbols=raw_symbols,
                start_date=raw_dataset.date_coverage_start,
                end_date=raw_dataset.date_coverage_end,
                include_returns=True,
                include_volatility=True,
                include_moving_average=True,
                include_liquidity=True,
                include_regime=True,
            )

            # ── Phase 3: Bar-by-bar replay ──────────────────────────────
            if progress:
                print("[Backtest] Phase 3/3: Replaying trading cycle bar-by-bar")

            bar_reader = HistoricalBarDatasetReader(session=session, base_path="data")
            bars_by_ts: dict[datetime, dict[str, Decimal]] = defaultdict(dict)
            for sym in cfg.symbols:
                table = bar_reader.read(
                    dataset=RAW_BARS_DATASET,
                    dataset_version=str(raw_dataset.dataset_version_id),
                    symbol=sym,
                    start_date=cfg.start_date,
                    end_date=cfg.end_date,
                )
                for row in table.to_pylist():
                    ts_val = row["timestamp"]
                    if not isinstance(ts_val, datetime):
                        ts_val = datetime.fromisoformat(str(ts_val)).replace(tzinfo=UTC)
                    elif ts_val.tzinfo is None:
                        ts_val = ts_val.replace(tzinfo=UTC)
                    bars_by_ts[ts_val][sym] = Decimal(str(row["close"]))

            if not bars_by_ts:
                print("  No bars found after backfill — check symbols/dates")
                return BacktestTradingCycleResult(
                    run_id=str(run_id),
                    bars_processed=0,
                    fills_created=0,
                    snapshots_written=0,
                    final_equity=cfg.initial_capital,
                    final_cash=cfg.initial_capital,
                )

            sorted_timestamps = sorted(bars_by_ts.keys())

            settings = Settings()
            broker_client = BacktestBrokerClient(config=cfg)

            # ── FINDING-11: Snapshot allocation config once before the bar loop ─
            # Reads CapitalAllocationPolicies + AllocationOverrides from DB exactly once.
            # The pinned AllocationConfig is immutable for the full simulation run so
            # production policy edits after this point cannot change backtest behavior.
            allocation_config = snapshot_allocation_config(
                policies_repo=CapitalAllocationPoliciesRepository(session),
                overrides_repo=AllocationOverridesRepository(session),
                total_capital=float(cfg.initial_capital),
                strategy_ids=[cfg.strategy_id] if cfg.strategy_id else None,
            )
            allocation_provider = SimulationAllocationProvider(allocation_config)
            if progress:
                print(
                    f"[Backtest] Allocation config: hash={allocation_config.allocation_config_hash}"
                    f"  strategies={len(allocation_config.strategy_entries)}"
                    f"  default_pct={allocation_config.default_max_pct_of_capital:.1%}"
                )

            fills_created = 0
            snapshots_written = 0
            bars_processed = 0
            current_day = None

            for ts in sorted_timestamps:
                close_prices = bars_by_ts[ts]

                # Refresh broker client with current bar prices
                broker_client.set_current_prices(close_prices)

                # Build fresh deps each bar so allocation_provider tracks current equity.
                # The pinned AllocationConfig (policies, percentages) stays frozen — only
                # total_capital is updated inside _build_backtest_dependencies (FINDING-11).
                deps = _build_backtest_dependencies(
                    session=session,
                    settings=settings,
                    broker_client=broker_client,
                    raw_dataset_version_id=str(raw_dataset.dataset_version_id),
                    run_id=run_id,
                    allocation_provider=allocation_provider,
                )

                manifest = build_trading_run_manifest(
                    run_id=run_id,
                    now_utc=ts,
                    cycle_start=ts,
                    cycle_end=ts,
                )

                # Trading evaluation — real strategy, reads features from parquet
                try:
                    _, intents = run_trading_evaluation_job(
                        now_utc=ts,
                        trading_cycle_dependencies=deps,
                        manifest=manifest,
                    )
                    intents = list(intents)
                except Exception as exc:
                    if progress:
                        print(f"  [{ts.date()}] Evaluation skipped: {exc}")
                    bars_processed += 1
                    continue

                # Simulate fills for each intent
                for intent in intents:
                    price = close_prices.get(intent.symbol)
                    if price is None:
                        continue

                    qty = self._resolve_qty(intent, price)
                    if qty is None or qty <= 0:
                        continue

                    slippage = cfg.slippage_rate
                    if intent.side == Side.BUY:
                        fill_price = price * (1 + slippage)
                    else:
                        fill_price = price * (1 - slippage)

                    fees = fill_price * qty * cfg.fee_rate

                    # Check affordability for buys
                    if intent.side == Side.BUY and (fill_price * qty + fees) > broker_client.cash:
                        continue

                    broker_client.apply_fill(
                        symbol=intent.symbol,
                        side=intent.side.value,
                        qty=qty,
                        price=fill_price,
                        fees=fees,
                    )

                    fill_id = f"bt_{run_id}_{intent.symbol}_{intent.side.value}_{ts.isoformat()}"
                    session.add(
                        Fill(
                            fill_id=fill_id[:64],
                            broker_order_id=fill_id[:64],
                            intent_id=intent.intent_id,
                            run_id=run_id,
                            timestamp=ts,
                            symbol=intent.symbol,
                            side=intent.side,
                            quantity=qty,
                            price=fill_price,
                            fees=fees,
                            liquidity=LiquiditySide.TAKER,
                            venue="backtest",
                            meta={"source": "backtest_replay", "strategy_id": cfg.strategy_id},
                        )
                    )
                    fills_created += 1

                # Write end-of-day position/cash snapshots
                day = ts.date()
                if current_day != day:
                    if current_day is not None:
                        self._write_snapshots(
                            session=session,
                            run_id=run_id,
                            ts=ts,
                            broker_client=broker_client,
                            current_prices=close_prices,
                        )
                        snapshots_written += 1

                        # Risk snapshot — reads the position snapshot we just wrote
                        with contextlib.suppress(Exception):
                            run_risk_snapshot_job(
                                now_utc=ts,
                                trading_cycle_dependencies=deps,
                                run_id=run_id,
                            )

                        if progress:
                            equity = broker_client.portfolio_equity()
                            print(
                                f"  {current_day}  equity=${float(equity):>12,.2f}  "
                                f"cash=${float(broker_client.cash):>12,.2f}  "
                                f"positions={len(broker_client.positions)}  "
                                f"fills={fills_created}"
                            )

                    current_day = day

                bars_processed += 1

            # Final snapshot
            if current_day is not None and sorted_timestamps:
                last_prices = bars_by_ts[sorted_timestamps[-1]]
                broker_client.set_current_prices(last_prices)
                self._write_snapshots(
                    session=session,
                    run_id=run_id,
                    ts=sorted_timestamps[-1],
                    broker_client=broker_client,
                    current_prices=last_prices,
                )
                snapshots_written += 1

            session.commit()

            return BacktestTradingCycleResult(
                run_id=str(run_id),
                bars_processed=bars_processed,
                fills_created=fills_created,
                snapshots_written=snapshots_written,
                final_equity=broker_client.portfolio_equity(),
                final_cash=broker_client.cash,
            )
        finally:
            session.close()

    def _resolve_qty(self, intent, price: Decimal) -> Decimal | None:
        if intent.qty is not None:
            return Decimal(str(intent.qty))
        if intent.notional is not None:
            return (Decimal(str(intent.notional)) / price).to_integral_value(rounding=ROUND_DOWN)
        return None

    def _write_snapshots(
        self,
        *,
        session: Session,
        run_id: uuid.UUID,
        ts: datetime,
        broker_client: BacktestBrokerClient,
        current_prices: dict[str, Decimal],
    ) -> None:
        pos_snapshot = PositionSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=run_id,
            timestamp=ts,
            source=OrderSource.BACKTEST,
        )
        session.add(pos_snapshot)
        session.flush()

        for symbol, qty in broker_client.positions.items():
            if qty <= 0:
                continue
            price = current_prices.get(symbol, Decimal("0"))
            avg_cost = broker_client.avg_costs.get(symbol, Decimal("0"))
            market_value = qty * price
            session.add(
                PositionSnapshotItem(
                    snapshot_id=pos_snapshot.snapshot_id,
                    symbol=symbol,
                    quantity=qty,
                    avg_cost=avg_cost,
                    market_price=price,
                    market_value=market_value,
                    unrealized_pnl=market_value - avg_cost * qty,
                )
            )

        equity = broker_client.portfolio_equity()
        session.add(
            CashSnapshot(
                snapshot_id=uuid.uuid4(),
                run_id=run_id,
                timestamp=ts,
                currency="USD",
                cash=broker_client.cash,
                buying_power=broker_client.cash,
                reserved_cash=Decimal("0"),
                equity=equity,
                source=OrderSource.BACKTEST,
                capital_bucket=self._config.initial_capital,
            )
        )
