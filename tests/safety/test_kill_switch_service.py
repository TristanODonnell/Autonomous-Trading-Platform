import pytest

from autonomous_trading_platform.safety.errors import KillSwitchEnabledError
from autonomous_trading_platform.safety.services.kill_switch_service import (
    KillSwitchService,
)


def test_kill_switch_starts_disabled() -> None:
    service = KillSwitchService()

    assert service.is_enabled() is False
    service.assert_not_enabled()

    status = service.get_status()
    assert status["enabled"] is False
    assert status["reason"] is None
    assert status["updated_by"] is None
    assert status["updated_at"] is None


def test_kill_switch_can_be_enabled() -> None:
    service = KillSwitchService()

    service.enable(
        reason="emergency stop",
        updated_by="tester",
    )

    assert service.is_enabled() is True

    with pytest.raises(KillSwitchEnabledError, match="emergency stop"):
        service.assert_not_enabled()

    status = service.get_status()
    assert status["enabled"] is True
    assert status["reason"] == "emergency stop"
    assert status["updated_by"] == "tester"
    assert status["updated_at"] is not None


def test_kill_switch_can_be_disabled() -> None:
    service = KillSwitchService()
    service.enable(reason="emergency stop", updated_by="tester")

    service.disable(
        reason="validated safe",
        updated_by="tester",
    )

    assert service.is_enabled() is False
    service.assert_not_enabled()

    status = service.get_status()
    assert status["enabled"] is False
    assert status["reason"] == "validated safe"
    assert status["updated_by"] == "tester"
    assert status["updated_at"] is not None
