import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.safety.errors import KillSwitchEnabledError
from autonomous_trading_platform.safety.services.kill_switch_service import (
    KillSwitchService,
)
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)


def _make_service(db_session: Session) -> KillSwitchService:
    return KillSwitchService(repository=KillSwitchStateRepository(session=db_session))


def test_kill_switch_starts_disabled(db_session: Session) -> None:
    service = _make_service(db_session)

    assert service.is_enabled() is False
    service.assert_not_enabled()

    status = service.get_status()
    assert status["enabled"] is False
    assert status["reason"] is None
    assert status["updated_by"] is None
    assert status["updated_at"] is None


def test_kill_switch_can_be_enabled(db_session: Session) -> None:
    service = _make_service(db_session)

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


def test_kill_switch_can_be_disabled(db_session: Session) -> None:
    service = _make_service(db_session)
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
