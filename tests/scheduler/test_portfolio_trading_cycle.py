"""Trading cycle in portfolio mode: several active strategies, each on its own sleeve."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    StrategySleeveLedgerRow,
    StrategySleeveSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.portfolio_membership_repository import (
    PortfolioMembershipRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)

# Both strategies are seeded approved-for-paper by the paper trading cycle fixture.
_STRATEGIES = {"baseline_strategy", "stub_strategy_v1"}


@pytest.fixture(autouse=True)
def _order_limits_for_several_strategies(monkeypatch) -> None:
    # Account-wide throttle limits must allow one order per strategy per bar; don't
    # depend on whatever the local .env happens to set.
    monkeypatch.setenv("MAX_ORDERS_PER_BAR", "10")
    monkeypatch.setenv("MAX_ORDERS_PER_HOUR", "100")


def _enable_portfolio_mode(db_session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {
            "portfolio_mode_enabled": True,
            "min_active_strategies": 1,
            "max_active_strategies": 6,
            "per_strategy_cap": 1.0,
            "max_total_strategy_allocation_pct": 0.9,
        },
        updated_by="test",
    )


def _latest_manifest(db_session) -> RunManifestRow:
    db_session.expire_all()
    manifest: RunManifestRow | None = (
        db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()
    )
    assert manifest is not None
    return manifest


def _intent_strategies(db_session, run_id) -> set[str]:
    rows = db_session.query(OrderIntents).filter(OrderIntents.run_id == run_id).all()
    return {row.strategy_id for row in rows}


def test_every_active_strategy_trades_its_own_sleeve(
    seeded_paper_trading_cycle_fixture, db_session
) -> None:
    fixture = seeded_paper_trading_cycle_fixture
    _enable_portfolio_mode(db_session)

    run_trading_cycle(now_utc=fixture.now_utc)

    manifest = _latest_manifest(db_session)
    assert manifest.strategy_id == "portfolio"
    assert manifest.status == "completed"
    assert _intent_strategies(db_session, manifest.run_id) == _STRATEGIES

    members = PortfolioMembershipRepository(db_session).get_all()
    assert {(m.strategy_id, m.status) for m in members} == {(sid, "active") for sid in _STRATEGIES}

    # Reconciled fills are attributed to the strategy that owns each order.
    ledger_owners = {row.strategy_id for row in db_session.query(StrategySleeveLedgerRow).all()}
    assert ledger_owners == _STRATEGIES

    snapshots = db_session.query(StrategySleeveSnapshotRow).all()
    assert {row.strategy_id for row in snapshots} == _STRATEGIES
    for row in snapshots:
        assert row.allocated_capital is not None and row.allocated_capital > 0


def test_disabled_strategy_is_skipped_without_blocking_the_portfolio(
    seeded_paper_trading_cycle_fixture, db_session
) -> None:
    fixture = seeded_paper_trading_cycle_fixture
    _enable_portfolio_mode(db_session)
    StrategyControlStateRepository(db_session).set_enabled(
        strategy_id="stub_strategy_v1",
        enabled=False,
        reason="operator paused",
        updated_by="test",
        updated_at=fixture.now_utc,
    )
    db_session.flush()

    run_trading_cycle(now_utc=fixture.now_utc)

    manifest = _latest_manifest(db_session)
    assert manifest.status == "completed"
    assert manifest.error_message is None
    assert _intent_strategies(db_session, manifest.run_id) == {"baseline_strategy"}


def test_portfolio_mode_off_keeps_the_single_strategy_cycle(
    seeded_paper_trading_cycle_fixture, db_session
) -> None:
    fixture = seeded_paper_trading_cycle_fixture

    run_trading_cycle(now_utc=fixture.now_utc)

    manifest = _latest_manifest(db_session)
    assert manifest.strategy_id == "baseline_strategy"
    assert PortfolioMembershipRepository(db_session).get_all() == []
    assert db_session.query(StrategySleeveLedgerRow).count() == 0
