"""Trading cycle with an on-deck tier: shadow-traded, never touches the broker."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.scheduler.jobs import on_deck_shadow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    ShadowSleeveLedgerRow,
    ShadowSleevePositionRow,
    ShadowSleeveSnapshotRow,
    StrategySleeveLedgerRow,
    StrategySleeveSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)

# Both seeded approved-for-paper by the fixture; with one active seat, the
# higher-ranked takes it and the other goes on-deck.
_ACTIVE = "baseline_strategy"
_ON_DECK = "stub_strategy_v1"


@pytest.fixture(autouse=True)
def _order_limits(monkeypatch) -> None:
    monkeypatch.setenv("MAX_ORDERS_PER_BAR", "10")
    monkeypatch.setenv("MAX_ORDERS_PER_HOUR", "100")


def _settings(db_session, **extra: object) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {
            "portfolio_mode_enabled": True,
            "min_active_strategies": 1,
            "max_active_strategies": 1,
            "max_on_deck_strategies": 5,
            "per_strategy_cap": 1.0,
            "max_total_strategy_allocation_pct": 0.9,
            **extra,
        },
        updated_by="test",
    )


def _set_governance(db_session, strategy_id: str, state: str) -> None:
    for row in db_session.query(StrategyGovernance).filter_by(strategy_id=strategy_id).all():
        row.current_state = state
    db_session.flush()


def _latest_portfolio_manifest(db_session) -> RunManifestRow:
    db_session.expire_all()
    manifest: RunManifestRow | None = (
        db_session.query(RunManifestRow)
        .filter(RunManifestRow.strategy_id == "portfolio")
        .order_by(RunManifestRow.created_at.desc())
        .first()
    )
    assert manifest is not None
    return manifest


def _tiers(db_session) -> dict[str, str]:
    return {m.strategy_id: m.status for m in PortfolioMembershipRepository(db_session).get_all()}


def _shadow_snapshot(db_session, strategy_id: str) -> ShadowSleeveSnapshotRow:
    row: ShadowSleeveSnapshotRow | None = (
        db_session.query(ShadowSleeveSnapshotRow)
        .filter_by(strategy_id=strategy_id)
        .order_by(ShadowSleeveSnapshotRow.timestamp.desc())
        .first()
    )
    assert row is not None
    return row


def test_on_deck_strategy_is_shadow_traded_without_broker_orders(
    seeded_paper_trading_cycle_fixture, db_session
) -> None:
    _settings(db_session)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    manifest = _latest_portfolio_manifest(db_session)
    assert manifest.status == "completed"
    assert _tiers(db_session) == {_ACTIVE: "active", _ON_DECK: "on_deck"}

    # Only the active strategy produced order intents and broker orders.
    intents = db_session.query(OrderIntents).all()
    assert {row.strategy_id for row in intents} == {_ACTIVE}
    assert db_session.query(BrokerOrder).count() == len(intents)

    # The on-deck strategy traded only in the shadow book.
    shadow = db_session.query(ShadowSleeveLedgerRow).all()
    assert {row.strategy_id for row in shadow} == {_ON_DECK}
    assert {row.source for row in shadow} == {"shadow_fill"}
    real_owners = {row.strategy_id for row in db_session.query(StrategySleeveLedgerRow).all()}
    assert _ON_DECK not in real_owners
    real_snapshots = {row.strategy_id for row in db_session.query(StrategySleeveSnapshotRow).all()}
    assert real_snapshots == {_ACTIVE}

    # Notional budget = the equal-weight active share (0.9 / 1 seat) of total capital.
    snap = _shadow_snapshot(db_session, _ON_DECK)
    real_snap = db_session.query(StrategySleeveSnapshotRow).filter_by(strategy_id=_ACTIVE).one()
    assert snap.allocated_capital == real_snap.allocated_capital
    assert snap.position_count == 1
    assert snap.blocked_order_count == 0
    # Filled with slippage against the cycle price, so it starts slightly negative.
    assert snap.net_pnl < 0


def test_risk_breach_blocks_the_shadow_order_and_is_counted(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    # Every order breaches the daily notional limit (enforced only by the pre-trade
    # check, not by sizing): dropped for real and shadow alike.
    monkeypatch.setenv("MAX_DAILY_NOTIONAL_TRADED", "1")
    _settings(db_session)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    assert _latest_portfolio_manifest(db_session).status == "completed"
    assert db_session.query(ShadowSleeveLedgerRow).count() == 0
    assert _shadow_snapshot(db_session, _ON_DECK).blocked_order_count == 1


def test_throttle_blocks_shadow_orders_and_is_counted(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    # No active strategy (so no real orders); both shadow-traded with a zero throttle.
    monkeypatch.setenv("MAX_ORDERS_PER_BAR", "0")
    _set_governance(db_session, _ACTIVE, "candidate")
    _set_governance(db_session, _ON_DECK, "candidate")
    _settings(db_session)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    assert _tiers(db_session) == {_ACTIVE: "on_deck", _ON_DECK: "on_deck"}
    assert db_session.query(OrderIntents).count() == 0
    assert db_session.query(ShadowSleeveLedgerRow).count() == 0
    for strategy_id in (_ACTIVE, _ON_DECK):
        assert _shadow_snapshot(db_session, strategy_id).blocked_order_count == 1


def test_shadow_sleeve_is_closed_when_the_strategy_leaves_on_deck(
    seeded_paper_trading_cycle_fixture, db_session
) -> None:
    now = seeded_paper_trading_cycle_fixture.now_utc
    _settings(db_session)
    run_trading_cycle(now_utc=now)
    assert db_session.query(ShadowSleevePositionRow).filter_by(strategy_id=_ON_DECK).count() == 1

    _settings(db_session, max_on_deck_strategies=0)
    run_trading_cycle(now_utc=now + timedelta(minutes=5))

    db_session.expire_all()
    assert _tiers(db_session)[_ON_DECK] == "inactive"
    assert db_session.query(ShadowSleevePositionRow).count() == 0
    exits = db_session.query(ShadowSleeveLedgerRow).filter_by(source="tier_exit").all()
    assert [(row.strategy_id, row.side) for row in exits] == [(_ON_DECK, "sell")]
    history = PortfolioMembershipRepository(db_session).get_transitions(_ON_DECK)
    assert history[-1].reason == "on_deck_over_max"


def test_shadow_failure_never_affects_the_real_cycle(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    def explode(**_: object) -> None:
        raise RuntimeError("shadow boom")

    monkeypatch.setattr(on_deck_shadow, "run_on_deck_shadow", explode)
    _settings(db_session)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    manifest = _latest_portfolio_manifest(db_session)
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert {row.strategy_id for row in db_session.query(OrderIntents).all()} == {_ACTIVE}
    assert db_session.query(ShadowSleeveLedgerRow).count() == 0


def test_disabling_on_deck_keeps_the_step_one_cycle(
    seeded_paper_trading_cycle_fixture, db_session
) -> None:
    _settings(db_session, max_on_deck_strategies=0)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    assert _tiers(db_session) == {_ACTIVE: "active"}
    assert db_session.query(ShadowSleeveSnapshotRow).count() == 0
    assert db_session.query(ShadowSleeveLedgerRow).count() == 0
    assert Decimal(db_session.query(OrderIntents).count()) > 0
