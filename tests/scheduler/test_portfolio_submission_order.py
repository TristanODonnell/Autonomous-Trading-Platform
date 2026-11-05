"""Finding 19 / 23 helpers: exits go first under the per-bar throttle, and symbols with
an open broker order get no new intent until the order resolves."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.governance.portfolio_membership import (
    MembershipStatus,
)
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.services.portfolio_construction_service import (
    PortfolioConstructionService,
)
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.scheduler.common.trading_cycle_common import StrategyRuntime
from autonomous_trading_platform.scheduler.jobs.portfolio_evaluation import (
    _order_for_submission,
    _split_by_symbols,
)

_RUN = UUID("00000000-0000-0000-0000-00000000dddd")
_NOW = datetime(2026, 11, 4, 14, 35, tzinfo=UTC)
_PRICES = {"AAPL": 100.0, "MSFT": 400.0, "DIA": 520.0, "TLT": 82.0}


class _PassRisk:
    def assert_order_allowed(self, order_intent: object, now: object) -> None:
        return None


@pytest.fixture
def construction() -> PortfolioConstructionService:
    return PortfolioConstructionService(
        pre_trade_risk_service=_PassRisk(),  # type: ignore[arg-type]
        position_sizer=object(),  # type: ignore[arg-type]
    )


def _intent(
    construction: PortfolioConstructionService, strategy_id: str, symbol: str, delta: int
) -> OrderIntent:
    return construction.build_order_intent(
        delta={"symbol": symbol, "delta_qty": delta},
        prices=_PRICES,
        run_id=_RUN,
        strategy_id=strategy_id,
        bar_timestamp=_NOW,
        now=_NOW,
    )


def _runtime(strategy_id: str, status: MembershipStatus) -> StrategyRuntime:
    return StrategyRuntime(
        strategy_id=strategy_id,
        status=status,
        governance_state=GovernanceState.APPROVED_PAPER,
        budget_pct=Decimal("0.35") if status == MembershipStatus.ACTIVE else Decimal("0"),
    )


def _shape(intents: list[OrderIntent]) -> list[tuple[str, str, str]]:
    return [(i.strategy_id, i.symbol, i.side.value.upper()) for i in intents]


def test_exits_go_first_and_orphan_exits_before_member_exits(construction) -> None:
    """The shape of the first live session: two active members' intents come first in
    evaluation order, the orphan sleeve's sells last, and a 2-per-bar throttle never
    reached them."""
    runtimes = [
        _runtime("mean_reversion_v1", MembershipStatus.ACTIVE),
        _runtime("momentum_v1", MembershipStatus.ACTIVE),
        _runtime("__unattributed__", MembershipStatus.WINDING_DOWN),
    ]
    intents = [
        _intent(construction, "mean_reversion_v1", "AAPL", 3),
        _intent(construction, "momentum_v1", "MSFT", 2),
        _intent(construction, "momentum_v1", "AAPL", -1),
        _intent(construction, "momentum_v1", "DIA", 1),
        _intent(construction, "__unattributed__", "DIA", -48),
        _intent(construction, "__unattributed__", "TLT", -306),
    ]

    ordered = _order_for_submission(intents, runtimes)

    assert _shape(ordered) == [
        ("__unattributed__", "DIA", "SELL"),
        ("__unattributed__", "TLT", "SELL"),
        ("momentum_v1", "AAPL", "SELL"),
        ("mean_reversion_v1", "AAPL", "BUY"),
        ("momentum_v1", "MSFT", "BUY"),
        ("momentum_v1", "DIA", "BUY"),
    ]
    # Nothing is dropped or duplicated, only reordered.
    assert sorted(i.intent_id for i in ordered) == sorted(i.intent_id for i in intents)


def test_ordering_is_stable_within_each_group(construction) -> None:
    runtimes = [_runtime("a", MembershipStatus.ACTIVE), _runtime("b", MembershipStatus.ACTIVE)]
    intents = [
        _intent(construction, "a", "AAPL", 1),
        _intent(construction, "b", "MSFT", 1),
        _intent(construction, "a", "DIA", -1),
        _intent(construction, "b", "TLT", -1),
    ]

    ordered = _order_for_submission(intents, runtimes)

    assert _shape(ordered) == [
        ("a", "DIA", "SELL"),
        ("b", "TLT", "SELL"),
        ("a", "AAPL", "BUY"),
        ("b", "MSFT", "BUY"),
    ]


def test_split_by_symbols_holds_back_open_order_symbols(construction) -> None:
    intents = [
        _intent(construction, "a", "AAPL", -2),
        _intent(construction, "b", "AAPL", 2),
        _intent(construction, "a", "MSFT", -1),
        _intent(construction, "b", "MSFT", 1),
    ]

    crossable, held_back = _split_by_symbols(intents, {"aapl"})

    assert _shape(crossable) == [("a", "MSFT", "SELL"), ("b", "MSFT", "BUY")]
    assert _shape(held_back) == [("a", "AAPL", "SELL"), ("b", "AAPL", "BUY")]


def test_split_by_symbols_with_nothing_open_returns_everything(construction) -> None:
    intents = [_intent(construction, "a", "AAPL", -2), _intent(construction, "b", "AAPL", 2)]

    crossable, held_back = _split_by_symbols(intents, set())

    assert crossable == intents
    assert held_back == []
