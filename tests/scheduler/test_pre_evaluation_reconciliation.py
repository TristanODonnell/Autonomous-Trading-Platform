"""Finding 20: in portfolio mode the trading cycle applies the previous bar's fills
(order reconciliation) before the sleeves are evaluated and crossed."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.scheduler.cycles import run_trading_cycle as cycle_module
from autonomous_trading_platform.scheduler.cycles.run_trading_cycle import run_trading_cycle
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


@pytest.fixture(autouse=True)
def _order_limits(monkeypatch) -> None:
    monkeypatch.setenv("MAX_ORDERS_PER_BAR", "10")
    monkeypatch.setenv("MAX_ORDERS_PER_HOUR", "100")


def _portfolio_mode(db_session, enabled: bool) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {
            "portfolio_mode_enabled": enabled,
            "min_active_strategies": 1,
            "max_active_strategies": 2,
            "max_on_deck_strategies": 0,
            "per_strategy_cap": 1.0,
            "max_total_strategy_allocation_pct": 0.9,
        },
        updated_by="test",
    )


def _record_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real_reconciliation = cycle_module.run_order_reconciliation_job
    real_evaluation = cycle_module.run_trading_evaluation_job

    def reconciliation(*args, **kwargs):
        calls.append("reconciliation")
        return real_reconciliation(*args, **kwargs)

    def evaluation(*args, **kwargs):
        calls.append("evaluation")
        return real_evaluation(*args, **kwargs)

    monkeypatch.setattr(cycle_module, "run_order_reconciliation_job", reconciliation)
    monkeypatch.setattr(cycle_module, "run_trading_evaluation_job", evaluation)
    return calls


def _latest_manifest(db_session) -> RunManifestRow:
    db_session.expire_all()
    manifest: RunManifestRow | None = (
        db_session.query(RunManifestRow).order_by(RunManifestRow.created_at.desc()).first()
    )
    assert manifest is not None
    return manifest


def test_portfolio_mode_reconciles_before_evaluating(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    _portfolio_mode(db_session, enabled=True)
    calls = _record_calls(monkeypatch)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    assert calls[:2] == ["reconciliation", "evaluation"]
    # The post-submission reconciliation still runs, so fills land in the same tick too.
    assert calls.count("reconciliation") == 2
    assert _latest_manifest(db_session).status == "completed"


def test_legacy_single_strategy_cycle_is_unchanged(
    seeded_paper_trading_cycle_fixture, db_session, monkeypatch
) -> None:
    _portfolio_mode(db_session, enabled=False)
    calls = _record_calls(monkeypatch)

    run_trading_cycle(now_utc=seeded_paper_trading_cycle_fixture.now_utc)

    assert calls == ["evaluation", "reconciliation"]
    assert _latest_manifest(db_session).status == "completed"
