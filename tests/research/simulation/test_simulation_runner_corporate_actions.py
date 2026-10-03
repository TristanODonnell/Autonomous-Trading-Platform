"""SimulationRunner loads the run's corporate actions and forwards them (plan 5d-E):
splits inside the window go to the engine, every loaded split adjusts the strategy's
history, cash dividends become dividend events, and settlement / policy are forwarded."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from autonomous_trading_platform.accounting.corporate_actions import StaticSplitSource
from autonomous_trading_platform.contracts.common.enums import CorporateActionType, PriceBasis
from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
)
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.contracts.simulation.dividend_event import DividendEvent
from autonomous_trading_platform.research.simulation.simulation_runner import (
    CORPORATE_ACTION_HISTORY_DAYS,
    SimulationRunner,
    SimulationRunRequest,
)

_METRICS = [
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_return_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_risk_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_trade_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_stability_metrics",
]


@pytest.fixture(autouse=True)
def _patch_metrics():
    fakes = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    fakes[0].total_return = 0.0
    fakes[1].sharpe_ratio = 0.0
    fakes[1].max_drawdown = 0.0
    fakes[1].volatility = 0.0
    fakes[2].total_trades = 0
    fakes[2].win_rate = 0.0
    fakes[3].consistency_score = 0.0
    with (
        patch(_METRICS[0], return_value=fakes[0]),
        patch(_METRICS[1], return_value=fakes[1]),
        patch(_METRICS[2], return_value=fakes[2]),
        patch(_METRICS[3], return_value=fakes[3]),
    ):
        yield


def _action(action_id: str, action_type: CorporateActionType, ex_date: date, **kw: Any):
    return CorporateAction(
        action_id=action_id,
        symbol="NVDA",
        action_type=action_type,
        effective_date=ex_date,
        split_ratio=kw.get("split_ratio"),
        cash_amount=kw.get("cash_amount"),
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
        payable_date=kw.get("payable_date"),
    )


IN_WINDOW_SPLIT = _action(
    "s-in", CorporateActionType.SPLIT_FORWARD, date(2024, 6, 10), split_ratio=Decimal("10")
)
PRE_WINDOW_SPLIT = _action(
    "s-pre", CorporateActionType.SPLIT_FORWARD, date(2024, 1, 15), split_ratio=Decimal("4")
)
DIVIDEND = _action(
    "d-in",
    CorporateActionType.CASH_DIVIDEND,
    date(2024, 6, 5),
    cash_amount=Decimal("0.01"),
    payable_date=date(2024, 6, 28),
)


class _Source:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions
        self.calls: list[dict[str, Any]] = []

    def actions_for(self, *, symbols, start_date, end_date):
        self.calls.append({"symbols": list(symbols), "start": start_date, "end": end_date})
        return list(self.actions)


def _execution_result() -> MagicMock:
    result = MagicMock()
    for name in ("trade_logs", "equity_curve", "per_bar_metrics", "positions", "signal_log"):
        setattr(result, name, pd.DataFrame())
    return result


def _runner(source: _Source | None) -> tuple[SimulationRunner, MagicMock, MagicMock]:
    resolved = MagicMock()
    resolved.metadata = {}
    resolved.dataset = MagicMock()
    window = MagicMock()
    window.warmup_timestamps = set()
    window.symbols = ["NVDA"]
    loader = MagicMock()
    loader.load_window.return_value = window
    run_repo = MagicMock()
    run_repo.get_by_run_id.return_value = MagicMock(execution_config={}, metrics_snapshot_id=None)
    metrics_repo = MagicMock()
    metrics_repo.to_row.return_value = MagicMock()
    engine = MagicMock()
    engine.execute.return_value = _execution_result()
    context_builder = MagicMock()
    runner = SimulationRunner(
        dataset_resolver=MagicMock(**{"resolve_bars_dataset.return_value": resolved}),
        window_loader=loader,
        result_recorder=MagicMock(),
        execution_engine=engine,
        context_builder=context_builder,
        simulated_execution_service=MagicMock(),
        simulation_run_repository=run_repo,
        strategy_config_repository=MagicMock(),
        experiment_repository=None,
        metrics_summary_repository=metrics_repo,
        manifest_service=None,
        strategy_factory=MagicMock(**{"build.return_value": MagicMock()}),
        feature_dependency_resolver=None,
        corporate_action_source=source,
    )
    return runner, engine, context_builder


def _request(**overrides: Any) -> SimulationRunRequest:
    fields: dict[str, Any] = dict(
        strategy_id="s",
        strategy_config={"type": "stub", "parameters": {"price_change_threshold": 0.0}},
        dataset_version="raw_bars_v",
        random_seed=1,
        price_basis=PriceBasis.RAW,
        symbols=["NVDA"],
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 14),
    )
    fields.update(overrides)
    return SimulationRunRequest(**fields)


def test_actions_are_loaded_for_the_window_with_history_and_forwarded() -> None:
    source = _Source([PRE_WINDOW_SPLIT, DIVIDEND, IN_WINDOW_SPLIT])
    runner, engine, context_builder = _runner(source)
    policy = ExecutionPolicyConfig()

    runner.run(_request(settlement_days=1, execution_policy_config=policy))

    assert source.calls == [
        {
            "symbols": ["NVDA"],
            "start": date(2024, 6, 3) - timedelta(days=CORPORATE_ACTION_HISTORY_DAYS),
            "end": date(2024, 6, 14),
        }
    ]
    kwargs = engine.execute.call_args.kwargs
    # only the in-window split is applied to positions
    assert kwargs["corporate_actions"] == [IN_WINDOW_SPLIT]
    assert kwargs["dividend_events"] == [
        DividendEvent(
            symbol="NVDA",
            ex_date=date(2024, 6, 5),
            cash_amount_per_share=Decimal("0.01"),
            payable_date=date(2024, 6, 28),
        )
    ]
    assert kwargs["settlement_days"] == 1
    assert kwargs["execution_policy_config"] is policy
    # every loaded split adjusts the history the strategy reads (warmup included)
    with_split_source = cast(
        MagicMock, context_builder.with_lookback.return_value
    ).with_split_source
    split_source = with_split_source.call_args.args[0]
    assert isinstance(split_source, StaticSplitSource)
    assert split_source.splits_for("NVDA", date(2024, 1, 1), date(2024, 6, 30)) == [
        PRE_WINDOW_SPLIT,
        IN_WINDOW_SPLIT,
    ]
    assert kwargs["context_builder"] is with_split_source.return_value


def test_an_explicit_empty_list_bypasses_the_source() -> None:
    source = _Source([IN_WINDOW_SPLIT])
    runner, engine, context_builder = _runner(source)

    runner.run(_request(corporate_actions=[]))

    assert source.calls == []
    kwargs = engine.execute.call_args.kwargs
    assert kwargs["corporate_actions"] == []
    assert kwargs["dividend_events"] == []
    cast(
        MagicMock, context_builder.with_lookback.return_value
    ).with_split_source.assert_not_called()


def test_explicit_dividend_events_win_over_the_loaded_dividends() -> None:
    runner, engine, _ = _runner(_Source([DIVIDEND]))
    explicit = [
        DividendEvent(symbol="NVDA", ex_date=date(2024, 6, 7), cash_amount_per_share=Decimal("1"))
    ]

    runner.run(_request(dividend_events=explicit))

    assert engine.execute.call_args.kwargs["dividend_events"] == explicit


def test_without_a_source_nothing_is_applied() -> None:
    runner, engine, _ = _runner(None)

    runner.run(_request())

    kwargs = engine.execute.call_args.kwargs
    assert kwargs["corporate_actions"] == []
    assert kwargs["dividend_events"] == []
    assert kwargs["settlement_days"] == 0
    assert kwargs["execution_policy_config"] is None
