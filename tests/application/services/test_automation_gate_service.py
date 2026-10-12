from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.automation_gate_service import (
    KILL_SWITCH_REASON,
    Automation,
    AutomationGateService,
)
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


def _enable_kill_switch(session: Session) -> None:
    KillSwitchStateRepository(session).enable(
        reason="test", updated_by="ops", updated_at=datetime.now(UTC)
    )


def test_defaults_block_every_automation_except_demotion(db_session: Session) -> None:
    gate = AutomationGateService(db_session)

    status = gate.status()

    assert status["kill_switch_enabled"] is False
    allowed = {name: a["allowed"] for name, a in status["automations"].items()}
    assert allowed == {
        "promotion": False,
        "demotion": True,  # auto_demote_on_breach defaults to True (protective)
        "rebalance": False,
        "review_swaps": False,
        "worker_results": False,
    }
    assert status["automations"]["promotion"]["reason"] == "auto_promote_enabled_off"
    assert status["automations"]["review_swaps"]["reason"] == "portfolio_review_mode_off"
    assert status["automations"]["worker_results"]["switch"] == "worker_results_enabled"


def test_switch_on_allows_and_kill_switch_overrides(db_session: Session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {
            "auto_promote_enabled": True,
            "auto_rebalance_enabled": True,
            "portfolio_review_mode": "auto",
            "worker_results_enabled": True,
        },
        updated_by="test",
    )
    gate = AutomationGateService(db_session)
    assert all(gate.check(a).allowed for a in Automation)
    assert gate.blocked_by_kill_switch(Automation.REBALANCE) is False

    _enable_kill_switch(db_session)

    for automation in Automation:
        decision = gate.check(automation)
        assert decision.allowed is False
        assert decision.reason == KILL_SWITCH_REASON
        assert decision.kill_switch_enabled is True
        assert decision.switch_enabled is True  # the switch itself is still on
        assert gate.blocked_by_kill_switch(automation) is True
    assert gate.status()["kill_switch_enabled"] is True


def test_advisory_review_mode_is_not_the_swap_switch(db_session: Session) -> None:
    OperatorSettingsRepository(db_session).update_current(
        {"portfolio_review_mode": "advisory"}, updated_by="test"
    )

    decision = AutomationGateService(db_session).check(Automation.REVIEW_SWAPS)

    assert decision.allowed is False
    assert decision.reason == "portfolio_review_mode_off"
    assert decision.switch_enabled is False


def test_decision_as_dict_is_dashboard_friendly(db_session: Session) -> None:
    decision = AutomationGateService(db_session).check(Automation.WORKER_RESULTS)

    assert decision.as_dict() == {
        "automation": "worker_results",
        "allowed": False,
        "reason": "worker_results_enabled_off",
        "kill_switch_enabled": False,
        "switch": "worker_results_enabled",
        "switch_enabled": False,
    }
