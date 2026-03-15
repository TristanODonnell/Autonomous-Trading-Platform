from datetime import UTC, datetime, timedelta

import pytest

from autonomous_trading_platform.safety.errors import RuntimeGateNotArmedError
from autonomous_trading_platform.safety.services.runtime_gate_service import (
    RuntimeGateService,
)


def test_runtime_gate_starts_disarmed() -> None:
    service = RuntimeGateService()

    assert service.is_armed() is False

    with pytest.raises(RuntimeGateNotArmedError):
        service.assert_runtime_gate_open()


def test_runtime_gate_can_be_armed() -> None:
    service = RuntimeGateService()

    service.arm(
        reason="supervised session",
        armed_by="tester",
    )

    assert service.is_armed() is True
    service.assert_runtime_gate_open()

    status = service.get_status()
    assert status["armed"] is True
    assert status["armed_by"] == "tester"
    assert status["reason"] == "supervised session"
    assert status["armed_at"] is not None
    assert status["expires_at"] is None


def test_runtime_gate_can_be_disarmed() -> None:
    service = RuntimeGateService()
    service.arm(reason="supervised session", armed_by="tester")

    service.disarm()

    assert service.is_armed() is False

    status = service.get_status()
    assert status["armed"] is False
    assert status["armed_by"] is None
    assert status["reason"] is None
    assert status["armed_at"] is None
    assert status["expires_at"] is None


def test_runtime_gate_rejects_past_expiration() -> None:
    service = RuntimeGateService()
    expires_at = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(ValueError, match="expires_at must be in the future"):
        service.arm(
            reason="bad expiry",
            armed_by="tester",
            expires_at=expires_at,
        )


def test_runtime_gate_expires_automatically() -> None:
    service = RuntimeGateService()
    expires_at = datetime.now(UTC) + timedelta(milliseconds=1)

    service.arm(
        reason="short lived session",
        armed_by="tester",
        expires_at=expires_at,
    )

    # Force the clock past expiry by checking after a tiny delay would be ideal,
    # but for simple deterministic coverage we can directly assert once the
    # deadline has passed naturally in test runtime.
    while datetime.now(UTC) < expires_at:
        pass

    assert service.is_armed() is False

    with pytest.raises(RuntimeGateNotArmedError):
        service.assert_runtime_gate_open()

    status = service.get_status()
    assert status["armed"] is False
