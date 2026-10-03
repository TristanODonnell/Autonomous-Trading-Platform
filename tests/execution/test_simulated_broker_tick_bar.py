"""The backtest broker fills at the bar stamped at the tick, never a later one (step 5c-C, F2).

A decision at tick T is made from bars before T and fills at the close of the bar
stamped T, as research fills do. Before the fix every intraday order, sizing price and
shadow fill used the day's last bar.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.execution.clients.simulated_broker_client import (
    SimulatedBrokerClient,
)
from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.models.slippage_model import (
    SlippageModel,
    SlippageModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelConfig,
    SimulationCostModelService,
)
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from tests.utilities.parity_harness import (
    COMPARE_START,
    PARITY_DATASET_VERSION,
    bar_timestamps,
    write_parity_bars,
)

_SYMBOL = "AAA"
_DAY = COMPARE_START


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    write_parity_bars(root)
    return root


@pytest.fixture()
def closes(data_root: Path) -> dict[datetime, float]:
    files = data_root.glob(f"**/symbol={_SYMBOL}/**/*.parquet")
    rows = [row for f in files for row in pq.read_table(f).to_pylist()]
    return {row["timestamp"]: row["close"] for row in rows}


def _broker(session, root: Path, when: datetime) -> SimulatedBrokerClient:
    execution = SimulatedExecutionService(
        simulation_cost_model_service=SimulationCostModelService(
            config=SimulationCostModelConfig(
                commission_per_share=Decimal("0"), min_commission=Decimal("0")
            ),
            slippage_model=SlippageModel(SlippageModelConfig(slippage_rate=Decimal("0"))),
        ),
        fill_model_config=SimulatedFillModelConfig(),
    )
    execution.reset_for_run(rng=random.Random(1))
    return SimulatedBrokerClient(
        session=session,
        timestamp=when,
        simulated_execution_service=execution,
        base_path=str(root),
        dataset_version_id=PARITY_DATASET_VERSION,
    )


def _close(broker: SimulatedBrokerClient) -> float:
    bar = broker.bar_for(_SYMBOL)
    assert bar is not None
    return float(bar.close)


def _buy(broker: SimulatedBrokerClient) -> dict:
    return broker.submit_order(
        {
            "symbol": _SYMBOL,
            "qty": "10",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "client_order_id": f"c-{broker.orders_submitted_count}",
        }
    )


def test_intraday_ticks_fill_at_their_own_bar_close(db_session, data_root, closes) -> None:
    ticks = bar_timestamps(_DAY)
    broker = _broker(db_session, data_root, ticks[0])
    prices = []
    for tick in ticks[:: len(ticks) // 6]:
        broker.advance_to(tick)
        order = _buy(broker)
        assert order["status"] == "filled"
        assert float(order["filled_avg_price"]) == pytest.approx(closes[tick])
        assert broker.get_latest_trades([_SYMBOL])[_SYMBOL]["p"] == pytest.approx(closes[tick])
        assert _close(broker) == pytest.approx(closes[tick])
        prices.append(float(order["filled_avg_price"]))
    assert len(set(prices)) == len(prices), "fill prices must vary through the day"


def test_tick_between_bars_uses_the_latest_earlier_bar(db_session, data_root, closes) -> None:
    bar = bar_timestamps(_DAY)[10]
    broker = _broker(db_session, data_root, bar + timedelta(minutes=3))
    assert _close(broker) == pytest.approx(closes[bar])


def test_tick_before_the_first_bar_has_no_bar_and_leaves_the_order_open(
    db_session, data_root
) -> None:
    before_open = bar_timestamps(_DAY)[0] - timedelta(minutes=30)
    broker = _broker(db_session, data_root, before_open)
    assert broker.bar_for(_SYMBOL) is None
    assert _buy(broker)["status"] == "new"


def test_daily_cadence_tick_at_the_close_still_uses_the_last_bar(
    db_session, data_root, closes
) -> None:
    close_tick = datetime(_DAY.year, _DAY.month, _DAY.day, 21, 0, tzinfo=UTC)
    broker = _broker(db_session, data_root, close_tick)
    last_bar = bar_timestamps(_DAY)[-1]
    assert _close(broker) == pytest.approx(closes[last_bar])


def test_each_symbol_is_read_once_per_day(db_session, data_root, monkeypatch) -> None:
    reads: list[tuple[str, object]] = []
    original = HistoricalBarDatasetReader.read_with_pyarrow

    def counting(self, **kwargs):
        reads.append((kwargs["symbol"], kwargs["start_date"]))
        return original(self, **kwargs)

    monkeypatch.setattr(HistoricalBarDatasetReader, "read_with_pyarrow", counting)
    ticks = bar_timestamps(_DAY)
    broker = _broker(db_session, data_root, ticks[0])
    for tick in ticks[:5]:
        broker.advance_to(tick)
        broker.bar_for(_SYMBOL)
    assert reads == [(_SYMBOL, _DAY)]

    next_day = bar_timestamps(_DAY - timedelta(days=1))[0]
    broker.advance_to(next_day)
    broker.bar_for(_SYMBOL)
    assert reads[-1] == (_SYMBOL, next_day.date())


def test_account_equity_marks_positions_at_the_last_close_before_the_tick(
    db_session, data_root, closes, monkeypatch
) -> None:
    """The cycle sizes from broker equity; a broker reports it at current prices, not
    at the last fill (the cash snapshot is only rewritten when something fills)."""
    ticks = bar_timestamps(_DAY)
    db_session.add(
        CashSnapshot(
            snapshot_id=uuid4(),
            run_id=uuid4(),
            timestamp=ticks[0],
            currency="USD",
            cash=Decimal("50000"),
            buying_power=Decimal("50000"),
            reserved_cash=Decimal("0"),
            equity=Decimal("99999"),  # stale: marked at the last fill
            source=OrderSource.BROKER_RECONCILED,
            capital_bucket=Decimal("100000"),
        )
    )
    db_session.flush()
    broker = _broker(db_session, data_root, ticks[0])
    monkeypatch.setattr(
        broker,
        "get_positions",
        lambda: [{"symbol": _SYMBOL, "qty": "100", "current_price": "1"}],
    )

    broker.advance_to(ticks[10])
    expected = Decimal("50000") + 100 * Decimal(str(closes[ticks[9]]))
    assert Decimal(broker.get_account()["equity"]) == expected

    # First tick of the day: the previous session's last close.
    broker.advance_to(ticks[0])
    previous_close = closes[bar_timestamps(_DAY - timedelta(days=1))[-1]]
    expected = Decimal("50000") + 100 * Decimal(str(previous_close))
    assert Decimal(broker.get_account()["equity"]) == expected
