"""Research <-> trading-cycle parity harness (rotation step 5c).

One synthetic 5-minute raw-bars dataset feeds both paths:

- research: the production SimulationRunner factory (as research, bench re-sims and the
  rotation export build it), run over the comparison days;
- platform: the trading cycle's own strategy construction and context
  (`_instantiate_strategy` + `build_strategy_runtime_context`), and for trades the real
  `run_trading_cycle` driven tick by tick against the backtest's SimulatedBrokerClient.

Callers chdir into a tmp directory first: every reader resolves the relative "data" root.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pyarrow as pa
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    OrderSource,
    PriceBasis,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.writer import write_table
from autonomous_trading_platform.storage.sor.models.capital_allocation_policies import (
    CapitalAllocationPolicies,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember as UniverseMemberRow,
)
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseVersion as UniverseVersionRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

PARITY_DATASET_VERSION = "raw_bars_parity_v1"
PARITY_SYMBOLS: tuple[str, ...] = ("AAA", "BBB", "CCC")
# Mon-Thu in January (EST: regular session 14:30-21:00 UTC, 78 bars a day).
PARITY_DAYS: tuple[date, ...] = (
    date(2024, 1, 8),
    date(2024, 1, 9),
    date(2024, 1, 10),
    date(2024, 1, 11),
)
# The first three days feed warmup (factor windows of 100 bars need more than a day);
# signals and trades are compared on the last day. Platform ticks are slow (one Parquet
# read per tick and symbol, ~0.3 s per trading cycle on SQLite), so one day only.
COMPARE_START = date(2024, 1, 11)
PARITY_CAPITAL = Decimal("100000")
_SESSION_OPEN_UTC = (14, 30)
_BARS_PER_DAY = 78


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def bar_timestamps(day: date) -> list[datetime]:
    first = datetime(day.year, day.month, day.day, *_SESSION_OPEN_UTC, tzinfo=UTC)
    return [first + timedelta(minutes=5 * i) for i in range(_BARS_PER_DAY)]


def compare_timestamps(start: date = COMPARE_START) -> list[datetime]:
    return [ts for day in PARITY_DAYS if day >= start for ts in bar_timestamps(day)]


def _close(symbol_index: int, i: int) -> float:
    """Deterministic path with fast and slow cycles, so short and long windows both
    cross, z-scores reach their extremes and momentum flips."""
    base = 100.0 + 40.0 * symbol_index
    fast = 0.012 * math.sin(i / (5.0 + symbol_index) + symbol_index)
    slow = 0.03 * math.sin(i / (47.0 + 7 * symbol_index))
    drift = 0.00004 * i * (1 if symbol_index % 2 == 0 else -1)
    return round(base * (1.0 + fast + slow + drift), 4)


def _volume(symbol_index: int, i: int) -> int:
    spike = 3.0 if (i + 11 * symbol_index) % 37 == 0 else 1.0
    return int((40_000 + 25_000 * abs(math.sin(i / 3.0 + symbol_index))) * spike)


def write_parity_bars(data_root: Path) -> None:
    """Raw 5-minute bars for PARITY_SYMBOLS over PARITY_DAYS under data_root."""
    ingested_at = datetime(2024, 2, 1, tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    for symbol_index, symbol in enumerate(PARITY_SYMBOLS):
        i = 0
        previous = _close(symbol_index, -1)
        for day in PARITY_DAYS:
            for ts in bar_timestamps(day):
                close = _close(symbol_index, i)
                open_ = previous
                rows.append(
                    {
                        "bar_id": f"{symbol}-{ts.isoformat()}",
                        "timestamp": ts,
                        "end_timestamp": ts + timedelta(minutes=5),
                        "interval": BarInterval.FIVE_MIN.value,
                        "symbol": symbol,
                        "open": open_,
                        "high": max(open_, close) * 1.0005,
                        "low": min(open_, close) * 0.9995,
                        "close": close,
                        "volume": _volume(symbol_index, i),
                        "vwap": (open_ + close) / 2,
                        "trade_count": 100,
                        "price_basis": PriceBasis.RAW.value,
                        "adjustment_factor": 1.0,
                        "source": "parity_fixture",
                        "ingested_at": ingested_at,
                        "quality_flags": [],
                        "date": day,
                        "year": f"{day.year:04d}",
                        "month": f"{day.month:02d}",
                    }
                )
                previous = close
                i += 1
    schema = RAW_BARS_DATASET.schema
    table = pa.table({f.name: [row[f.name] for row in rows] for f in schema}, schema=schema)
    write_table(
        table=table,
        dataset=RAW_BARS_DATASET,
        base_path=data_root,
        dataset_version=PARITY_DATASET_VERSION,
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParityStrategy:
    strategy_id: str
    strategy_type: str
    parameters: dict[str, Any]

    @property
    def wrapped_config(self) -> dict[str, Any]:
        # Research stores configs wrapped like this; the trading cycle unwraps them.
        return {
            "type": self.strategy_type,
            "parameters": self.parameters,
            "strategy_id": self.strategy_id,
        }


def seed_strategy(session: Session, strategy: ParityStrategy) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    session.merge(
        StrategyConfigs(
            strategy_id=strategy.strategy_id,
            config_hash=f"parity-{strategy.strategy_id}",
            config_json=strategy.wrapped_config,
            created_at=now,
            strategy_type=strategy.strategy_type,
            metadata_json={"fixture": "parity"},
        )
    )
    session.merge(
        StrategyGovernance(
            strategy_id=strategy.strategy_id,
            config_hash=f"parity-{strategy.strategy_id}",
            current_state="approved_for_paper_trading",
            experiment_id="parity_fixture",
            source_run_id=None,
            submitted_at=now,
            updated_at=now,
            submitted_by="parity_fixture",
        )
    )
    session.flush()


def seed_universe(session: Session) -> None:
    version_id = "parity-universe"
    effective_from = datetime(2024, 1, 1, tzinfo=UTC)
    session.add(
        UniverseVersionRow(
            universe_version_id=version_id,
            name="parity_universe",
            source="custom",
            created_at=effective_from,
            effective_from=effective_from,
            effective_to=None,
            status="active",
            rebalance_reason="fixture_seed",
            config_hash="parity-universe",
            generation_metadata_json={"fixture": "parity"},
        )
    )
    for rank, symbol in enumerate(PARITY_SYMBOLS, start=1):
        session.add(
            UniverseMemberRow(
                universe_version_id=version_id,
                symbol=symbol,
                rank=rank,
                score=None,
                included_reason="fixture_seed",
                excluded_reason=None,
                liquidity_metrics_json=None,
                quality_metrics_json=None,
            )
        )
    session.flush()


def seed_platform_state(session: Session, monkeypatch: Any) -> None:
    """Portfolio mode, one paper account and limits loose enough that only sizing and
    fills decide the trades (risk limits are not part of the parity question)."""
    for key, value in {
        "APP_ENV": "test",
        "TRADING_ENVIRONMENT": "paper",
        "NO_LIVE_TRADING": "true",
        "PAPER_ALLOWED_ACCOUNT_IDS": "paper",
        "INITIAL_CAPITAL": str(PARITY_CAPITAL),
        "MAX_SYMBOL_EXPOSURE": "10000000",
        "MAX_GROSS_EXPOSURE": "10000000",
        "MAX_NET_EXPOSURE": "10000000",
        "MAX_DAILY_NOTIONAL_TRADED": "1000000000",
        "MAX_RESERVED_CASH": "10000000",
        "MAX_ORDERS_PER_BAR": "100",
        "MAX_ORDERS_PER_HOUR": "10000",
        "SKIP_EVALUATION_ON_INGESTION_FAILURE": "true",
        "HOLD_POSITIONS_ON_EVALUATION_FAILURE": "true",
    }.items():
        monkeypatch.setenv(key, value)

    import autonomous_trading_platform.scheduler.common.trading_cycle_common as trading_common

    monkeypatch.setattr(trading_common, "get_session", lambda: session)

    now = datetime(2024, 1, 1, tzinfo=UTC)
    OperatorSettingsRepository(session).update_current(
        {
            "portfolio_mode_enabled": True,
            "min_active_strategies": 1,
            "max_active_strategies": 1,
            "max_on_deck_strategies": 0,
            "per_strategy_cap": 1.0,
            "max_total_strategy_allocation_pct": 1.0,
        },
        updated_by="parity_fixture",
    )
    session.merge(
        CapitalAllocationPolicies(
            policy_id="parity-paper-policy",
            approval_status="approved_paper",
            performance_tier=None,
            max_pct_of_capital=1.0,
            max_position_size_usd=None,
            max_drawdown_allowed=0.99,
            is_active=True,
            created_at=now,
            notes="parity fixture",
        )
    )
    session.merge(
        RuntimeControlState(
            control_id="global",
            trading_enabled=True,
            trading_paused=False,
            kill_switch_enabled=False,
            trading_mode="paper",
            reason=None,
            updated_by=None,
            created_at=now,
            updated_at=now,
        )
    )
    session.merge(
        CashSnapshot(
            snapshot_id=uuid4(),
            run_id=uuid4(),
            timestamp=now,
            currency="USD",
            cash=PARITY_CAPITAL,
            buying_power=PARITY_CAPITAL,
            reserved_cash=Decimal("0"),
            equity=PARITY_CAPITAL,
            source=OrderSource.BROKER_RECONCILED,
            capital_bucket=PARITY_CAPITAL,
        )
    )
    seed_universe(session)
    session.flush()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SignalKey = tuple[datetime, str, str]  # (bar timestamp, symbol, direction)
FillKey = tuple[datetime, str, str, int, float]  # (time, symbol, side, qty, price)


def research_run(
    session: Session, strategy: ParityStrategy, *, start: date = COMPARE_START
) -> tuple[set[SignalKey], list[FillKey]]:
    """Signals and fills of one research simulation from start to the last day."""
    from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
        build_simulation_context,
    )
    from autonomous_trading_platform.research.simulation.simulation_runner import (
        SimulationRunRequest,
    )

    # As the research, bench and rotation-export call sites build it.
    runner = build_simulation_context(
        session=session, universe_size=len(PARITY_SYMBOLS)
    ).simulation_runner

    captured: dict[str, Any] = {}
    record_results = runner.result_recorder.record_results

    def _capture(**kwargs: Any) -> Any:
        captured["signal_log"] = kwargs["signal_log"]
        return record_results(**kwargs)

    runner.result_recorder.record_results = _capture  # type: ignore[method-assign]
    result = runner.run(
        SimulationRunRequest(
            strategy_id=strategy.strategy_id,
            strategy_config=strategy.wrapped_config,
            dataset_version=PARITY_DATASET_VERSION,
            random_seed=42,
            price_basis=PriceBasis.RAW,
            symbols=list(PARITY_SYMBOLS),
            start_date=start,
            end_date=PARITY_DAYS[-1],
            initial_cash=float(PARITY_CAPITAL),
            experiment_id="parity",
            window_role="parity",
            stage_name="parity",
        )
    )
    first_bar = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    signals: set[SignalKey] = set()
    for row in captured["signal_log"].to_dict("records"):
        ts = _utc(row["timestamp"])
        if ts >= first_bar:
            signals.add((ts, str(row["symbol"]), str(row["direction"])))
    fills = [
        (
            _utc(row["timestamp"]),
            str(row["symbol"]),
            str(row["side"]).lower(),
            int(row["quantity"]),
            round(float(row["price"]), 4),
        )
        for row in result.trade_logs.to_dict("records")
    ]
    return signals, sorted(fills)


def platform_signals(session: Session, strategy: ParityStrategy) -> set[SignalKey]:
    """Signals the trading cycle's strategy evaluation produces at every comparison bar."""
    from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
        _instantiate_strategy,
    )
    from autonomous_trading_platform.strategy.contexts.build_strategy_runtime_context import (
        build_strategy_runtime_context,
    )

    built, warmup_bars = _instantiate_strategy(session, strategy.strategy_id)
    context = build_strategy_runtime_context(
        session=session,
        strategy=built,
        dataset_version=PARITY_DATASET_VERSION,
        use_raw_bars=True,
        lookback_bars=warmup_bars,
    )
    signals: set[SignalKey] = set()
    for ts in compare_timestamps():
        result = context.strategy_evaluation_service.evaluate(
            bar_timestamp=ts, run_id=uuid4(), evaluation_timestamp=ts
        )
        for signal in result.signals:
            signals.add((ts, signal.symbol, signal.direction.value))
    return signals


def platform_cycle_fills(session: Session, *, start: date = COMPARE_START) -> list[FillKey]:
    """Run the real trading cycle at every bar from start against the backtest broker."""
    from autonomous_trading_platform.execution.clients.simulated_broker_client import (
        SimulatedBrokerClient,
        build_platform_execution_service,
    )
    from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle

    ticks = compare_timestamps(start)
    broker = SimulatedBrokerClient(
        session=session,
        timestamp=ticks[0],
        simulated_execution_service=build_platform_execution_service(),
        dataset_version_id=PARITY_DATASET_VERSION,
        starting_cash=PARITY_CAPITAL,
    )
    for ts in ticks:
        broker.advance_to(ts)
        run_trading_cycle(
            now_utc=ts, broker_client=broker, dataset_version_id_override=PARITY_DATASET_VERSION
        )
    fills = [
        (
            _utc(datetime.fromisoformat(order["filled_at"])),
            str(order["symbol"]),
            str(order["side"]).lower(),
            int(Decimal(order["filled_qty"])),
            round(float(order["filled_avg_price"]), 4),
        )
        for order in broker._orders.values()
        if order.get("status") == "filled"
    ]
    return sorted(fills)


def describe_mismatch(name: str, research: Any, platform: Any, limit: int = 8) -> str:
    research_set, platform_set = set(research), set(platform)
    only_research = sorted(research_set - platform_set)[:limit]
    only_platform = sorted(platform_set - research_set)[:limit]
    return (
        f"{name}: research {len(research_set)} vs platform {len(platform_set)}; "
        f"only in research (first {limit}): {only_research}; "
        f"only in platform (first {limit}): {only_platform}"
    )


def _utc(value: Any) -> datetime:
    ts: datetime = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
